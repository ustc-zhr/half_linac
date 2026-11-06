from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import shutil
import tempfile
import warnings

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.compose import TransformedTargetRegressor
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error
from sklearn.neural_network import MLPRegressor
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from .data import (
    AggregatedRun,
    PreparedDataset,
    discover_runs,
    inspect_run,
    load_aggregated_run,
    prepare_dataset,
)
from .tasks import get_training_task
from .types import (
    CancelCallback,
    CandidateMetric,
    ProgressCallback,
    RunDescriptor,
    RunRole,
    TrainingExperimentResult,
    TrainingExperimentSpec,
)


class TrainingCancelled(RuntimeError):
    pass


class PersistenceDeltaRegressor:
    """Serializable zero-delta baseline with the same predict contract as models."""

    def fit(self, x, y=None):
        return self

    def predict(self, x):
        return np.zeros(len(x), dtype=float)


class Trend12DeltaRegressor:
    """Serializable 12-window mean-trend baseline."""

    def __init__(self, feature_index: int, horizon_windows: int) -> None:
        self.feature_index = int(feature_index)
        self.horizon_windows = int(horizon_windows)

    def fit(self, x, y=None):
        return self

    def predict(self, x):
        values = np.asarray(x, dtype=float)[:, self.feature_index]
        return np.nan_to_num(values, nan=0.0) * self.horizon_windows


def _json_value(value):
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, (np.bool_,)):
        return bool(value)
    return value


def _notify(callback: ProgressCallback | None, percent: int, message: str) -> None:
    if callback is not None:
        callback(max(0, min(100, int(percent))), str(message))


def _check_cancel(callback: CancelCallback | None) -> None:
    if callback is not None and callback():
        raise TrainingCancelled("Training cancelled")


def _model_candidates():
    imputer_args = {"strategy": "median", "keep_empty_features": True}
    return {
        "ridge": (
            make_pipeline(
                SimpleImputer(**imputer_args),
                StandardScaler(),
                Ridge(alpha=10.0),
            ),
            {"class": "Ridge", "alpha": 10.0},
            {"imputer": "median", "scaler": "standard"},
        ),
        "gradient_boosting": (
            make_pipeline(
                SimpleImputer(**imputer_args),
                HistGradientBoostingRegressor(
                    max_iter=180,
                    learning_rate=0.05,
                    max_leaf_nodes=15,
                    min_samples_leaf=20,
                    l2_regularization=1.0,
                    random_state=42,
                ),
            ),
            {
                "class": "HistGradientBoostingRegressor",
                "max_iter": 180,
                "learning_rate": 0.05,
                "max_leaf_nodes": 15,
                "min_samples_leaf": 20,
                "l2_regularization": 1.0,
                "random_state": 42,
            },
            {"imputer": "median"},
        ),
        "mlp": (
            TransformedTargetRegressor(
                regressor=make_pipeline(
                    SimpleImputer(**imputer_args),
                    StandardScaler(),
                    MLPRegressor(
                        hidden_layer_sizes=(32, 16),
                        activation="relu",
                        alpha=0.01,
                        learning_rate_init=0.001,
                        max_iter=500,
                        early_stopping=False,
                        shuffle=False,
                        random_state=42,
                    ),
                ),
                transformer=StandardScaler(),
            ),
            {
                "class": "MLPRegressor",
                "hidden_layer_sizes": [32, 16],
                "activation": "relu",
                "alpha": 0.01,
                "learning_rate_init": 0.001,
                "max_iter": 500,
                "early_stopping": False,
                "shuffle": False,
                "random_state": 42,
            },
            {"imputer": "median", "scaler": "standard", "target_scaler": "standard"},
        ),
    }


