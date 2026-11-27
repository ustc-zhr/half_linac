"""InfoBAX integration with the operator application's frozen measurement."""
from __future__ import annotations
from copy import deepcopy
from dataclasses import replace
import json
import math
from pathlib import Path
from types import SimpleNamespace
import numpy as np

from .control import emittance_lock
from .measurement_service import MeasurementSnapshot, PointAcquirer, fingerprint, prepare_measurement
from .infobax_machine import HalfRealInfoBAXMachine
from .infobax_ict1 import ICT1WindowSampler, monitor_settings
from .infobax_workflow import (
    DEFAULT_CURRENT_CANDIDATES, DEFAULT_INITIAL_POINTS_PER_END, FRAMEWORK_WORKFLOW_SCHEMAS,
    InfoBAXWorkflow, WorkflowConfig, current_vector,
)
from .optimization import OptimizationVariable


def grid_for_parameters(paras, initial_k1):
    from .profile_runtime import effective_k1_scan_limit
    if paras.scan_strategy in {"adaptive", "adaptive_quality"}:
        low, high = paras.adaptive_config.k1_min, paras.adaptive_config.k1_max
    else:
        low, high = paras.k1_from, paras.k1_end
    limits = effective_k1_scan_limit(paras.app_context, paras.quad_name, low, high,
                                     paras.k1_mode, paras.k1_unit, initial_k1)
    if type(paras.k1_steps) is not int or paras.k1_steps < 3:
        raise ValueError("InfoBAX needs at least three K1 candidates")
    return tuple(float(v) for v in np.linspace(limits.low, limits.high, paras.k1_steps))


def measurement_contract(paras):
    snapshot = MeasurementSnapshot.from_parameters(paras)
    return {"measurement": snapshot.metadata(), "energy_mev": paras.EnergyMeV,
            "quad": paras.quad_name, "screen": paras.flag_name, "model_line": paras.model_line,
            "model_elements": getattr(paras,"model_elements",None),
            "k1_source": {"mode":paras.k1_mode,"unit":paras.k1_unit,"steps":paras.k1_steps,
                          "bounds": (paras.adaptive_config.k1_min,paras.adaptive_config.k1_max)
                          if paras.scan_strategy in {"adaptive","adaptive_quality"} else (paras.k1_from,paras.k1_end)},
            "model_overrides": {k:v for k,v in paras.model_lattice_overrides.items() if k!=paras.quad_name},
            "channels": {e.id: {"channels":e.channels,"limits":e.limits,"geometry":e.image_geometry}
                         for e in paras.app_context.profile.elements
                         if e.id in (paras.quad_name, paras.flag_name) or e.kind=="solenoid"}}


class _SharedCamera:
    """Compatibility boundary for supply control; sampling is owned by the service."""
    def __init__(self, paras, plane):
        self.measurement = MeasurementSnapshot.from_parameters(paras)
        self.settings = SimpleNamespace(plane=plane)
        self.background = self.measurement.background
        self.fingerprint = self.measurement.fingerprint
        self.frozen_exposure = None
        self.archive_dir = None
        self.statistics = {}
        self.paras, self.machine = paras, None

    def exposure(self):
        pv = getattr(self.paras, "flagExposurePV", None)
        if not pv:
            return 0.
        value = self.machine.device.get(pv, timeout=1, use_monitor=False)
        return 0. if value is None else float(value)

    def arm(self):
        self.frozen_exposure = self.exposure()

    def measure(self, query_index, *, before_images=None):
        machine = self.machine
        k1 = machine._pending_query[1]
        acquirer = PointAcquirer(self.measurement, move=machine.quad.move, check=machine.check,
            get=machine.device.get, progress=machine.frame_progress,
            archive_dir=None if self.archive_dir is None else Path(self.archive_dir)/"frames",
            before_images=before_images)
        result = acquirer.acquire_point(k1)
        self.statistics["accepted_frames"] = self.statistics.get("accepted_frames",0)+len(result.frames)
        return result.observation(machine.config.plane)


