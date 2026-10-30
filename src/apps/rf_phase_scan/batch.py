from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping
from uuid import uuid4

from epics import caget
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from half_linac.src.apps.rf_phase_scan.executor import (
    RFPhaseScanCallbacks,
    RFPhaseScanExecutor,
    RFPhaseScanRequest,
)
from half_linac.src.apps.rf_phase_scan.history import (
    BatchHistory,
    RFPhaseHistoryError,
    latest_batch_history_path,
    load_batch_history,
)
from half_linac.src.apps.rf_phase_scan.phase_energy_scan import phase_difference_deg
from half_linac.src.shared.machine_profile import (
    EnergyControlLock,
    MachineProfileError,
    list_elements,
    resolve_channel,
    resolve_write_target,
    require_workflow_write_allowed,
)


@dataclass(frozen=True)
class CrestQualitySettings:
    min_points_used: int = 5
    min_r_squared: float = 0.90
    crest_edge_margin_steps: int = 1

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None):
        raw = dict(value or {})
        return cls(
            min_points_used=int(raw.get("min_points_used", 5)),
            min_r_squared=float(raw.get("min_r_squared", 0.90)),
            crest_edge_margin_steps=int(raw.get("crest_edge_margin_steps", 1)),
        )


@dataclass(frozen=True)
class CrestQualityAssessment:
    accepted: bool
    reasons: tuple[str, ...]
    crest_phase_deg: float | None = None
    crest_energy_mev: float | None = None
    energy_gain_mev: float | None = None
    r_squared: float | None = None
    points_used: int = 0


def evaluate_crest_quality(
    result: Mapping[str, Any],
    settings: CrestQualitySettings,
    *,
    energy_low_mev: float,
    energy_high_mev: float,
) -> CrestQualityAssessment:
    reasons: list[str] = []
    fit = result.get("fit")
    if not isinstance(fit, Mapping):
        return CrestQualityAssessment(False, ("Phase-energy fit is unavailable.",))

    names = (
        "baseline_energy_mev",
        "amplitude_mev",
        "crest_phase_unwrapped_deg",
        "crest_phase_command_deg",
        "rmse_mev",
        "r_squared",
        "points_used",
    )
    values: dict[str, float] = {}
    for name in names:
        try:
            value = float(fit[name])
        except (KeyError, TypeError, ValueError):
            reasons.append(f"Fit field {name} is unavailable.")
            continue
        if not math.isfinite(value):
            reasons.append(f"Fit field {name} is not finite.")
            continue
        values[name] = value

    points_used = int(values.get("points_used", 0))
    r_squared = values.get("r_squared")
    if points_used < settings.min_points_used:
        reasons.append(
            f"Only {points_used} points were used; {settings.min_points_used} required."
        )
    if r_squared is None or r_squared < settings.min_r_squared:
        score = "unavailable" if r_squared is None else f"{r_squared:.4f}"
        reasons.append(
            f"Curve R2 {score} is below {settings.min_r_squared:.4f}."
        )

    phases = []
    for point in result.get("points") or ():
        try:
            phase = float(point["requested_phase_unwrapped_deg"])
        except (KeyError, TypeError, ValueError):
            continue
        if math.isfinite(phase):
            phases.append(phase)
    phases = sorted(set(phases))
    crest_unwrapped = values.get("crest_phase_unwrapped_deg")
    if len(phases) < 2:
        reasons.append("The executed phase range is unavailable.")
    elif crest_unwrapped is not None:
        scan_low, scan_high = phases[0], phases[-1]
        step = (scan_high - scan_low) / (len(phases) - 1)
        margin = settings.crest_edge_margin_steps * step
        if crest_unwrapped < scan_low + margin or crest_unwrapped > scan_high - margin:
            reasons.append(
                "Crest is outside the accepted interior of the executed phase range."
            )

    crest_energy = None
    if "baseline_energy_mev" in values and "amplitude_mev" in values:
        crest_energy = values["baseline_energy_mev"] + values["amplitude_mev"]
        if not math.isfinite(crest_energy):
            reasons.append("Fitted crest energy is not finite.")
        elif not float(energy_low_mev) <= crest_energy <= float(energy_high_mev):
            reasons.append("Fitted crest energy is outside coordinated energy limits.")

    return CrestQualityAssessment(
        accepted=not reasons,
        reasons=tuple(reasons),
        crest_phase_deg=values.get("crest_phase_command_deg"),
        crest_energy_mev=crest_energy,
        energy_gain_mev=values.get("amplitude_mev"),
        r_squared=r_squared,
        points_used=points_used,
    )