def _metric(
    *,
    scope: str,
    run_id: str,
    candidate_id: str,
    feature_group: str,
    model_name: str,
    exportable: bool,
    actual_delta: np.ndarray,
    predicted_delta: np.ndarray,
) -> CandidateMetric:
    baseline_mse = float(np.mean(np.square(actual_delta)))
    mse = float(np.mean(np.square(actual_delta - predicted_delta)))
    if actual_delta.size > 1 and np.std(actual_delta) > 0.0 and np.std(predicted_delta) > 0.0:
        correlation = float(np.corrcoef(actual_delta, predicted_delta)[0, 1])
    else:
        correlation = math.nan
    threshold = float(np.median(np.abs(actual_delta))) if actual_delta.size else math.inf
    meaningful = np.abs(actual_delta) >= threshold
    direction = (
        float(np.mean(np.sign(actual_delta[meaningful]) == np.sign(predicted_delta[meaningful])))
        if np.any(meaningful)
        else math.nan
    )
    return CandidateMetric(
        scope=scope,
        run_id=run_id,
        candidate_id=candidate_id,
        feature_group=feature_group,
        model=model_name,
        exportable=exportable,
        samples=int(actual_delta.size),
        rmse_mm=float(np.sqrt(mse)),
        mae_mm=float(mean_absolute_error(actual_delta, predicted_delta)),
        persistence_rmse_mm=float(np.sqrt(baseline_mse)),
        skill_vs_persistence=(float(1.0 - mse / baseline_mse) if baseline_mse > 0.0 else math.nan),
        delta_correlation=correlation,
        direction_accuracy_large_half=direction,
    )


def _runtime_versions() -> dict[str, str]:
    try:
        app_version = importlib.metadata.version("jitter-analysis")
    except importlib.metadata.PackageNotFoundError:
        app_version = "0.3.0"
    return {
        "python": platform.python_version(),
        "numpy": np.__version__,
        "pandas": pd.__version__,
        "scikit_learn": sklearn.__version__,
        "joblib": joblib.__version__,
        "jitter_analysis": app_version,
    }


