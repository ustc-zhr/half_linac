"""Offline tests for new camera statistics, approval boundaries and recovery."""
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import patch

os.environ.setdefault("MPLCONFIGDIR", "/tmp/emit-infobax-test-mpl")
from repo_bootstrap import ensure_repo_import_path
ensure_repo_import_path(__file__)
import numpy as np
from half_linac.src.optimization.infobax import InfoBAXCore, InfoBAXConfig, PointObservation, CheckpointError
from half_linac.src.apps.emit_measure.infobax_camera import CameraSettings, CameraPointAcquirer
from half_linac.src.apps.emit_measure.infobax_session import PointMeasurement
from half_linac.src.apps.emit_measure.infobax_machine import FakeInfoBAXMachine, HalfRealInfoBAXMachine, camera_contract
from half_linac.src.apps.emit_measure.infobax_workflow import WorkflowConfig, InfoBAXWorkflow, current_candidates, WorkflowInterrupted
from half_linac.src.apps.emit_measure.infobax_launcher import build_workflow
from half_linac.src.apps.emit_measure.control import emittance_lock
from half_linac.src.shared.machine_profile import load_app_context
from half_linac.src.shared.machine_profile.control_lock import ControlLockError


def config(**kwargs):
    scans = tuple(np.linspace(-1, 1, 5))
    return WorkflowConfig(-1, 1, scans, tuple((1., v) for v in scans), 225, **kwargs)


NUMERICAL = InfoBAXConfig(outer_fantasies=4, inner_samples=32, batch_count=2,
                         direct_prior_samples=64, summary_samples=64)


class CameraSource:
    def __init__(self):
        self.time = 0.
        self.static = False
        self.exposure = .1
        self.no_signal = False
        self.fixed_width = False
        self.reset = False
        self.unstable = False

    def clock(self):
        return self.time

    def sleep(self, seconds):
        self.time += seconds

    def get(self, pv, **kwargs):
        frame_id = 0 if self.static else int(self.time / .04)
        if pv == "exposure":
            return self.exposure
        if pv == "id":
            return 0 if self.reset and self.time > .2 else frame_id
        if pv == "image":
            if self.unstable: self.time += .04
            if self.no_signal: return np.zeros((64, 64))
            a = np.arange(64) + .5 - 32
            x, y = np.meshgrid(a, a)
            width = 4. if self.fixed_width else 4. + (frame_id % 7)*.1
            return 1000*np.exp(-(x*x+y*y)/(2*width**2))
        raise AssertionError(pv)

    def camera(self, **kwargs):
        return CameraPointAcquirer(CameraSettings((64,64), .1, timeout=1., **kwargs),
            get=self.get, image_pv="image", frame_id_pv="id", exposure_pv="exposure",
            identity_verified=True, clock=self.clock, sleeper=self.sleep)