@dataclass(frozen=True)
class RFPhaseBatchDevice:
    element_id: str
    display_name: str
    order: int
    target: Any
    phase_pv: str
    phase_readback_pv: str


def discover_batch_devices(context, workflow, phase_readback_channel):
    batch = workflow.get("batch")
    if not isinstance(batch, Mapping) or not batch.get("enabled", False):
        return ()
    excluded_tags = {str(tag) for tag in batch.get("exclude_tags", ())}
    devices = []
    for element in list_elements(context, kind="rf"):
        if not {"llrf", "wrapped_phase"}.issubset(element.tags):
            continue
        if excluded_tags.intersection(element.tags):
            continue
        try:
            target = resolve_write_target(
                context, element.id, logical_channel="phase_set", unit="deg"
            )
            readback = resolve_channel(context, element.id, phase_readback_channel)
        except MachineProfileError:
            continue
        devices.append(
            RFPhaseBatchDevice(
                element_id=element.id,
                display_name=element.display_name,
                order=element.order,
                target=target,
                phase_pv=target.pv_name,
                phase_readback_pv=readback,
            )
        )
    return tuple(sorted(devices, key=lambda item: (item.order, item.element_id)))


@dataclass(frozen=True)
class RFPhaseBatchTask:
    device: RFPhaseBatchDevice
    request: RFPhaseScanRequest


@dataclass(frozen=True)
class RFPhaseBatchResult:
    status: str
    directory: str
    records: tuple[Mapping[str, Any], ...]
    error: str = ""


@dataclass(frozen=True)
class RFPhaseBatchCallbacks:
    item: Callable[[int, Mapping[str, Any]], None] = field(
        default=lambda _index, _record: None
    )
    point: Callable[[dict[str, Any]], None] = field(default=lambda _point: None)
    diagnostic: Callable[[Any, float, dict[str, Any]], None] = field(
        default=lambda _image, _energy, _quality: None
    )
    scan_state: Callable[[str], None] = field(default=lambda _state: None)
    match_state: Callable[[dict[str, Any]], None] = field(
        default=lambda _payload: None
    )
    scan_finished: Callable[[dict[str, Any]], None] = field(
        default=lambda _result: None
    )


