"""Build frozen standalone runs, with a Fake backend as the launch default."""
from dataclasses import asdict
import json
from pathlib import Path

import numpy as np

from half_linac.src.shared.machine_profile import load_app_context, get_emit_preset
from .measurement_service import fingerprint
from .infobax_machine import FakeInfoBAXMachine
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
    if backend != "fake":
        raise ValueError("Real InfoBAX runs use the main Emittance Optimization / InfoBAX tab")
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
        projections = [(1., v) for v in scans]
        config = WorkflowConfig(float(options["current_low"]), float(options["current_high"]),
            scans, tuple(projections), energy, options.get("plane", "x"),
            options.get("model", "transport_moments"), int(options.get("active_queries", 3)),
            float(options.get("max_minutes", 120)), json.dumps(settings, sort_keys=True, allow_nan=False))
    if settings["profile_fingerprint"] != profile_fingerprint(context):
        raise ValueError("Machine profile changed since this run was created")
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
    if args.backend == "real":
        return launch_framework_gui()
    from .infobax_gui import show_window
    return show_window("fake",args.run_root)


def launch_framework_gui():
    """The legacy --backend real command opens the production framework tab."""
    import os
    import sys
    from PyQt5.QtWidgets import QApplication
    os.environ["HALF_LINAC_MACHINE_ID"]="half"
    os.environ["HALF_LINAC_CONTROL_BACKEND"]="real"
    directory=str(Path(__file__).parent)
    if directory not in sys.path:sys.path.insert(0,directory)
    from .main import myWindow
    app=QApplication.instance() or QApplication(sys.argv[:1])
    window=myWindow()
    window.show()
    window._show_optimization()
    window.optimization_dialog.tabs.setCurrentIndex(1)
    return app.exec_()


if __name__=="__main__":
    raise SystemExit(main())
