"""Build frozen standalone runs, with a Fake backend as the launch default."""
from dataclasses import asdict
import json
from pathlib import Path

import numpy as np

from half_linac.src.shared.machine_profile import load_app_context, get_emit_preset
from .infobax_camera import CameraPointAcquirer, CameraSettings, fingerprint
from .infobax_machine import FakeInfoBAXMachine, HalfRealInfoBAXMachine, camera_contract
from .infobax_workflow import InfoBAXWorkflow, WorkflowConfig


def profile_fingerprint(context):
    return fingerprint({"machine": asdict(context.profile.machine),
                        "elements": [asdict(e) for e in context.profile.elements],
                        "workflows": context.profile.workflows,
                        "backend": asdict(context.control_backend)})


def build_workflow(options, run_dir, *, resume=False, approve=None, progress=None, numerical_config=None):
    """Called by a worker after Start. Live reads are confined to this call/run."""
    options = dict(options)
    backend = options.get("backend", "fake")
    config = None
    if resume:
        data = json.loads((Path(run_dir)/InfoBAXWorkflow.filename).read_text())
        config = WorkflowConfig(**data["config"])
        settings = json.loads(config.settings_json)
        if backend != settings["launch"]["backend"]:
            raise ValueError("Select the checkpoint's backend before loading")
        options = settings["launch"]
    context = load_app_context("emit_measure", machine_id="half",
                               control_backend="real" if backend == "real" else "vm")
    preset = get_emit_preset(context, options.get("preset"))
    if config is None:
        options = {"backend": backend, "preset": preset.id,
                   "variable": next(e.id for e in context.profile.elements if e.kind == "solenoid"),
                   "plane": "x", "model": "transport_moments", "active_queries": 3,
                   "max_minutes": 120, "energy_mev": preset.energy_mev,
                   "scan_low": preset.scan.k1_from, "scan_high": preset.scan.k1_end,
                   "scan_points": preset.scan.k1_steps, "samples": 5,
                   "settle_time": preset.scan.settle_time or 0,
                   "sample_interval": preset.scan.sample_interval or 0,
                   "roi": None, "background": None, "upstream_confirmed": False, **options}
        energy = float(options.get("energy_mev", preset.energy_mev))
        scans = tuple(float(v) for v in np.linspace(
            options.get("scan_low", preset.scan.k1_from), options.get("scan_high", preset.scan.k1_end),
            int(options.get("scan_points", preset.scan.k1_steps))))
        settings = {"launch": options, "profile_fingerprint": profile_fingerprint(context),
                    "preset": asdict(preset)}
        if backend == "real":
            if not options.get("upstream_confirmed"):
                raise ValueError("Confirm the selected solenoid is upstream of the scan segment")
            from half_linac.src.shared.machine_profile import build_model_backend, build_model_snapshot
            from half_linac.src.shared.machine_profile.pixel_geometry import resolve_element_image_geometry
            camera_contract(context, preset.flag)  # Fail before any live reads if metadata is missing.
            model = build_model_backend(context, energy_mev=energy, line_name=preset.model_line)
            path = model.get_line_elements(preset.quad, preset.flag)
            if not path or path[0]["NAME"] != preset.quad or path[-1]["NAME"] != preset.flag:
                raise ValueError("Screen must be downstream of the scan quadrupole")
            fields = tuple((str(e["NAME"]), "K1") for e in path
                           if str(e.get("TYPE", "")).upper() == "QUAD" and "K1" in e)
            snapshot = build_model_snapshot(context, fields)
            settings["model_snapshot"] = snapshot.as_metadata()
            settings["model_elements"] = list(path)
            settings["model_checks"] = {f.source_pv: f.source_value for f in snapshot.fields
                                        if f.element_id != preset.quad and f.source_pv is not None}
            projections = []
            plane = options.get("plane", "x")
            for k1 in scans:
                matrix = np.asarray(model.get_map(preset.quad, preset.flag, k1=k1,
                    lattice_overrides=snapshot.lattice_overrides, seq="ent2exit", twiss_only=True))
                offset = 0 if plane == "x" else 2
                projections.append((float(matrix[offset, offset]), float(matrix[offset, offset+1])))
            geometry = resolve_element_image_geometry(context, preset.flag, "real")
            roi = options.get("roi")
            if roi is None and geometry.default_roi:
                g = geometry.default_roi
                roi = (g["x"], g["x"]+g["width"], g["y"], g["y"]+g["height"])
            camera_settings = CameraSettings(geometry.shape, geometry.pixel_width_mm, plane,
                geometry.flip_y, None if roi is None else tuple(roi),
                samples=int(options.get("samples", 5)),
                sample_interval=float(options.get("sample_interval", preset.scan.sample_interval or 0)),
                saturation_level=options.get("saturation_level"))
            settings["camera"] = asdict(camera_settings)
        else:
            projections = [(1., v) for v in scans]
        config = WorkflowConfig(float(options["current_low"]), float(options["current_high"]),
            scans, tuple(projections), energy, options.get("plane", "x"),
            options.get("model", "transport_moments"), int(options.get("active_queries", 3)),
            float(options.get("max_minutes", 120)), json.dumps(settings, sort_keys=True, allow_nan=False))
    if settings["profile_fingerprint"] != profile_fingerprint(context):
        raise ValueError("Machine profile changed since this run was created")
    if backend == "real":
        import epics
        from .optimization import OptimizationVariable
        from half_linac.src.shared.machine_profile.pixel_geometry import resolve_element_image_geometry
        channels = camera_contract(context, preset.flag)
        cs = dict(settings["camera"])
        cs["shape"] = tuple(cs["shape"])
        cs["roi"] = None if cs["roi"] is None else tuple(cs["roi"])
        camera_settings = CameraSettings(**cs)
        geometry = resolve_element_image_geometry(context, preset.flag, "real")
        if (geometry.shape != camera_settings.shape or geometry.pixel_width_mm != camera_settings.pixel_width_mm
                or geometry.flip_y != camera_settings.flip_y):
            raise ValueError("Camera geometry changed")
        background_path = (str(Path(run_dir)/"background.npy") if resume and
                           options.get("background") else options.get("background"))
        background = None if not background_path else np.load(background_path, allow_pickle=False)
        camera = CameraPointAcquirer(camera_settings, get=epics.caget, image_pv=channels["image"],
            frame_id_pv=channels["frame_id"], exposure_pv=channels["exposure_time"],
            background=background, identity_verified=True)
        machine = HalfRealInfoBAXMachine(context,
            OptimizationVariable(options["variable"], config.current_low, config.current_high),
            config, camera, scan_quad=preset.quad,
            settle_time=float(options.get("settle_time", preset.scan.settle_time or 0)),
            model_checks=settings.get("model_checks"))
    else:
        machine = FakeInfoBAXMachine(config, initial_current=float(options.get("fake_initial", 0.0)))
    kwargs = {"approve": approve, "progress": progress}
    if resume:
        return InfoBAXWorkflow.load(config, machine, run_dir, **kwargs)
    return InfoBAXWorkflow(config, machine, run_dir, numerical_config=numerical_config, **kwargs)


