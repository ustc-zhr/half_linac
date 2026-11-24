#!/usr/bin/env python3
"""Replay saved inputs or read one VM working point through Elegant."""
from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path
import signal
import sys
from threading import Event
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from repo_bootstrap import ensure_repo_import_path

ensure_repo_import_path(__file__)


def _interrupt(_number, _frame):
    raise KeyboardInterrupt


def _particle_live_commands(commands):
    for command, help_text in (("capture-particle-snapshot", "Save full-line VM or simulated Real readbacks"),
                               ("follow-particle", "Follow full-line VM or simulated Real readbacks with ELEGANT")):
        parser = commands.add_parser(command, help=help_text)
        parser.add_argument("--source", choices=("vm", "demo-real"), required=True)
        parser.add_argument("--output-root", type=Path, required=True)
        parser.add_argument("--vm-runtime-json", type=Path)
        parser.add_argument("--capture-timeout", type=float, default=5.0)
        if command == "follow-particle":
            parser.add_argument("--beam-path", type=Path, required=True)
            parser.add_argument("--impact-handoff", type=Path, required=True)
            parser.add_argument("--poll-interval", type=float, default=1.0)
            parser.add_argument("--model-timeout", type=float, default=120.0)


def _particle_live(args, context):
    from half_linac.src.shared.digital_shadow.particle_capture import ParticleCapture, archive_particle_capture
    from half_linac.src.shared.digital_shadow.particle_follow import run_follow

    if args.source != "vm" and args.vm_runtime_json is not None:
        raise ValueError("A VM runtime JSON applies only to the VM capture source")
    stop = Event()
    previous = {number: signal.signal(number, lambda *_: stop.set()) for number in (signal.SIGTERM, signal.SIGINT)}
    following = args.command == "follow-particle"
    ready_budget = min(0.05, args.capture_timeout / 10) if following and args.source == "vm" else None
    try:
        capture = ParticleCapture(context, args.source, runtime_json=args.vm_runtime_json,
            timeout_s=args.capture_timeout - (ready_budget or 0), ready_timeout_s=ready_budget or 10.0)
        if following:
            run_follow(context, capture, args.output_root, beam_path=args.beam_path.expanduser(),
                       handoff_path=args.impact_handoff.expanduser(), poll_interval=args.poll_interval,
                       model_timeout=args.model_timeout, stop=stop,
                       emit=lambda event: print(json.dumps(event, allow_nan=False), flush=True))
        else:
            snapshot, evidence = capture(stop.is_set)
            if not stop.is_set():
                directory = args.output_root.resolve() / f"capture_{uuid.uuid4().hex}"
                snapshot_path, evidence_path = archive_particle_capture(directory, snapshot, evidence)
                print(json.dumps({"status": "complete", "snapshot_id": snapshot.snapshot_id,
                                  "snapshot_path": str(snapshot_path), "evidence_path": str(evidence_path)}))
    except (RuntimeError, ValueError, OSError):
        if not stop.is_set():
            raise
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)
    return 130 if stop.is_set() else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    example = commands.add_parser("example", help="Write explicitly synthetic inputs; no simulation or PV access")
    example.add_argument("--output-dir", type=Path, required=True, help="New directory for synthetic inputs")
    examples = example.add_mutually_exclusive_group()
    examples.add_argument("--vm", action="store_true", help="Generate a QT02 to PRF06 VM request and synthetic VM snapshot")
    examples.add_argument("--real-demo", action="store_true", help="Generate a four-screen synthetic request for demo-real; no PV access")
    replay = commands.add_parser("replay", help="Run Elegant using only saved inputs; no PV access")
    replay.add_argument("--snapshot", type=Path, required=True)
    replay.add_argument("--request", type=Path, required=True)
    replay.add_argument("--output-root", type=Path, required=True)
    replay.add_argument("--vm-evidence", type=Path, help="Saved evidence from a verified VM capture; no reconnection")
    replay.add_argument("--real-evidence", type=Path, help="Saved real capture metadata; offline, no reconnection")
    replay.add_argument("--image-index", type=Path, help="Saved image references; defaults to image_index.json beside snapshot")
    particle = commands.add_parser("run-model", help="Run the offline HALF particle model")
    particle.add_argument("--request", type=Path, required=True)
    particle.add_argument("--output-root", type=Path, required=True)
    particle.add_argument("--beam-path", type=Path, help="Override the particle input path from the request")
    particle.add_argument("--impact-handoff", type=Path,
                          help="IMPACT-T native-plane metadata for archived beam and VM snapshot replay")
    _particle_live_commands(commands)
    capture = commands.add_parser("run-vm", help="Read a VM working point once, then calculate and compare; never access real PV mappings")
    capture.add_argument("--machine", default="half")
    capture.add_argument("--request", type=Path, required=True)
    capture.add_argument("--output-root", type=Path, required=True)
    capture.add_argument("--vm-runtime-json", type=Path, help="Running VM instance JSON; defaults to the selected machine profile")
    capture.add_argument("--capture-timeout", type=float, default=5.0)
    capture.add_argument("--ready-timeout", type=float, default=10.0)
    for command, help_text in (("demo-real", "Exercise real capture through an injected synthetic reader; NO PV access"),
                               ("run-real", "Explicit real read-only capture; requires --connect-real")):
        real = commands.add_parser(command, help=help_text)
        real.add_argument("--machine", choices=("half",), default="half")
        real.add_argument("--request", type=Path, required=True)
        real.add_argument("--output-root", type=Path, required=True)
        real.add_argument("--capture-timeout", type=float, default=5.0)
        if command == "run-real":
            real.add_argument("--connect-real", action="store_true", help="Explicitly enable this invocation's live real PV reads")
    for command_parser in (capture, *[commands.choices[name] for name in ("demo-real", "run-real")]):
        command_parser.add_argument("--save-images", action="store_true", help="Save one original frame per selected screen, losslessly")
        command_parser.add_argument("--image-limit-gib", type=float, default=10.0, help="Shared image pool limit; never delete existing objects")
        command_parser.add_argument("--image-store", type=Path, help="Shared image pool; defaults to output-root/image_store")
        command_parser.add_argument("--image-backgrounds", type=Path, help="Optional per-screen background path/provenance JSON")
    args = parser.parse_args(argv)
    try:
        if args.command == "example":
            if args.real_demo:
                from half_linac.src.shared.digital_shadow.real_demo import demo_request
                from half_linac.src.shared.runtime_state import write_runtime_state
                directory = args.output_dir.resolve()
                directory.mkdir(parents=True, exist_ok=False)
                write_runtime_state(directory / "request.json", demo_request().as_metadata())
                print(json.dumps({"synthetic": True, "input_dir": str(directory), "next_command": "demo-real"}))
                return 0
            from half_linac.src.shared.digital_shadow.example import write_example
            directory = write_example(args.output_dir, vm=args.vm)
            print(json.dumps({"synthetic": True, "input_dir": str(directory)}, ensure_ascii=False))
            return 0
        from half_linac.src.shared.digital_shadow import ShadowRequest, run_once
        from half_linac.src.shared.machine_profile.loader import load_model_context
        from half_linac.src.shared.machine_profile.models import ControlBackendConfig
        from half_linac.src.shared.machine_state import load_snapshot

        if args.command in {"capture-particle-snapshot", "follow-particle"}:
            return _particle_live(args, load_model_context("half"))

        if args.command == "run-model":
            from half_linac.src.shared.digital_shadow.particle_model import (
                PROGRESS_PREFIX, ParticleModelRequest, run_particle_model,
            )
            request = ParticleModelRequest.from_dict(json.loads(args.request.read_text(encoding="utf-8")),
                                                     base=args.request.resolve().parent)
            if args.beam_path is not None:
                request = replace(request, beam_path=str(args.beam_path.expanduser().resolve()))
            if args.impact_handoff is not None:
                request = replace(request, handoff_path=str(args.impact_handoff.expanduser().resolve()))
            if request.scope in ("preinjector", "coupled"):
                previous = signal.signal(signal.SIGTERM, _interrupt)
                try:
                    context = load_model_context("half") if request.scope == "coupled" else None
                    result = run_particle_model(context, request, args.output_root,
                        progress=lambda message: print(PROGRESS_PREFIX + message, file=sys.stderr, flush=True))
                finally:
                    signal.signal(signal.SIGTERM, previous)
            else:
                result = run_particle_model(load_model_context("half"), request, args.output_root)
            print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))
            if result["status"] == "interrupted":
                return 130
            return 0 if result["status"] == "complete" else 2

        request = ShadowRequest.from_dict(json.loads(args.request.read_text(encoding="utf-8")))
        real_evidence = None
        image_capture = image_bundle = None
        if args.command != "replay" and args.save_images:
            from half_linac.src.shared.digital_shadow.image_capture import ImageCapture, load_backgrounds
            from half_linac.src.shared.digital_shadow.models import positive
            positive(args.image_limit_gib, "image limit GiB")
            image_context = replace(load_model_context(args.machine, request.model_backend),
                                    control_backend=ControlBackendConfig("vm" if args.command == "run-vm" else "real"))
            image_capture = ImageCapture(image_context, request, backgrounds=load_backgrounds(args.image_backgrounds),
                                         synthetic=args.command == "demo-real")
        elif args.command != "replay" and args.image_backgrounds:
            raise ValueError("--image-backgrounds requires --save-images")
        if args.command == "run-vm":
            from half_linac.src.shared.digital_shadow.vm_capture import capture_vm_once
            # Ignore real defaults and inherited runtime selection for live capture.
            context = replace(load_model_context(args.machine, request.model_backend),
                              control_backend=ControlBackendConfig("vm"))
            snapshot, evidence = capture_vm_once(
                context, request, runtime_json=args.vm_runtime_json,
                timeout_s=args.capture_timeout, ready_timeout_s=args.ready_timeout,
                image_capture=image_capture,
            )
        elif args.command in {"run-real", "demo-real"}:
            from half_linac.src.shared.digital_shadow.real_capture import capture_real_once
            context = replace(load_model_context(args.machine, request.model_backend), control_backend=ControlBackendConfig("real"))
            if args.command == "demo-real":
                from half_linac.src.shared.digital_shadow.real_demo import capture_demo
                snapshot, real_evidence = capture_demo(context, request, timeout_s=args.capture_timeout, image_capture=image_capture)
            else:
                snapshot, real_evidence = capture_real_once(context, request, timeout_s=args.capture_timeout,
                                                            allow_live=args.connect_real, image_capture=image_capture)
            evidence = None
        else:
            snapshot = load_snapshot(args.snapshot)
            context = replace(load_model_context(snapshot.machine_id, request.model_backend),
                              control_backend=ControlBackendConfig(snapshot.backend))
            evidence = json.loads(args.vm_evidence.read_text(encoding="utf-8")) if args.vm_evidence else None
            real_evidence = json.loads(args.real_evidence.read_text(encoding="utf-8")) if args.real_evidence else None
            index_path = args.image_index or args.snapshot.parent / "image_index.json"
            if args.image_index or index_path.exists():
                image_bundle = json.loads(index_path.read_text(encoding="utf-8"))
        if image_capture is not None:
            from half_linac.src.shared.digital_shadow.image_capture import archive_images
            image_bundle = archive_images(image_capture, snapshot, args.output_root,
                                          limit_gib=args.image_limit_gib, vm_evidence=evidence, store_root=args.image_store)
        result = run_once(context, snapshot, request, args.output_root, vm_evidence=evidence,
                          real_evidence=real_evidence, image_bundle=image_bundle)
        print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))
        return 0 if result["status"] == "complete" else 2
    except (RuntimeError, ValueError, TypeError, KeyError, OSError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