class FrameworkInfoBAXMachine(HalfRealInfoBAXMachine):
    def __init__(self, paras, variable, config, *, solenoid_settle=2, get=None, put=None, motion_timeout=10):
        camera = _SharedCamera(paras, config.plane)
        checks = {f["source_pv"]: f["source_value"] for f in
                  (getattr(paras,"model_snapshot_metadata",None) or {}).get("fields",[])
                  if f["element_id"]!=paras.quad_name and f.get("source_pv")}
        super().__init__(paras.app_context,variable,config,camera,scan_quad=paras.quad_name,
                         settle_time=solenoid_settle,model_checks=checks,get=get,put=put,motion_timeout=motion_timeout)
        camera.machine = self
        self.paras, self.frame_progress = paras, None
        settings = config.ict1_settings
        self.ict1_sampler = None if settings is None else ICT1WindowSampler(settings)

    def apply_query(self,candidate,scan_value):
        if scan_value not in self.config.scan_values:
            raise ValueError("K1 is outside the frozen query pool")
        self.apply_current(candidate)
        self._pending_query=(candidate,float(scan_value))

    def measure_point(self,query_index,expected_noise_std_mm2, *, before_images=None):
        self.check()
        result=self.camera.measure(query_index, before_images=before_images)
        self._verify_query()
        self._pending_query=None
        return result

    def measure_ict1(self, window):
        def readback():
            currents = self.device.readback()
            k1 = self.device.read(self.quad_pvs["K1"])
            actual = {"current_a": currents, "scan_k1": k1,
                      "quad_readback_a": self.device.read(self.quad_pvs["current_readback"])}
            return actual

        def verify(actual):
            if (not self._currents_match(actual["current_a"], current_vector(window["target_current_a"]))
                    or not math.isclose(actual["scan_k1"], window["target_k1"], rel_tol=0, abs_tol=1e-6)
                    or not math.isclose(actual["quad_readback_a"], self.device.read(self.quad_pvs["current_set"]),
                                        rel_tol=0, abs_tol=self.tolerance)):
                raise RuntimeError("Device readback drifted during the ICT1 measurement")

        window["readback_start"] = readback()
        verify(window["readback_start"])
        self.ict1_sampler.capture(window, self.check)
        window["readback_end"] = readback()
        verify(window["readback_end"])

    def scan_parameters(self, directory, completed, on_point, lease, *, before_images=None):
        from .optimization import VerifiedQuadRestore
        paras=deepcopy(self.paras)
        paras.restore_quad=VerifiedQuadRestore(paras.app_context,paras.quad_name)
        paras.control_lease=lease
        paras.scan_strategy="grid"
        paras.k1_mode="absolute"
        paras.k1_from,paras.k1_end=self.config.scan_values[0],self.config.scan_values[-1]
        paras.k1_steps=len(self.config.scan_values)
        paras.explicit_k1_values=self.config.scan_values
        paras.previous_point_results=completed
        if len(completed)==len(self.config.scan_values):
            paras.recal=True
            paras.recal_points=[(p["k1"],f["sigx"],f["sigy"]) for p in completed
                                for f in p["frames"] if "sigx" in f and "sigy" in f]
        paras.on_point_result=on_point
        paras.before_point_images=before_images
        paras.measurement_check=self.check
        paras.require_point_quality=True
        paras.skip_parabolic=True
        paras.scan_latest_dir=Path(directory)/"latest"
        paras.scan_archive_dir=Path(directory)/"runs"
        paras.raw_archive_dir=Path(directory)/"frames"
        paras.scan_metadata=deepcopy(self.paras.scan_metadata)
        paras.scan_metadata.update(scan_strategy="grid",k1_values=list(self.config.scan_values),
                                  freshness="timed_unverified",samples=paras.samples)
        return paras


def archived_variables(config, settings):
    names = settings["variables"] if "variables" in settings else (settings["variable"],)
    return tuple(OptimizationVariable(name, low, high) for name, low, high in zip(
        names, current_vector(config.current_low), current_vector(config.current_high), strict=True,
    ))