def main(argv=None):
    import argparse
    import sys
    from datetime import datetime
    from uuid import uuid4
    parser = argparse.ArgumentParser(description="Standalone HALF InfoBAX emittance optimization (default: Fake)")
    parser.add_argument("--backend", choices=("fake", "real"), default="fake")
    parser.add_argument("--run-root", type=Path, default=Path.cwd()/"runtime"/"emit_infobax")
    parser.add_argument("--headless", action="store_true", help="Fake workflow only")
    parser.add_argument("--approve-fake", action="store_true", help="Explicitly approve simulated motions")
    parser.add_argument("--quick", action="store_true", help="Reduced Monte Carlo settings for Fake smoke tests")
    parser.add_argument("--active-queries", type=int, choices=range(4), default=3)
    args=parser.parse_args(argv)
    if args.headless:
        if args.backend!="fake" or not args.approve_fake:
            parser.error("Headless execution requires --backend fake --approve-fake")
        from half_linac.src.optimization.infobax import InfoBAXConfig
        options={"backend":"fake","current_low":-1.,"current_high":1.,
                 "scan_low":-1.,"scan_high":1.,"scan_points":5,
                 "active_queries":args.active_queries}
        numerical=(InfoBAXConfig(outer_fantasies=4,inner_samples=32,batch_count=2,
                                direct_prior_samples=64,summary_samples=64) if args.quick else None)
        run_dir=args.run_root/(datetime.now().strftime("%Y%m%d_%H%M%S")+"_"+uuid4().hex[:8])
        w=build_workflow(options,run_dir,approve=lambda request,check:True,numerical_config=numerical)
        result=w.run()
        print(json.dumps({"status":result["status"],"restoration_status":result["restoration_status"],
                          "error":result["error"],"run_dir":str(run_dir)},ensure_ascii=False))
        return 0 if result["status"] in {"completed","degraded_negative_eig","invalid_recommendation"} and result["restoration_status"]=="succeeded" else 1
    from .infobax_gui import show_window
    return show_window(args.backend,args.run_root)


if __name__=="__main__":
    raise SystemExit(main())
