#!/usr/bin/env python3
"""Verify and summarize an ICT1 InfoBAX archive without opening PVs."""

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from repo_bootstrap import ensure_repo_import_path

ensure_repo_import_path(__file__)

from half_linac.src.apps.emit_measure.infobax_ict1 import (
    numerical_ict1_config, read_ict1_window, scan_charge_summary,
)
from half_linac.src.apps.emit_measure.infobax_workflow import (
    ICT1_WORKFLOW_SCHEMA, InfoBAXWorkflow, WorkflowConfig, workflow_pool,
)
from half_linac.src.apps.emit_measure.optimization import ChargeConstraint
from half_linac.src.optimization.infobax import ICT1Observation, InfoBAXConfig, InfoBAXCore, PointObservation


def _check_equal(name, actual, recorded):
    if json.loads(json.dumps(actual)) != recorded:
        raise ValueError(f"Recomputed {name} differs from the archive")


def _beam_observation(record):
    measurement = record["measurement"]
    return PointObservation(record["query_index"], measurement["variance_mm2"], measurement["noise_std_mm2"])


def replay(run_dir):
    run_dir = Path(run_dir)
    data = json.loads((run_dir / InfoBAXWorkflow.filename).read_text())
    if data["schema"] != ICT1_WORKFLOW_SCHEMA:
        raise ValueError("An ICT1 InfoBAX v3 workflow archive is required")
    config = WorkflowConfig(**data["config"])
    _check_equal("workflow fingerprint", config.fingerprint, data["config_fingerprint"])
    settings = config.ict1_settings
    archived_charge = data["ict1"]
    directory = run_dir / "ict1_windows"
    windows = {path.stem: read_ict1_window(directory, path.stem) for path in sorted(directory.glob("*.json"))}
    baseline = archived_charge["baseline"]
    minimum = None if baseline is None else baseline["minimum_nc"]

    for window_id, window in windows.items():
        if window["completed"]:
            charge = scan_charge_summary(settings, [window], connection_error=window["summary"].get("connection_error"))
            if "minimum_nc" in window["summary"]:
                charge.update(minimum_nc=minimum, feasible=charge["status"] == "valid" and charge["value"] >= minimum)
            _check_equal(f"ICT1 window {window_id}", charge, window["summary"])

    if baseline is not None:
        charge = scan_charge_summary(settings, [windows[r["ict1_window_id"]] for r in data["baseline"]])
        minimum_nc = ChargeConstraint.from_settings(settings).minimum_at(charge["value"])
        _check_equal("baseline ICT1", dict(charge, minimum_nc=minimum_nc), baseline)
    verification = archived_charge["verification"]
    if verification is not None:
        charge = scan_charge_summary(settings, [windows[r["ict1_window_id"]] for r in data["verification"]])
        charge.update(minimum_nc=minimum, feasible=charge["status"] == "valid" and charge["value"] >= minimum)
        _check_equal("verification ICT1", charge, verification)

    for action in data["actions"]:
        if "ict1_window_id" in action:
            _check_equal("apply ICT1", windows[action["ict1_window_id"]]["summary"], action["ict1"])

    checkpoint = data["core"]
    queries = []
    if checkpoint is not None:
        pool = workflow_pool(config, data["candidates"], data["baseline"], data["machine_fingerprint"])
        _check_equal("pool fingerprint", pool.fingerprint, checkpoint["pool_fingerprint"])
        core = InfoBAXCore(pool, InfoBAXConfig.from_dict(checkpoint["config"]), model=config.model,
                           initial_queries=checkpoint["initial_queries"], ict1=numerical_ict1_config(settings, baseline))
        for record in data["baseline"]:
            core.observe(_beam_observation(record))
        core.observe_ict1(ICT1Observation("baseline", data["baseline"][0]["candidate_index"],
                                         baseline["value"], baseline["standard_error_nc"]))
        for record in data["records"]:
            if record["phase"] not in {"initialization", "active"}:
                continue
            suggestion = core.propose()
            _check_equal(f"query {record['sequence']}", asdict(suggestion), record["suggestion"])
            if suggestion.phase == "infobax":
                queries.append({"sequence": record["sequence"], "selected_index": suggestion.query_index,
                                "ranking": [row.query_index for row in
                                            sorted(suggestion.diagnostics, key=lambda row: -row.raw_eig_nats)]})
            if "ict1_window_id" in record:
                window_id = record["ict1_window_id"]
                charge = windows[window_id]["summary"]
                _check_equal(f"query {record['sequence']} ICT1", charge, record["ict1"])
                if charge["status"] == "valid":
                    core.observe_ict1(ICT1Observation(window_id, record["candidate_index"],
                                                     charge["value"], charge["standard_error_nc"]))
            if record["state"] == "observed":
                core.observe(_beam_observation(record))
        _check_equal("numerical checkpoint", core.to_checkpoint(), checkpoint)
        if data["recommendation"] is not None:
            _check_equal("recommendation", asdict(core.recommend()), data["recommendation"])

    return {
        "status": data["status"], "confirmed": data["confirmed"],
        "baseline_ict1": baseline, "verification_ict1": verification,
        "ict1_windows": len(windows),
        "valid_ict1_samples": sum(sample["status"] == "valid" for w in windows.values() for sample in w["samples"]),
        "active_attempts": sum(r["phase"] == "active" and r.get("motion_started", False) for r in data["records"]),
        "active_queries": queries, "recommendation": data["recommendation"],
        "raw_frames": data["cost"]["raw_frame_count"], "eig_seconds": archived_charge["eig_seconds"],
        "elapsed_seconds": data["elapsed_seconds"], "restoration_status": data["restoration_status"],
        "restore_readback": archived_charge["restore_readback"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()
    try:
        result = replay(args.run_dir)
    except (ValueError, KeyError, OSError) as error:
        parser.error(str(error))
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