class CameraTests(unittest.TestCase):
    def test_independent_frames_pixel_centres_sem_and_archive(self):
        source = CameraSource()
        camera = source.camera()
        camera.arm()
        with TemporaryDirectory() as directory:
            camera.archive_dir = Path(directory)
            measurement = camera.measure(7)
            frames = measurement.metadata["frames"]
            self.assertEqual(len(frames),5)
            self.assertEqual(len({f["frame_id"] for f in frames}),5)
            self.assertGreaterEqual(frames[0]["frame_id"],2)
            self.assertEqual(len(list((Path(directory)/"frames").glob("*.npz"))),5)
            for f in frames:
                with np.load(f["raw_path"]) as saved:
                    raw = saved["raw"]
                    a = np.arange(64)*.1 + .05
                    projection = raw.sum(axis=0)
                    centre = (a*projection).sum()/projection.sum()
                    expected = ((a-centre)**2*projection).sum()/projection.sum()
                self.assertAlmostEqual(f["rms_variances_mm2"]["x"], expected, places=13)
                self.assertIsNotNone(f["gaussian_variances_mm2"]["x"])
            values = measurement.metadata["variance_samples_mm2"]
            self.assertAlmostEqual(measurement.noise_std_mm2,np.std(values,ddof=1)/np.sqrt(5))
            self.assertEqual(camera.statistics["discarded_frames"],1)

    def test_cached_frames_timeout(self):
        source=CameraSource(); source.static=True
        camera=source.camera(); camera.arm()
        with self.assertRaises(TimeoutError): camera.measure(0)

    def test_changing_id_during_read_is_never_accepted(self):
        source=CameraSource(); source.unstable=True
        camera=source.camera(); camera.arm()
        with self.assertRaises(TimeoutError): camera.measure(0)
        self.assertEqual(camera.statistics["accepted_frames"],0)
        self.assertGreater(camera.statistics["unstable_reads"],0)

    def test_counter_reset_and_exposure_change_fail(self):
        for mode in ("reset","exposure"):
            source=CameraSource(); camera=source.camera(); camera.arm()
            if mode=="reset": source.reset=True
            else: source.exposure=.2
            with self.assertRaises(RuntimeError): camera.measure(0)

    def test_invalid_frame_and_zero_uncertainty_fail(self):
        for mode in ("no_signal","fixed_width"):
            source=CameraSource(); setattr(source,mode,True)
            camera=source.camera(); camera.arm()
            with self.assertRaises(ValueError): camera.measure(0)

    def test_roi_saturation_background_and_small_sample_validation(self):
        with self.assertRaises(ValueError): CameraSettings((64,64),.1,samples=2)
        with self.assertRaises(ValueError): CameraSettings((64,64),.1,roi=(0,65,0,64))
        source=CameraSource(); camera=source.camera(saturation_level=100.)
        with self.assertRaisesRegex(ValueError,"Saturated"): camera.analyze(source.get("image"))
        camera=source.camera(roi=(30,34,30,34))
        with self.assertRaises(ValueError): camera.analyze(source.get("image"))
        with self.assertRaises(ValueError):
            CameraPointAcquirer(CameraSettings((64,64),.1),get=source.get,image_pv="image",
                frame_id_pv="id",exposure_pv="exposure",identity_verified=True,background=np.zeros((3,3)))

    def test_missing_identity_is_not_silently_accepted(self):
        with self.assertRaises(ValueError):
            CameraPointAcquirer(CameraSettings((64,64),.1),get=lambda _:None,
                                image_pv="image",frame_id_pv="id",exposure_pv="exposure")
        with self.assertRaises(ValueError):
            camera_contract(load_app_context("emit_measure",machine_id="half",control_backend="real"),"PRF07")


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.directory=TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.cfg=config(active_queries=0)
        self.machine=FakeInfoBAXMachine(self.cfg)

    def workflow(self, **kwargs):
        return InfoBAXWorkflow(self.cfg,self.machine,self.directory.name,
            approve=kwargs.pop("approve",lambda request,check:True),numerical_config=NUMERICAL,**kwargs)

    def test_baseline_initialization_verification_restore_and_explicit_application(self):
        requests=[]
        w=self.workflow(approve=lambda r,c: requests.append(r) or True)
        initial=self.machine.snapshot()
        result=w.run()
        self.assertEqual(result["status"],"completed")
        self.assertEqual(result["restoration_status"],"succeeded")
        self.assertEqual(self.machine.snapshot(),initial)
        self.assertEqual(len(w.baseline),5)
        self.assertEqual(len(w.verification),5)
        self.assertEqual(len(w.core.observations),11)
        self.assertEqual(len({p.query_index for p in w.core.observations}),11)
        self.assertEqual(len([r for r in requests if r["phase"]=="initialization"]),6)
        self.assertTrue(w.confirmed)
        w.manual_move(apply_verified=True)
        self.assertNotEqual(self.machine.current,initial["current_a"])
        self.assertEqual(w.restoration_status,"not_at_initial")
        self.assertEqual(self.machine.k1,initial["scan_k1"])
        w.manual_move()
        self.assertEqual(self.machine.snapshot(),initial)

    def test_explicit_initialization_and_transport_checkpoint_round_trip(self):
        w=self.workflow(); w.run()
        checkpoint=w.core.to_checkpoint()
        self.assertEqual(checkpoint["model"],"transport_moments")
        restored=InfoBAXCore.from_checkpoint(w.core.pool,checkpoint)
        self.assertEqual(restored.initial_design,w.core.initial_design)
        self.assertEqual(restored.recommend(),w.core.recommend())
        checkpoint["model"]="bad-model"
        with self.assertRaises(CheckpointError): InfoBAXCore.from_checkpoint(w.core.pool,checkpoint)

    def test_active_suggestions_stop_when_the_candidate_pool_is_exhausted(self):
        self.cfg=config(active_queries=30)
        self.machine=FakeInfoBAXMachine(self.cfg)
        original=InfoBAXCore.propose
        def predictable(core, **kwargs):
            if all(i in {o.query_index for o in core.observations} for i in core.initial_design):
                return core._suggestion(core.available_indices[0],phase="infobax",status="ready")
            return original(core, **kwargs)
        with patch.object(InfoBAXCore,"propose",predictable):
            w=self.workflow(); result=w.run()
        self.assertEqual(result["status"],"completed")
        self.assertEqual(len([r for r in w.records if r["phase"]=="active"]),14)
        observed = [point.query_index for point in w.core.observations]
        self.assertEqual(len(observed), w.core.pool.query_count)
        self.assertEqual(len(set(observed)), len(observed))
        self.assertEqual(result["cost"]["accepted_frames"],len(w.records)*5)

    def test_endpoint_baseline_reuses_existing_initial_points(self):
        self.machine.current=-1.
        w=self.workflow(); w.run()
        self.assertEqual(len(w.core.initial_design),8)
        self.assertEqual(len(w.core.observations),8)
        self.assertEqual(len([r for r in w.records if r["phase"]=="initialization"]),3)

    def test_no_writes_before_approval_and_pause_wait_restores(self):
        w=None
        def approve(request, check):
            self.assertFalse(any(m[0]!="restore" for m in self.machine.moves))
            self.assertTrue(w.path.exists())
            w.request_pause()
            check()
        w=self.workflow(approve=approve)
        result=w.run()
        self.assertEqual(result["status"],"paused")
        self.assertEqual(result["restoration_status"],"succeeded")
        self.assertEqual(self.machine.moves,[("restore",0.,0.)])

    def test_waiting_keeps_previous_approved_state_and_resume_discards_pending(self):
        w=None
        def approve(request, check):
            if request["phase"]=="initialization":
                self.assertEqual(self.machine.k1,self.cfg.scan_values[-1])
                w.request_pause(); check()
            return True
        w=self.workflow(approve=approve); w.run()
        self.assertEqual(w.status,"paused")
        loaded=InfoBAXWorkflow.load(self.cfg,self.machine,self.directory.name,approve=lambda r,c:True)
        self.assertTrue(any(r["state"]=="interrupted" for r in loaded.records))
        self.assertEqual(loaded.run()["status"],"completed")

    def test_resume_detects_drift_without_writing(self):
        w=None
        def approve(r,c): w.request_pause(); c()
        w=self.workflow(approve=approve); w.run()
        loaded=InfoBAXWorkflow.load(self.cfg,self.machine,self.directory.name,approve=lambda r,c:True)
        self.machine.current=.2
        before=list(self.machine.moves)
        result=loaded.run()
        self.assertEqual(result["status"],"failed")
        self.assertIn("drift",result["error"])
        self.assertEqual(self.machine.moves,before)

    def test_failure_and_restore_failure_disable_application(self):
        with patch.object(self.machine,"measure_point",side_effect=RuntimeError("camera failed")):
            w=self.workflow(); result=w.run()
        self.assertEqual(result["status"],"failed")
        self.assertEqual(result["restoration_status"],"succeeded")
        with self.assertRaises(ValueError): w.manual_move(apply_verified=True)
        w.approve=lambda r,c:True
        with patch.object(self.machine,"restore",side_effect=RuntimeError("supply failed")):
            result=w.run()
        self.assertEqual(result["status"],"restore_failed")
        self.assertFalse(w.confirmed)
        w.manual_move()
        self.assertEqual(w.restoration_status,"succeeded")

    def test_timeout_in_approval_includes_wait_and_restores(self):
        now=[0.]
        def approve(r,check): now[0]=121*60.; check()
        w=self.workflow(approve=approve,clock=lambda:now[0])
        result=w.run()
        self.assertEqual(result["status"],"time_budget_exhausted")
        self.assertEqual(result["restoration_status"],"succeeded")

    def test_stale_checkpoint_writer_and_config_change_are_rejected(self):
        w=self.workflow(); w.run()
        other=InfoBAXWorkflow.load(self.cfg,self.machine,self.directory.name)
        w.manual_move()
        with self.assertRaisesRegex(ValueError,"changed"): other.run()
        with self.assertRaises(ValueError):
            InfoBAXWorkflow.load(config(active_queries=1),self.machine,self.directory.name)

    def test_cooperative_lock_conflict_precedes_reads_and_motion(self):
        with emittance_lock("half","fake"):
            with self.assertRaises(ControlLockError): self.workflow().run()
        self.assertEqual(self.machine.moves,[])
        with emittance_lock("half","vm"):
            self.assertEqual(self.workflow().run()["status"],"completed")

    def test_candidate_and_transport_validation(self):
        self.assertEqual(current_candidates(-1,1,0),(-1.,-.5,0.,.5,1.))
        self.assertIn(.13,current_candidates(-1,1,.13))
        with self.assertRaises(ValueError): current_candidates(-1,1,2)
        self.assertEqual(current_candidates(-1, 1, 0, count=3), (-1., 0., 1.))
        with self.assertRaisesRegex(ValueError, 'two current candidates'):
            config(current_candidate_count=1)
        with self.assertRaisesRegex(ValueError, 'Initial K1 points'):
            config(initial_points_per_end=len(self.cfg.scan_values) + 1)
        with self.assertRaises(ValueError):
            WorkflowConfig(-1,1,(-1.,0.,1.),((1.,0.),)*3,225)

    def test_unclean_checkpoint_requires_recovery_before_resume(self):
        w=None
        def approve(r,c):
            if r["phase"]=="initialization": w.request_pause(); c()
            return True
        w=self.workflow(approve=approve); w.run()
        payload=json.loads(w.path.read_text())
        payload.update(status="awaiting_approval",restoration_status="pending")
        payload["records"][-1]["state"]="pending"
        w.path.write_text(json.dumps(payload))
        restored=InfoBAXWorkflow.load(self.cfg,self.machine,self.directory.name,approve=lambda r,c:True)
        self.assertEqual(restored.status,"recovery_required")
        before=list(self.machine.moves)
        with self.assertRaisesRegex(RuntimeError,"Recover"): restored.run()
        self.assertEqual(before,self.machine.moves)
        restored.manual_move()
        self.assertEqual(restored.status,"paused")
        self.assertEqual(restored.run()["status"],"completed")

    def test_interrupted_manual_apply_requires_recovery(self):
        w=self.workflow(); w.run()
        payload=json.loads(w.path.read_text())
        payload["actions"].append({"state":"pending","apply_verified":True})
        w.path.write_text(json.dumps(payload))
        self.machine.current=.5
        restored=InfoBAXWorkflow.load(self.cfg,self.machine,self.directory.name,approve=lambda r,c:True)
        self.assertEqual(restored.status,"recovery_required")
        self.assertFalse(restored.confirmed)
        with self.assertRaises(RuntimeError): restored.run()
        restored.manual_move()
        self.assertEqual(self.machine.current,0.)
        self.assertEqual(restored.restoration_status,"succeeded")

    def test_cancellation_check_interrupts_acquisition_before_candidate_evaluation(self):
        w=self.workflow(); w.run()
        calls=[]
        def cancelled():
            calls.append(True)
            raise WorkflowInterrupted("paused")
        with patch("half_linac.src.optimization.infobax.acquisition.InfoBAXEstimator.estimate") as estimate:
            with self.assertRaises(WorkflowInterrupted): w.core.propose(check=cancelled)
            estimate.assert_not_called()
        self.assertEqual(calls,[True])

    def test_launcher_safe_default_and_frozen_reload(self):
        with TemporaryDirectory() as directory:
            opts={"current_low":-1.,"current_high":1.,"scan_low":-1.,"scan_high":1.,
                  "scan_points":5,"active_queries":0,"backend":"fake"}
            with patch("epics.caget",side_effect=AssertionError("No live reads")), patch("epics.caput",side_effect=AssertionError("No writes")):
                w=build_workflow(opts,directory,approve=lambda r,c:True,numerical_config=NUMERICAL)
                w.run()
                loaded=build_workflow({"backend":"fake"},directory,resume=True)
                self.assertEqual(loaded.config.fingerprint,w.config.fingerprint)
                self.assertEqual(loaded.run()["status"],"completed")