class RFPhaseBatchRunner:
    """Synchronous batch state machine; one QThread adapter owns it in the GUI."""

    def __init__(
        self,
        *,
        context,
        devices: tuple[RFPhaseBatchDevice, ...],
        tasks: tuple[RFPhaseBatchTask, ...],
        quality: CrestQualitySettings,
        directory: Path,
        callbacks: RFPhaseBatchCallbacks | None = None,
        executor_factory=RFPhaseScanExecutor,
        read: Callable[[str], Any] = caget,
        lock_factory=EnergyControlLock.for_machine,
    ) -> None:
        self.context = context
        self.devices = tuple(devices)
        self.tasks = tuple(tasks)
        self.quality = quality
        self.directory = Path(directory)
        self.callbacks = callbacks or RFPhaseBatchCallbacks()
        self.executor_factory = executor_factory
        self.read = read
        self.lock_factory = lock_factory
        self.stopping = False
        self.active_executor = None
        self.status = "pending"
        self.error = ""
        self.expected_phases: dict[str, float] = {}
        self.expected_energy: float | None = None
        self.initial_snapshot: dict[str, Any] | None = None
        self.records = [
            {
                "element_id": task.device.element_id,
                "display_name": task.device.display_name,
                "status": "pending",
                "archive": str(
                    self.directory
                    / f"{index + 1:03d}_{task.device.element_id}"
                ),
            }
            for index, task in enumerate(self.tasks)
        ]

    def stop(self) -> None:
        self.stopping = True

    @staticmethod
    def _finite(value, name):
        if value is None:
            raise RuntimeError(f"{name} is unavailable.")
        number = float(value)
        if not math.isfinite(number):
            raise RuntimeError(f"{name} is not finite.")
        return number

    def _write_json(self, path: Path, payload: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(dict(payload), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)

    def _configuration_snapshot(self) -> dict[str, Any]:
        if not self.tasks:
            return {}
        request = self.tasks[0].request
        return {
            "selected_elements": [task.device.element_id for task in self.tasks],
            "candidate_elements": [device.element_id for device in self.devices],
            "phase_scan": asdict(request.settings),
            "point_measurement": dict(request.point_measurement),
            "energy_match": dict(request.scan),
            "phase_verification": dict(request.phase_verification),
            "energy_pv": request.energy_pv,
            "energy_machine_limits_mev": {
                "low": request.energy_machine_low_mev,
                "high": request.energy_machine_high_mev,
            },
            "background_used": bool(request.remove_bg),
            "roi": (
                request.roi.as_dict()
                if request.roi is not None and hasattr(request.roi, "as_dict")
                else None
            ),
        }

    def _save(self) -> None:
        self._write_json(
            self.directory / "batch.json",
            {
                "schema_version": "rf_crest_batch_v1",
                "machine_id": self.context.machine.id,
                "backend": self.context.control_backend.name,
                "status": self.status,
                "error": self.error,
                "updated_at": datetime.now().astimezone().isoformat(
                    timespec="milliseconds"
                ),
                "quality": asdict(self.quality),
                "configuration": self._configuration_snapshot(),
                "initial_snapshot": self.initial_snapshot,
                "items": self.records,
            },
        )

    def _validate_tasks(self) -> None:
        if not self.tasks:
            raise RuntimeError("No RF phase batch tasks were selected.")
        candidate_ids = {device.element_id for device in self.devices}
        selected_ids = [task.device.element_id for task in self.tasks]
        if len(selected_ids) != len(set(selected_ids)):
            raise RuntimeError("RF phase batch contains duplicate LLRF tasks.")
        if not set(selected_ids).issubset(candidate_ids):
            raise RuntimeError("RF phase batch contains an ineligible LLRF task.")
        if selected_ids != [
            task.device.element_id
            for task in sorted(
                self.tasks, key=lambda item: (item.device.order, item.device.element_id)
            )
        ]:
            raise RuntimeError("RF phase batch tasks must follow machine order.")

        reference = self.tasks[0].request
        for task in self.tasks:
            request = task.request
            if request.target.element_id != task.device.element_id:
                raise RuntimeError("RF phase batch target and device do not match.")
            if request.energy_pv != reference.energy_pv:
                raise RuntimeError("RF phase batch tasks must share one energy channel.")
            if (
                asdict(request.settings) != asdict(reference.settings)
                or dict(request.scan) != dict(reference.scan)
                or dict(request.point_measurement)
                != dict(reference.point_measurement)
                or dict(request.phase_verification)
                != dict(reference.phase_verification)
            ):
                raise RuntimeError("RF phase batch tasks must share frozen scan settings.")

        points = int(reference.settings.points)
        if self.quality.min_points_used < 3 or self.quality.min_points_used > points:
            raise RuntimeError(
                "Batch minimum fitted points must be between 3 and the current scan points."
            )
        if not 0 <= self.quality.min_r_squared <= 1:
            raise RuntimeError("Batch minimum R2 must be in [0, 1].")
        if (
            self.quality.crest_edge_margin_steps < 0
            or self.quality.crest_edge_margin_steps * 2 >= points - 1
        ):
            raise RuntimeError("Batch crest edge margin leaves no accepted scan interior.")

    def _snapshot(self) -> tuple[dict[str, Any], float]:
        request = self.tasks[0].request
        tolerance = float(request.phase_verification["tolerance_deg"])
        devices = {}
        expected = {}
        for device in self.devices:
            setpoint = self._finite(
                self.read(device.phase_pv), f"{device.element_id} phase setpoint"
            )
            readback = self._finite(
                self.read(device.phase_readback_pv),
                f"{device.element_id} phase readback",
            )
            if abs(phase_difference_deg(readback, setpoint)) > tolerance:
                raise RuntimeError(
                    f"{device.element_id} phase readback does not match its setpoint."
                )
            devices[device.element_id] = {
                "phase_setpoint_deg": setpoint,
                "phase_readback_deg": readback,
                "phase_pv": device.phase_pv,
                "phase_readback_pv": device.phase_readback_pv,
            }
            expected[device.element_id] = setpoint
        energy = self._finite(
            self.read(request.energy_pv), "Coordinated energy setpoint"
        )
        self.expected_phases = expected
        self.expected_energy = energy
        snapshot = {
            "schema_version": "rf_crest_batch_snapshot_v1",
            "created_at": datetime.now().astimezone().isoformat(
                timespec="milliseconds"
            ),
            "machine_id": self.context.machine.id,
            "backend": self.context.control_backend.name,
            "energy_pv": request.energy_pv,
            "energy_setpoint_mev": energy,
            "devices": devices,
        }
        self.initial_snapshot = snapshot
        for index, task in enumerate(self.tasks):
            self.records[index].update(
                initial_phase_deg=expected[task.device.element_id],
                initial_energy_mev=energy,
            )
            self.callbacks.item(index, dict(self.records[index]))
        self._write_json(self.directory / "initial_snapshot.json", snapshot)
        return devices, energy

    def _check_expected_state(self) -> None:
        request = self.tasks[0].request
        tolerance = float(request.phase_verification["tolerance_deg"])
        for device in self.devices:
            setpoint = self._finite(
                self.read(device.phase_pv), f"{device.element_id} phase setpoint"
            )
            readback = self._finite(
                self.read(device.phase_readback_pv),
                f"{device.element_id} phase readback",
            )
            expected = self.expected_phases[device.element_id]
            if abs(phase_difference_deg(setpoint, expected)) > tolerance:
                raise RuntimeError(
                    f"{device.element_id} phase changed outside this batch: "
                    f"expected {expected:g} deg, setpoint {setpoint:g} deg."
                )
            if abs(phase_difference_deg(readback, expected)) > tolerance:
                raise RuntimeError(
                    f"{device.element_id} phase readback drifted outside this batch: "
                    f"expected {expected:g} deg, readback {readback:g} deg."
                )
        energy = self._finite(
            self.read(request.energy_pv), "Coordinated energy setpoint"
        )
        if self.expected_energy is None or not math.isclose(
            energy, self.expected_energy, abs_tol=0.01, rel_tol=0
        ):
            raise RuntimeError(
                "Coordinated energy changed outside this batch: "
                f"expected {self.expected_energy:g} MeV, actual {energy:g} MeV."
            )

    def _update(self, index: int, **values) -> None:
        self.records[index].update(values)
        self.callbacks.item(index, dict(self.records[index]))
        self._save()

    def _restore_apply_failure(
        self,
        executor: RFPhaseScanExecutor,
        initial_phase: float,
        initial_energy: float,
    ) -> list[str]:
        errors = []
        try:
            executor.set_phase(initial_phase)
        except Exception as exc:
            errors.append(f"phase restore failed: {exc}")
        try:
            executor.set_energy(initial_energy)
        except Exception as exc:
            errors.append(f"energy restore failed: {exc}")
        return errors

    def run(self) -> RFPhaseBatchResult:
        active_index = 0
        lock = self.lock_factory(
            self.context.machine.id,
            {"app": "rf_phase_scan", "operation": "RF Crest Batch"},
        )
        try:
            self.status = "preflight"
            self._save()
            self._validate_tasks()
            require_workflow_write_allowed(
                self.context, "rf_phase_scan", "RF Crest Batch"
            )
            lock.acquire()
            self._snapshot()
            self.status = "running"
            self._save()
            skipped = False
            for active_index, task in enumerate(self.tasks):
                if self.stopping:
                    self.status = "stopped"
                    break
                self._check_expected_state()
                initial_phase = self.expected_phases[task.device.element_id]
                initial_energy = self._finite(
                    self.read(task.request.energy_pv),
                    "Coordinated energy setpoint",
                )
                archive = Path(self.records[active_index]["archive"])
                archive.mkdir(parents=True, exist_ok=True)
                self._update(
                    active_index,
                    status="scanning",
                    initial_phase_deg=initial_phase,
                    initial_energy_mev=initial_energy,
                )
                scan_callbacks = RFPhaseScanCallbacks(
                    point=self.callbacks.point,
                    diagnostic=self.callbacks.diagnostic,
                    state=self.callbacks.scan_state,
                    match_state=self.callbacks.match_state,
                )
                executor = self.executor_factory(
                    task.request,
                    cancel_requested=lambda: self.stopping,
                    callbacks=scan_callbacks,
                    runs_dir=archive,
                    manage_lock=False,
                )
                self.active_executor = executor
                result = executor.run()
                self.callbacks.scan_finished(dict(result))
                self.active_executor = None
                scan_status = str(result.get("status", "FAILED"))
                self.records[active_index].update(
                    scan_status=scan_status,
                    csv_path=result.get("csv_path"),
                    json_path=result.get("json_path"),
                )
                if scan_status == "CANCELLED" and self.stopping:
                    if not result.get("phase_restored") or not result.get("energy_restored"):
                        raise RuntimeError(
                            result.get("message")
                            or f"{task.device.element_id} did not restore after Stop."
                        )
                    self._update(active_index, status="stopped")
                    self.status = "stopped"
                    break
                if (
                    scan_status != "DONE"
                    or not result.get("phase_restored")
                    or not result.get("energy_restored")
                ):
                    raise RuntimeError(
                        result.get("message")
                        or f"{task.device.element_id} scan or restoration failed."
                    )
                self._check_expected_state()
                energy_low = task.request.energy_machine_low_mev
                energy_high = task.request.energy_machine_high_mev
                if energy_low is None:
                    energy_low = task.request.settings.energy_low_mev
                if energy_high is None:
                    energy_high = task.request.settings.energy_high_mev
                assessment = evaluate_crest_quality(
                    result,
                    self.quality,
                    energy_low_mev=energy_low,
                    energy_high_mev=energy_high,
                )
                quality_payload = asdict(assessment)
                quality_payload["reasons"] = list(assessment.reasons)
                self.records[active_index]["quality"] = quality_payload
                if not assessment.accepted:
                    skipped = True
                    self._update(
                        active_index,
                        status="skipped",
                        message="; ".join(assessment.reasons),
                    )
                    continue
                if self.stopping:
                    self._update(active_index, status="stopped")
                    self.status = "stopped"
                    break
                assert assessment.crest_phase_deg is not None
                assert assessment.crest_energy_mev is not None
                self._update(active_index, status="applying")
                try:
                    executor.set_phase(assessment.crest_phase_deg)
                    executor.set_energy(assessment.crest_energy_mev)
                except Exception as exc:
                    restore_errors = self._restore_apply_failure(
                        executor, initial_phase, initial_energy
                    )
                    detail = str(exc)
                    if restore_errors:
                        detail += "; " + "; ".join(restore_errors)
                    self._update(
                        active_index,
                        status=("restore_failed" if restore_errors else "failed"),
                        error=detail,
                    )
                    raise RuntimeError(detail) from exc
                self.expected_phases[task.device.element_id] = (
                    assessment.crest_phase_deg
                )
                self.expected_energy = assessment.crest_energy_mev
                self._update(
                    active_index,
                    status="applied",
                    applied_phase_deg=assessment.crest_phase_deg,
                    applied_energy_mev=assessment.crest_energy_mev,
                )
            else:
                self.status = "completed_with_skips" if skipped else "completed"
        except Exception as exc:
            self.status = "failed"
            self.error = str(exc)
            if self.records and self.records[active_index]["status"] in {
                "pending",
                "scanning",
                "applying",
            }:
                self.records[active_index].update(status="failed", error=self.error)
                self.callbacks.item(active_index, dict(self.records[active_index]))
        finally:
            self.active_executor = None
            for index, record in enumerate(self.records):
                if record["status"] == "pending":
                    record["status"] = "not_run"
                    self.callbacks.item(index, dict(record))
            try:
                self._save()
            finally:
                lock.release()
        return RFPhaseBatchResult(
            self.status, str(self.directory), tuple(self.records), self.error
        )


class RFPhaseBatchThread(QThread):
    item = pyqtSignal(int, dict)
    point = pyqtSignal(dict)
    diagnostic = pyqtSignal(object, float, dict)
    scan_state = pyqtSignal(str)
    match_state = pyqtSignal(dict)
    scan_finished = pyqtSignal(dict)
    done = pyqtSignal(dict)

    def __init__(self, runner_factory):
        super().__init__()
        callbacks = RFPhaseBatchCallbacks(
            item=self.item.emit,
            point=self.point.emit,
            diagnostic=self.diagnostic.emit,
            scan_state=self.scan_state.emit,
            match_state=self.match_state.emit,
            scan_finished=self.scan_finished.emit,
        )
        self.runner = runner_factory(callbacks)

    def stop(self):
        self.runner.stop()

    def run(self):
        self.done.emit(asdict(self.runner.run()))


class RFPhaseBatchHistoryDialog(QDialog):
    COLUMNS = ("LLRF", "Fitted Crest", "Applied", "Gain", "R²", "Status")

    def __init__(self, history: BatchHistory, parent=None):
        super().__init__(parent)
        self.setModal(True)
        self.setWindowTitle("Historical RF Crest Batch")
        self.resize(900, 520)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(9)

        identity = " / ".join(
            value for value in (history.machine_id, history.backend) if value
        ) or "Unknown source"
        detail = (
            f"Historical result · read-only\n{identity} · "
            f"{history.status.replace('_', ' ').title()} · "
            f"updated {history.updated_at or '—'}\n{history.path}"
        )
        if history.error:
            detail += f"\nError: {history.error}"
        summary = QLabel(detail, self)
        summary.setWordWrap(True)
        layout.addWidget(summary)

        table = QTableWidget(len(history.items), len(self.COLUMNS), self)
        table.setHorizontalHeaderLabels(self.COLUMNS)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setAlternatingRowColors(True)
        table.verticalHeader().hide()
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        table.horizontalHeader().setStretchLastSection(True)
        for row, item in enumerate(history.items):
            values = (
                item.display_name,
                self._number(item.crest_phase_deg, "°"),
                self._number(item.applied_phase_deg, "°"),
                self._number(item.energy_gain_mev, " MeV"),
                self._number(item.r_squared, precision=4),
                item.status.replace("_", " ").title(),
            )
            for column, value in enumerate(values):
                table.setItem(row, column, QTableWidgetItem(value))
            notes = list(item.reasons)
            if item.json_path is not None:
                notes.append(f"Scan JSON: {item.json_path}")
            if item.accepted is False and not item.reasons:
                notes.append("Fit was not accepted by batch quality checks.")
            tooltip = "\n".join(notes)
            for column in range(len(self.COLUMNS)):
                table.item(row, column).setToolTip(tooltip)
        layout.addWidget(table, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Close, self)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def _number(value, suffix="", precision=2):
        if value is None:
            return "—"
        return f"{float(value):.{precision}f}{suffix}"


class RFPhaseBatchDialog(QDialog):
    COLUMNS = (
        "Use",
        "LLRF",
        "Initial",
        "Crest",
        "Crest Energy",
        "Gain",
        "R²",
        "Applied",
        "Status",
    )

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.workflow = dict(window.config)
        self.batch_config = dict(self.workflow.get("batch", {}))
        self.devices = discover_batch_devices(
            window.context,
            self.workflow,
            str(window.phase_verification["channel"]),
        )
        self.worker = None
        self.close_when_done = False
        self.setModal(True)
        self.setWindowTitle("RF Crest Batch")
        self.resize(1120, 650)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(9)
        self.summary = QLabel(self)
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        choices = QHBoxLayout()
        self.all_button = QPushButton("All", self)
        self.clear_button = QPushButton("Clear", self)
        choices.addWidget(self.all_button)
        choices.addWidget(self.clear_button)
        choices.addStretch(1)
        layout.addLayout(choices)
        self.table = QTableWidget(len(self.devices), len(self.COLUMNS), self)
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        for row, device in enumerate(self.devices):
            use = QTableWidgetItem("")
            use.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
            use.setCheckState(Qt.Checked)
            self.table.setItem(row, 0, use)
            self.table.setItem(row, 1, QTableWidgetItem(device.display_name))
            for column in range(2, len(self.COLUMNS)):
                self.table.setItem(row, column, QTableWidgetItem("—"))
        layout.addWidget(self.table, 1)
        self.progress = QProgressBar(self)
        self.progress.setRange(0, len(self.devices))
        self.progress.setValue(0)
        layout.addWidget(self.progress)
        actions = QHBoxLayout()
        self.start_button = QPushButton("Start Selected", self)
        self.stop_button = QPushButton("Stop", self)
        self.history_button = QPushButton("View History...", self)
        self.close_button = QPushButton("Close", self)
        self.stop_button.setEnabled(False)
        actions.addWidget(self.start_button)
        actions.addWidget(self.stop_button)
        actions.addWidget(self.history_button)
        actions.addStretch(1)
        actions.addWidget(self.close_button)
        layout.addLayout(actions)
        self.all_button.clicked.connect(lambda: self._select(True))
        self.clear_button.clicked.connect(lambda: self._select(False))
        self.table.itemChanged.connect(lambda _item: self._update_summary())
        self.start_button.clicked.connect(self._start)
        self.history_button.clicked.connect(self._show_history)
        self.stop_button.clicked.connect(self._stop)
        self.close_button.clicked.connect(self.close)
        self._update_summary()

    def _rows(self):
        return [
            row
            for row in range(len(self.devices))
            if self.table.item(row, 0).checkState() == Qt.Checked
        ]

    def _select(self, selected):
        self.table.blockSignals(True)
        for row in range(len(self.devices)):
            self.table.item(row, 0).setCheckState(
                Qt.Checked if selected else Qt.Unchecked
            )
        self.table.blockSignals(False)
        self._update_summary()

    def _update_summary(self):
        rows = self._rows()
        points = self.window.points.value()
        per_point = (
            self.window.settle.value()
            + self.window.measurement_samples.value()
            * self.window.measurement_interval.value()
            + self.window.auto_tune_settle.value()
        )
        center_transition = 0.0
        if (
            self.window.phase_mode.currentData() == "relative"
            and self.window.low.value() < 0 < self.window.high.value()
        ):
            center_transition = (
                self.window.settle.value()
                + 2.0 * self.window.auto_tune_settle.value()
            )
        minutes = len(rows) * (points * per_point + center_transition) / 60.0
        self.summary.setText(
            f"{len(rows)} selected · approximately {minutes:.1f} min · "
            "qualified results will be applied to crest and LINAC_ENERGY."
        )
        self.start_button.setEnabled(bool(rows) and self.worker is None)

    def _set_running(self, running):
        self.table.setEnabled(not running)
        self.all_button.setEnabled(not running)
        self.clear_button.setEnabled(not running)
        self.start_button.setEnabled(not running and bool(self._rows()))
        self.history_button.setEnabled(not running)
        self.stop_button.setEnabled(running)
        self.close_button.setEnabled(not running)
        self.window._set_batch_running(running)

    def _show_history(self):
        latest = latest_batch_history_path(self.window.runtime_paths["runs_dir"])
        start = latest or self.window.runtime_paths["runs_dir"]
        path, _ = QFileDialog.getOpenFileName(
            self,
            "View Historical RF Crest Batch",
            str(start),
            "RF Crest Batch (batch.json);;JSON files (*.json)",
        )
        if not path:
            return
        try:
            history = load_batch_history(path)
        except RFPhaseHistoryError as exc:
            QMessageBox.warning(self, "Historical RF Crest Batch", str(exc))
            return
        RFPhaseBatchHistoryDialog(history, self).exec_()

    def _start(self):
        rows = self._rows()
        if not rows or self.worker is not None or self.window.thread is not None:
            return
        quality = CrestQualitySettings.from_mapping(
            self.batch_config.get("quality")
        )
        if quality.min_points_used > self.window.points.value():
            QMessageBox.warning(
                self,
                "RF Crest Batch",
                "Current scan points are fewer than the batch minimum fitted points.",
            )
            return
        if not self.window.preview_current_diagnostic(show_warning=True):
            return
        if QMessageBox.question(
            self,
            "Start RF Crest Batch",
            f"Scan {len(rows)} selected LLRFs in machine order?\n\n"
            "Qualified fits will be written to crest and LINAC_ENERGY will follow.",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        ) != QMessageBox.Yes:
            return
        try:
            tasks = tuple(
                RFPhaseBatchTask(
                    self.devices[row],
                    self.window._build_batch_scan_request(self.devices[row].target),
                )
                for row in rows
            )
        except Exception as exc:
            QMessageBox.warning(self, "RF Crest Batch", str(exc))
            return
        directory = self.window.runtime_paths["runs_dir"] / (
            "batch_"
            + datetime.now().strftime("%Y%m%d_%H%M%S")
            + "_"
            + uuid4().hex[:8]
        )

        def runner_factory(callbacks):
            return RFPhaseBatchRunner(
                context=self.window.context,
                devices=self.devices,
                tasks=tasks,
                quality=quality,
                directory=directory,
                callbacks=callbacks,
            )

        self.progress.setRange(0, len(tasks))
        self.progress.setValue(0)
        self.close_when_done = False
        self.worker = RFPhaseBatchThread(runner_factory)
        self.worker.item.connect(self._item)
        self.worker.point.connect(self.window._batch_point)
        self.worker.diagnostic.connect(self.window._update_diagnostics)
        self.worker.scan_state.connect(self.window._set_scan_status)
        self.worker.match_state.connect(self.window._match_state)
        self.worker.scan_finished.connect(self.window._batch_scan_finished)
        self.worker.done.connect(self._done)
        self._set_running(True)
        self.worker.start()

    def _stop(self):
        if self.worker is not None:
            self.worker.stop()
            self.stop_button.setEnabled(False)
            self.summary.setText("Stopping · waiting for the current scan to restore...")

    @staticmethod
    def _number(value, suffix="", precision=2):
        if value is None:
            return "—"
        try:
            number = float(value)
        except (TypeError, ValueError):
            return "—"
        return "—" if not math.isfinite(number) else f"{number:.{precision}f}{suffix}"

    def _item(self, task_index, record):
        selected_rows = self._rows()
        if task_index >= len(selected_rows):
            return
        row = selected_rows[task_index]
        quality = record.get("quality") or {}
        values = {
            2: self._number(record.get("initial_phase_deg"), "°"),
            3: self._number(quality.get("crest_phase_deg"), "°"),
            4: self._number(quality.get("crest_energy_mev"), " MeV"),
            5: self._number(quality.get("energy_gain_mev"), " MeV"),
            6: self._number(quality.get("r_squared"), precision=4),
            7: self._number(record.get("applied_phase_deg"), "°"),
            8: str(record.get("status", "—")).replace("_", " ").title(),
        }
        for column, value in values.items():
            self.table.item(row, column).setText(value)
        tooltip = record.get("message") or record.get("error") or ""
        self.table.item(row, 8).setToolTip(str(tooltip))
        completed = sum(
            self.table.item(selected_row, 8).text()
            not in {"—", "Pending", "Scanning", "Applying"}
            for selected_row in selected_rows
        )
        self.progress.setValue(completed)
        if record.get("status") == "scanning":
            self.window._begin_batch_item(record.get("element_id", ""))

    def _done(self, result):
        worker = self.worker
        self.worker = None
        self._set_running(False)
        self.progress.setValue(self.progress.maximum())
        status = str(result.get("status", "failed")).replace("_", " ").title()
        detail = result.get("error") or result.get("directory", "")
        self.summary.setText(f"{status} · {detail}")
        self.window._set_scan_status(status, detail)
        if worker is not None:
            worker.deleteLater()
        if self.close_when_done:
            self.close_when_done = False
            self.close()

    def closeEvent(self, event):
        if self.worker is not None and self.worker.isRunning():
            self.close_when_done = True
            self._stop()
            event.ignore()
            return
        super().closeEvent(event)
