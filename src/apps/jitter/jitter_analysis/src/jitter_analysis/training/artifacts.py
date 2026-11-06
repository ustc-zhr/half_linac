from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import math
from pathlib import Path
import tempfile
import zipfile

import pandas as pd

from .types import ModelPackageManifest


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _clean_records(frame: pd.DataFrame) -> list[dict[str, object]]:
    records = []
    for raw in frame.to_dict(orient="records"):
        row = {}
        for key, value in raw.items():
            if isinstance(value, float) and not math.isfinite(value):
                row[key] = None
            elif hasattr(value, "item"):
                row[key] = value.item()
            else:
                row[key] = value
        records.append(row)
    return records


def export_model_package(
    experiment_dir: str | Path,
    candidate_id: str,
    destination: str | Path,
) -> Path:
    experiment_path = Path(experiment_dir).resolve()
    experiment = json.loads((experiment_path / "experiment.json").read_text(encoding="utf-8"))
    candidate_path = experiment_path / "candidates" / f"{candidate_id}.json"
    model_path = experiment_path / "candidates" / f"{candidate_id}.joblib"
    if not candidate_path.exists() or not model_path.exists():
        raise ValueError(f"Candidate is not exportable: {candidate_id}")
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    metrics_frame = pd.read_csv(experiment_path / "metrics.csv")
    metrics_frame = metrics_frame[metrics_frame["candidate_id"] == candidate_id]
    if metrics_frame.empty:
        raise ValueError(f"Metrics are missing for candidate: {candidate_id}")

    spec = experiment["spec"]
    manifest = ModelPackageManifest(
        schema_version=1,
        package_kind="jitter_analysis_training_model",
        task_id=str(candidate["task_id"]),
        task_version=str(candidate["task_version"]),
        candidate_id=candidate_id,
        target=dict(experiment["target"]),
        data_contract={
            "input_dtype": "float64",
            "input_shape": [None, len(candidate["feature_names"])],
            "feature_semantics": "causal per-window change rates",
            "output": "predicted target delta in mm",
            "window_seconds": spec["window_seconds"],
            "horizon_windows": spec["horizon_windows"],
            "lag_windows": spec["lag_windows"],
            "gate_channel": "bpm04_s",
            "beam_threshold": spec["beam_threshold"],
        },
        feature_names=list(candidate["feature_names"]),
        preprocessing=dict(candidate["preprocessing"]),
        model=dict(candidate["model"]),
        provenance={
            "experiment_id": experiment["experiment_id"],
            "started_at": experiment["started_at"],
            "completed_at": experiment["completed_at"],
            "runs": experiment["runs"],
            "warnings": experiment.get("warnings", []),
        },
        metrics=_clean_records(metrics_frame),
        runtime=dict(experiment["runtime"]),
        model_sha256=_sha256(model_path),
    )

    destination_path = Path(destination).expanduser()
    if destination_path.suffix.lower() != ".zip":
        destination_path = destination_path.with_suffix(".zip")
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=f".{destination_path.name}.", suffix=".tmp", dir=destination_path.parent, delete=False
    ) as temporary:
        temporary_path = Path(temporary.name)
    try:
        with zipfile.ZipFile(temporary_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(model_path, arcname="model.joblib")
            archive.writestr("manifest.json", json.dumps(asdict(manifest), indent=2))
            archive.writestr(
                "metrics.json",
                json.dumps({"candidate_id": candidate_id, "metrics": manifest.metrics}, indent=2),
            )
        temporary_path.replace(destination_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    return destination_path.resolve()
