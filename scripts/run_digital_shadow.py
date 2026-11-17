#!/usr/bin/env python3
"""Replay one saved working point through Elegant without EPICS operations."""
from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from repo_bootstrap import ensure_repo_import_path

ensure_repo_import_path(__file__)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    example = commands.add_parser("example", help="Write explicitly synthetic inputs; no simulation or PV access")
    example.add_argument("--output-dir", type=Path, required=True, help="New directory for synthetic inputs")
    replay = commands.add_parser("replay", help="Run Elegant using only saved inputs; no PV access")
    replay.add_argument("--snapshot", type=Path, required=True)
    replay.add_argument("--request", type=Path, required=True)
    replay.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "example":
            from half_linac.src.shared.digital_shadow.example import write_example
            directory = write_example(args.output_dir)
            print(json.dumps({"synthetic": True, "input_dir": str(directory)}, ensure_ascii=False))
            return 0
        from half_linac.src.shared.digital_shadow import ShadowRequest, run_once
        from half_linac.src.shared.machine_profile.loader import load_model_context
        from half_linac.src.shared.machine_profile.models import ControlBackendConfig
        from half_linac.src.shared.machine_state import load_snapshot

        snapshot = load_snapshot(args.snapshot)
        request = ShadowRequest.from_dict(json.loads(args.request.read_text(encoding="utf-8")))
        context = replace(load_model_context(snapshot.machine_id, request.model_backend),
                          control_backend=ControlBackendConfig(snapshot.backend))
        result = run_once(context, snapshot, request, args.output_root)
        print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))
        return 0 if result["status"] == "complete" else 2
    except (RuntimeError, ValueError, TypeError, KeyError, OSError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