class RealAdapterTests(unittest.TestCase):
    def setUp(self):
        from types import SimpleNamespace
        from half_linac.src.apps.emit_measure.optimization import OptimizationVariable
        from half_linac.src.shared.machine_profile import resolve_channel
        self.context=load_app_context("emit_measure",machine_id="half",control_backend="real")
        self.cfg=WorkflowConfig(0,2,(-1.,-.5,0.,.5,1.),((1.,-1.),(1.,-.5),(1.,0.),(1.,.5),(1.,1.)),225,active_queries=0)
        self.pvs={name:resolve_channel(self.context,e,ch) for name,e,ch in (
            ("sol_set","SS01","current_set"),("sol_read","SS01","current_readback"),
            ("k1","QL09","K1"),("qset","QL09","current_set"),("qread","QL09","current_readback"))}
        self.values={v:1. if k.startswith("sol") else 0. for k,v in self.pvs.items()}
        self.writes=[]
        def get(pv,**kwargs): return self.values[pv]
        def put(pv,value,**kwargs):
            self.writes.append((pv,value)); self.values[pv]=value
            if pv==self.pvs["sol_set"]: self.values[self.pvs["sol_read"]]=value
            if pv==self.pvs["k1"]:
                self.values[self.pvs["qset"]]=self.values[self.pvs["qread"]]=value
            return 1
        self.get,self.put=get,put
        self.camera=SimpleNamespace(fingerprint="camera-test",background=None,settings=SimpleNamespace(plane="x"),
            exposure=lambda:.1,frozen_exposure=None,arm=lambda:None,
            measure=lambda q: PointMeasurement(.1,.01,{"frame_count":5}))
        with patch("epics.caget",side_effect=AssertionError("Construction must be passive")):
            self.machine=HalfRealInfoBAXMachine(self.context,OptimizationVariable("SS01",0,2),
                self.cfg,self.camera,scan_quad="QL09",get=get,put=put,settle_time=0,motion_timeout=.05)
        self.machine.configure((0.,.5,1.,1.5,2.))

    def test_real_adapter_single_point_and_verified_restore(self):
        snapshot=self.machine.snapshot()
        self.machine.apply_query(0,-1.)
        self.assertEqual(self.machine.measure_point(0,0).variance_mm2,.1)
        self.machine.restore(snapshot)
        self.machine.verify_snapshot(snapshot)
        self.assertEqual(len(self.writes),4)

    def test_restore_attempts_quad_even_when_solenoid_write_fails(self):
        snapshot=self.machine.snapshot()
        self.machine.apply_query(0,-1.)
        original=self.machine.device.put
        self.machine.device.put=lambda pv,value,**kwargs: 0 if pv==self.pvs["sol_set"] else original(pv,value,**kwargs)
        with self.assertRaises(RuntimeError): self.machine.restore(snapshot)
        self.assertEqual(self.values[self.pvs["k1"]],snapshot["scan_k1"])

    def test_unsettled_initial_state_and_invalid_query_do_not_write(self):
        self.values[self.pvs["sol_read"]]=.3
        with self.assertRaises(RuntimeError): self.machine.snapshot()
        with self.assertRaises(ValueError): self.machine.apply_query(0,9.)
        self.assertEqual(self.writes,[])

    def test_measurement_detects_device_drift(self):
        self.machine.snapshot()
        self.machine.apply_query(1,0.)
        self.values[self.pvs["sol_set"]]=self.values[self.pvs["sol_read"]]=.7
        with self.assertRaisesRegex(RuntimeError,"drifted"): self.machine.measure_point(5,0)

    def test_transport_settings_drift_blocks_motion(self):
        self.machine.model_checks={"test-model":.2}
        self.values["test-model"]=.4
        with self.assertRaisesRegex(RuntimeError,"Transport"): self.machine.apply_current(0)
        self.assertEqual(self.writes,[])

if __name__=="__main__": unittest.main()