class TrainingService:
    def discover_runs(self, root: str, task_id: str = "fir_bpm04_forecast") -> list[RunDescriptor]:
        return discover_runs(root, get_training_task(task_id))

    def validate_spec(self, spec: TrainingExperimentSpec) -> list[RunDescriptor]:
        task = get_training_task(spec.task_id)
        if not spec.train_paths:
            raise ValueError("Select at least one Train run")
        if not spec.test_paths:
            raise ValueError("Select at least one Test run")
        if not math.isfinite(spec.beam_threshold):
            raise ValueError("Beam threshold must be finite")
        if not math.isfinite(spec.window_seconds) or spec.window_seconds <= 0.0:
            raise ValueError("Window length must be positive")
        if int(spec.horizon_windows) <= 0:
            raise ValueError("Prediction horizon must be at least one window")

        descriptors = [inspect_run(path, task) for path in spec.train_paths + spec.test_paths]
        incompatible = [item for item in descriptors if not item.compatible]
        if incompatible:
            detail = " | ".join(
                f"{item.run_id}: {'; '.join(item.issues)}" for item in incompatible
            )
            raise ValueError(f"Selected runs are incompatible: {detail}")
        run_ids = [item.run_id for item in descriptors]
        if len(run_ids) != len(set(run_ids)):
            raise ValueError("A run may only be assigned once")
        target_units = {
            item.units.get(task.target_channel, "")
            for item in descriptors
            if item.units.get(task.target_channel, "")
        }
        if len(target_units) > 1:
            raise ValueError("Selected runs use inconsistent target units")
        return descriptors

    def run(
        self,
        spec: TrainingExperimentSpec,
        *,
        progress: ProgressCallback | None = None,
        cancelled: CancelCallback | None = None,
    ) -> TrainingExperimentResult:
        started_at = datetime.now()
        task = get_training_task(spec.task_id)
        _notify(progress, 1, "Validating selected runs")
        descriptors = self.validate_spec(spec)
        _check_cancel(cancelled)
        descriptor_by_path = {item.path: item for item in descriptors}
        common_channels = set(descriptors[0].object_ids)
        for descriptor in descriptors[1:]:
            common_channels.intersection_update(descriptor.object_ids)
        selected_channels = sorted(
            set(task.required_channels).union(
                channel
                for recipe in task.feature_recipes
                for channel in recipe.channels
                if channel in common_channels
            )
        )
        dropped_channels = sorted(
            {
                channel
                for recipe in task.feature_recipes
                for channel in recipe.channels
                if channel not in common_channels
            }
        )
        experiment_warnings: list[str] = []
        if dropped_channels:
            experiment_warnings.append(
                "Optional channels absent from at least one run were dropped: "
                + ", ".join(dropped_channels)
            )
        if not spec.subtract_upstream_orbit:
            experiment_warnings.append(
                "Target is raw BPM04.x position; it is not an orbit-subtracted energy residual."
            )

        loaded_runs: dict[str, AggregatedRun] = {}
        selected_paths = spec.train_paths + spec.test_paths
        for index, path in enumerate(selected_paths):
            _check_cancel(cancelled)
            descriptor = descriptor_by_path[str(Path(path).resolve())]
            role = spec.run_roles.get(path, spec.run_roles.get(descriptor.path, RunRole.IGNORE.value))
            _notify(
                progress,
                3 + int(17 * index / max(1, len(selected_paths))),
                f"Loading {descriptor.run_id}",
            )
            loaded_runs[descriptor.path] = load_aggregated_run(
                descriptor, spec, task, selected_channels, role
            )

        train_paths = [str(Path(path).resolve()) for path in spec.train_paths]
        test_paths = [str(Path(path).resolve()) for path in spec.test_paths]
        prepared: dict[str, dict[str, PreparedDataset]] = {}
        active_recipes = []
        for recipe in task.feature_recipes:
            compatible_recipe_channels = [
                channel for channel in recipe.channels if channel in common_channels
            ]
            if not recipe.include_target_history and not compatible_recipe_channels:
                experiment_warnings.append(
                    f"Feature recipe {recipe.label} was skipped because no channels are common."
                )
                continue
            active_recipes.append(recipe)
            prepared[recipe.id] = {
                path: prepare_dataset(
                    loaded_runs[path], recipe, task, common_channels, spec.horizon_windows
                )
                for path in train_paths + test_paths
            }
        if not active_recipes:
            raise ValueError("No feature recipes are available for the selected runs")

        metrics: list[CandidateMetric] = []
        prediction_rows: list[dict[str, object]] = []
        models: dict[str, object] = {}
        candidate_metadata: dict[str, dict[str, object]] = {}

        def add_candidate_metrics(
            candidate_id: str,
            feature_group: str,
            model_name: str,
            exportable: bool,
            predictions_by_path: dict[str, np.ndarray],
            datasets_by_path: dict[str, PreparedDataset],
        ) -> None:
            pooled_actual = []
            pooled_predicted = []
            for path in test_paths:
                dataset = datasets_by_path[path]
                predicted = np.asarray(predictions_by_path[path], dtype=float)
                actual = dataset.y_delta
                metrics.append(
                    _metric(
                        scope="run",
                        run_id=dataset.run_id,
                        candidate_id=candidate_id,
                        feature_group=feature_group,
                        model_name=model_name,
                        exportable=exportable,
                        actual_delta=actual,
                        predicted_delta=predicted,
                    )
                )
                pooled_actual.append(actual)
                pooled_predicted.append(predicted)
                for row_index in range(len(actual)):
                    prediction_rows.append(
                        {
                            "run_id": dataset.run_id,
                            "candidate_id": candidate_id,
                            "feature_group": feature_group,
                            "model": model_name,
                            "window_index": int(dataset.window_indices[row_index]),
                            "current_q_mm": float(dataset.current_target[row_index]),
                            "actual_delta_mm": float(actual[row_index]),
                            "predicted_delta_mm": float(predicted[row_index]),
                            "actual_next_q_mm": float(dataset.current_target[row_index] + actual[row_index]),
                            "predicted_next_q_mm": float(dataset.current_target[row_index] + predicted[row_index]),
                        }
                    )
            metrics.append(
                _metric(
                    scope="pooled",
                    run_id="ALL",
                    candidate_id=candidate_id,
                    feature_group=feature_group,
                    model_name=model_name,
                    exportable=exportable,
                    actual_delta=np.concatenate(pooled_actual),
                    predicted_delta=np.concatenate(pooled_predicted),
                )
            )

        baseline_datasets = prepared[active_recipes[0].id]
        baseline_feature_names = baseline_datasets[train_paths[0]].feature_names
        persistence_model = PersistenceDeltaRegressor()
        trend_model = Trend12DeltaRegressor(
            baseline_feature_names.index("q_rate_lag12"), spec.horizon_windows
        )
        for candidate_id, label, model, model_parameters in (
            (
                "persistence",
                "Persistence",
                persistence_model,
                {"class": "PersistenceDeltaRegressor"},
            ),
            (
                "trend_12",
                "12-window trend",
                trend_model,
                {
                    "class": "Trend12DeltaRegressor",
                    "history_windows": 12,
                    "horizon_windows": int(spec.horizon_windows),
                },
            ),
        ):
            add_candidate_metrics(
                candidate_id,
                "baseline",
                label,
                True,
                {
                    path: np.asarray(model.predict(baseline_datasets[path].x), dtype=float)
                    for path in test_paths
                },
                baseline_datasets,
            )
            models[candidate_id] = model
            candidate_metadata[candidate_id] = {
                "candidate_id": candidate_id,
                "feature_group": "target_history",
                "feature_label": "Target history",
                "feature_names": baseline_feature_names,
                "model": model_parameters,
                "preprocessing": {},
            }

        candidate_total = len(active_recipes) * len(_model_candidates())
        candidate_index = 0
        for recipe in active_recipes:
            datasets = prepared[recipe.id]
            x_train = np.concatenate([datasets[path].x for path in train_paths])
            y_train = np.concatenate([datasets[path].y_delta for path in train_paths])
            for model_name, (model, model_params, preprocessing) in _model_candidates().items():
                _check_cancel(cancelled)
                candidate_index += 1
                candidate_id = f"{recipe.id}__{model_name}"
                _notify(
                    progress,
                    20 + int(67 * candidate_index / max(1, candidate_total)),
                    f"Training {recipe.label} / {model_name}",
                )
                with warnings.catch_warnings(record=True) as caught:
                    warnings.simplefilter("always")
                    model.fit(x_train, y_train)
                for warning in caught:
                    text = str(warning.message)
                    if text and text not in experiment_warnings:
                        experiment_warnings.append(f"{candidate_id}: {text}")
                predictions_by_path = {
                    path: np.asarray(model.predict(datasets[path].x), dtype=float)
                    for path in test_paths
                }
                add_candidate_metrics(
                    candidate_id,
                    recipe.id,
                    model_name,
                    True,
                    predictions_by_path,
                    datasets,
                )
                models[candidate_id] = model
                candidate_metadata[candidate_id] = {
                    "candidate_id": candidate_id,
                    "feature_group": recipe.id,
                    "feature_label": recipe.label,
                    "feature_names": datasets[train_paths[0]].feature_names,
                    "model": model_params,
                    "preprocessing": preprocessing,
                }

        recommended = self._recommend_candidate(metrics, test_paths, loaded_runs)
        pooled_metrics = [item for item in metrics if item.scope == "pooled"]
        plot_candidate = (
            recommended
            or min(pooled_metrics, key=lambda item: item.rmse_mm).candidate_id
        )
        _check_cancel(cancelled)
        _notify(progress, 90, "Saving experiment record")
        result = self._save_experiment(
            spec=spec,
            task=task,
            descriptors=descriptors,
            loaded_runs=loaded_runs,
            common_channels=sorted(common_channels),
            selected_channels=selected_channels,
            metrics=metrics,
            prediction_rows=prediction_rows,
            models=models,
            candidate_metadata=candidate_metadata,
            recommended=recommended,
            plot_candidate=plot_candidate,
            warnings_list=experiment_warnings,
            started_at=started_at,
        )
        _notify(progress, 100, f"Saved {result.experiment_id}")
        return result

    @staticmethod
    def _recommend_candidate(
        metrics: list[CandidateMetric],
        test_paths: list[str],
        loaded_runs: dict[str, AggregatedRun],
    ) -> str | None:
        test_run_ids = {loaded_runs[path].descriptor.run_id for path in test_paths}
        by_candidate: dict[str, list[CandidateMetric]] = {}
        for item in metrics:
            if item.scope == "run" and item.exportable:
                by_candidate.setdefault(item.candidate_id, []).append(item)
        eligible: list[tuple[float, float, str]] = []
        pooled_by_candidate = {
            item.candidate_id: item for item in metrics if item.scope == "pooled"
        }
        for candidate_id, rows in by_candidate.items():
            if {row.run_id for row in rows} != test_run_ids:
                continue
            skills = [row.skill_vs_persistence for row in rows]
            if not skills or any(not math.isfinite(value) or value <= 0.0 for value in skills):
                continue
            pooled = pooled_by_candidate[candidate_id]
            eligible.append((min(skills), -pooled.rmse_mm, candidate_id))
        if not eligible:
            return None
        eligible.sort(reverse=True)
        return eligible[0][2]

    @staticmethod
    def _save_experiment(
        *,
        spec,
        task,
        descriptors,
        loaded_runs,
        common_channels,
        selected_channels,
        metrics,
        prediction_rows,
        models,
        candidate_metadata,
        recommended,
        plot_candidate,
        warnings_list,
        started_at,
    ) -> TrainingExperimentResult:
        output_root = Path(spec.output_root).expanduser().resolve()
        output_root.mkdir(parents=True, exist_ok=True)
        experiment_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f") + "_fir_bpm04_forecast"
        staging = Path(tempfile.mkdtemp(prefix=f".{experiment_id}_", dir=output_root))
        final_dir = output_root / experiment_id
        try:
            candidates_dir = staging / "candidates"
            candidates_dir.mkdir()
            role_by_resolved_path = {
                str(Path(path).resolve()): role for path, role in spec.run_roles.items()
            }
            runtime = _runtime_versions()
            experiment_payload = {
                "schema_version": 1,
                "experiment_id": experiment_id,
                "task": {"id": task.id, "version": task.version, "label": task.label},
                "started_at": started_at.isoformat(),
                "completed_at": datetime.now().isoformat(),
                "spec": {
                    "run_root": str(Path(spec.run_root).resolve()),
                    "output_root": str(output_root),
                    "subtract_upstream_orbit": bool(spec.subtract_upstream_orbit),
                    "coefficient_a": float(spec.coefficient_a),
                    "coefficient_b": float(spec.coefficient_b),
                    "beam_threshold": float(spec.beam_threshold),
                    "window_seconds": float(spec.window_seconds),
                    "horizon_windows": int(spec.horizon_windows),
                    "lag_windows": list(task.lag_windows),
                },
                "target": {
                    "unit": "mm",
                    "formula": (
                        "bpm04_x - a*bpm02_x - b*bpm03_x"
                        if spec.subtract_upstream_orbit
                        else "bpm04_x"
                    ),
                    "a": float(spec.coefficient_a) if spec.subtract_upstream_orbit else 0.0,
                    "b": float(spec.coefficient_b) if spec.subtract_upstream_orbit else 0.0,
                },
                "runs": [
                    {
                        "run_id": item.run_id,
                        "path": item.path,
                        "role": role_by_resolved_path.get(item.path, "ignore"),
                        "created_at": item.created_at,
                        "status": item.status,
                    }
                    for item in descriptors
                ],
                "common_channels": common_channels,
                "selected_channels": selected_channels,
                "recommended_candidate_id": recommended,
                "plot_candidate_id": plot_candidate,
                "warnings": warnings_list,
                "runtime": runtime,
            }
            (staging / "experiment.json").write_text(
                json.dumps(_json_value(experiment_payload), indent=2), encoding="utf-8"
            )
            quality_rows = [asdict(run.quality) for run in loaded_runs.values()]
            pd.DataFrame(quality_rows).to_csv(staging / "run_quality.csv", index=False)
            metric_rows = [asdict(item) for item in metrics]
            pd.DataFrame(metric_rows).to_csv(staging / "metrics.csv", index=False)
            pd.DataFrame(prediction_rows).to_csv(staging / "predictions.csv", index=False)

            for candidate_id, model in models.items():
                model_path = candidates_dir / f"{candidate_id}.joblib"
                joblib.dump(model, model_path)
                metadata = dict(candidate_metadata[candidate_id])
                metadata.update(
                    {
                        "task_id": task.id,
                        "task_version": task.version,
                        "model_file": model_path.name,
                        "runtime": runtime,
                    }
                )
                (candidates_dir / f"{candidate_id}.json").write_text(
                    json.dumps(_json_value(metadata), indent=2), encoding="utf-8"
                )
            os.replace(staging, final_dir)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

        return TrainingExperimentResult(
            experiment_id=experiment_id,
            experiment_dir=str(final_dir),
            task_id=task.id,
            recommended_candidate_id=recommended,
            plot_candidate_id=plot_candidate,
            candidate_model_paths={
                candidate_id: str(final_dir / "candidates" / f"{candidate_id}.joblib")
                for candidate_id in models
            },
            metrics_path=str(final_dir / "metrics.csv"),
            predictions_path=str(final_dir / "predictions.csv"),
            warnings=list(warnings_list),
        )