def build_framework_workflow(paras, variable, run_dir, *, plane, model="transport_moments",
                             active_queries=3,max_minutes=120,solenoid_settle=2,
                             current_candidate_count=DEFAULT_CURRENT_CANDIDATES,
                             initial_points_per_end=DEFAULT_INITIAL_POINTS_PER_END,
                             approve=None,automatic_measurements=False,progress=None,full_scan=None,
                             resume=False,lease=None,ict1_retention=None):
    if paras.samples < 3:
        raise ValueError("InfoBAX requires at least 3 main-window Samples per point")
    variables = (variable,) if isinstance(variable, OptimizationVariable) else tuple(variable)
    if not variables:
        raise ValueError("Select at least one solenoid for InfoBAX")
    if paras.defer_measurement_prepare:
        prepare_measurement(paras)
    contract=measurement_contract(paras)
    ict1_settings = None if ict1_retention is None else monitor_settings(paras, ict1_retention)
    if resume:
        saved=json.loads((Path(run_dir)/InfoBAXWorkflow.filename).read_text())
        if saved.get("schema") not in FRAMEWORK_WORKFLOW_SCHEMAS:
            raise ValueError("Prototype v1 archives are read-only; start a new shared-measurement run")
        config=WorkflowConfig(**saved["config"])
        settings=json.loads(config.settings_json)
        if config.ict1_settings != ict1_settings:
            raise ValueError("ICT1 constraint settings differ from the saved run")
        if settings.get("measurement_contract")!=json.loads(json.dumps(contract)):
            raise ValueError("Main-window measurement/model settings differ from the saved run")
        if (variables != archived_variables(config, settings) or plane!=config.plane
                or model!=config.model or active_queries!=config.active_queries or max_minutes!=config.max_minutes
                or current_candidate_count!=config.current_candidate_count
                or initial_points_per_end!=config.initial_points_per_end):
            raise ValueError("Optimization settings differ from the saved run")
        if solenoid_settle!=settings["solenoid_settle"]:
            raise ValueError("Solenoid settle setting differs from the saved run")
    else:
        from half_linac.src.shared.machine_profile import build_model_backend
        scans=grid_for_parameters(paras,paras.restore_quad.initial_k1)
        backend=build_model_backend(paras.app_context,energy_mev=paras.EnergyMeV,line_name=paras.model_line)
        projections=[]
        offset=0 if plane=="x" else 2
        for k1 in scans:
            matrix=np.asarray(backend.get_map(paras.quad_name,paras.flag_name,k1=k1,
                lattice_overrides=paras.model_lattice_overrides,seq="ent2exit",twiss_only=True))
            projections.append((float(matrix[offset,offset]),float(matrix[offset,offset+1])))
        settings = {"measurement_contract": contract, "solenoid_settle": solenoid_settle}
        if ict1_settings is not None:
            settings["ict1"] = ict1_settings
        if len(variables) == 1:
            settings["variable"] = variables[0].element_id
            low, high = variables[0].low, variables[0].high
        else:
            settings["variables"] = [item.element_id for item in variables]
            low, high = tuple(item.low for item in variables), tuple(item.high for item in variables)
        config=WorkflowConfig(low,high,scans,tuple(projections),paras.EnergyMeV,
            plane,model,active_queries,max_minutes,json.dumps(settings,sort_keys=True,allow_nan=False),
            current_candidate_count=current_candidate_count,initial_points_per_end=initial_points_per_end)
    machine=FrameworkInfoBAXMachine(paras,variables,config,solenoid_settle=solenoid_settle)
    kwargs=dict(approve=approve,automatic_measurements=automatic_measurements,progress=progress,
                full_scan=full_scan,control_lease=lease)
    if resume:
        return InfoBAXWorkflow.load(config,machine,run_dir,**kwargs)
    return InfoBAXWorkflow(config,machine,run_dir,**kwargs)


def load_framework_recovery(context,run_dir):
    """Load a restore-only handle without consulting camera or live model state.

    Measurement identity is retained from the archive, but this handle cannot
    run queries or apply a recommendation. Device mappings/limits must match.
    """
    from half_linac.src.shared.machine_profile import resolve_write_target
    data=json.loads((Path(run_dir)/InfoBAXWorkflow.filename).read_text())
    if data.get("schema") not in FRAMEWORK_WORKFLOW_SCHEMAS:raise ValueError("Prototype archives are read-only")
    config=WorkflowConfig(**data["config"])
    settings=json.loads(config.settings_json)
    contract=settings.get("measurement_contract")
    if not contract:raise ValueError("Only framework archives support device recovery")
    variables = archived_variables(config, settings)
    for name in (*(variable.element_id for variable in variables), contract["quad"]):
        element=context.profile.get_element(name)
        actual={"channels":element.channels,"limits":element.limits,"geometry":element.image_geometry}
        if json.loads(json.dumps(actual))!=contract["channels"][name]:
            raise ValueError(f"Device mapping or limits changed: {name}")
    target=resolve_write_target(context,contract["quad"],quantity="K1",unit="1/m^2")
    if not target.machine_limit.contains(data["initial"]["scan_k1"]):
        raise ValueError("Saved scan K1 is outside the current machine limit")
    camera=SimpleNamespace(settings=SimpleNamespace(plane=config.plane),background=None,
        fingerprint=fingerprint(contract["measurement"]),statistics={},archive_dir=None)
    machine=HalfRealInfoBAXMachine(context, variables,
        config,camera,scan_quad=contract["quad"],settle_time=settings["solenoid_settle"])
    machine.recovery_only=True
    # Only device restoration is authorized by this handle. Measurement identity
    # is checked again by build_framework_workflow before any later resume.
    machine._fingerprint=data["machine_fingerprint"]
    workflow=InfoBAXWorkflow.load(config,machine,run_dir)
    workflow.confirmed=False
    return workflow
