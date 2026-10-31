from __future__ import annotations

import math
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Mapping

import epics
from epics import PV, caget, caput

from half_linac.src.shared.energy_tuning import normalize_pipeline
from half_linac.src.shared.energy_tuning.screen import (
    ScreenEnergyMatcher,
    ScreenImageAcquisition,
    aggregate_frame_energies,
    frame_energies_mev,
)
from half_linac.src.shared.machine_profile import (
    EnergyControlLock,
    TimingControlLock,
    require_workflow_write_allowed,
)
from half_linac.src.shared.machine_profile.app_runtime import resolve_app_runtime_paths

from .model import DEVICES
from .profile_runtime import TimingGroup
from .scan import (
    TimingEnergyMatchResult,
    TimingEnergyScanResult,
    TimingEnergyScanner,
    TimingEnergyScanSettings,
)
from .scan_log import TimingEnergyScanLog


@dataclass(frozen=True)
class TimingEnergyScanRequest:
    context: Any
    group: TimingGroup
    settings: TimingEnergyScanSettings
    scan: Mapping[str, Any]
    image_pv: Any
    image_shape: tuple[int, int]
    pixel_width_mm: float
    energy_pv: str
    remove_bg: bool = False
    bg_image: Any = None
    background_metadata: Mapping[str, Any] = field(default_factory=dict)
    point_measurement: Mapping[str, Any] = field(default_factory=dict)
    roi: Any = None
    readback_tolerance_us: float = 0.001
    readback_timeout_s: float = 2.0
    readback_poll_interval_s: float = 0.05


@dataclass(frozen=True)
class TimingEnergyScanCallbacks:
    point: Callable[[dict[str, Any]], None] = field(default=lambda _payload: None)
    diagnostic: Callable[[Any, float, dict[str, Any]], None] = field(
        default=lambda _image, _energy, _quality: None
    )
    state: Callable[[str], None] = field(default=lambda _state: None)
    progress: Callable[[int, int], None] = field(
        default=lambda _completed, _total: None
    )
    match_state: Callable[[dict[str, Any]], None] = field(
        default=lambda _payload: None
    )


class TimingEnergyScanExecutor:
    """EPICS adapter, logging, and locks for one linked timing-energy scan."""

    def __init__(
        self,
        request: TimingEnergyScanRequest,
        *,
        cancel_requested: Callable[[], bool] | None = None,
        callbacks: TimingEnergyScanCallbacks | None = None,
        runs_dir: Path | None = None,
        manage_locks: bool = True,
        read_pv: Callable[[str], Any] = caget,
        write_pv: Callable[..., Any] = caput,
        write_many: Callable[..., Any] = epics.caput_many,
        matcher_factory: Callable[..., Any] = ScreenEnergyMatcher,
    ) -> None:
        self.request = request
        self.cancel_requested = cancel_requested or (lambda: False)
        self.callbacks = callbacks or TimingEnergyScanCallbacks()
        self.runs_dir = None if runs_dir is None else Path(runs_dir)
        self.manage_locks = bool(manage_locks)
        self.read_pv = read_pv
        self.write_pv = write_pv
        self.write_many = write_many
        self.matcher_factory = matcher_factory
        self.scan = dict(request.scan)
        self.point_measurement = dict(request.point_measurement)
        self._match_count = 0
        self._created_image_pv = False
        if hasattr(request.image_pv, "get"):
            self.image_pv = request.image_pv
        else:
            self.image_pv = PV(str(request.image_pv))
            self._created_image_pv = True
        self.acquisition = ScreenImageAcquisition(
            self.image_pv,
            request.image_shape,
            request.pixel_width_mm,
            background=request.bg_image if request.remove_bg else None,
            roi=request.roi,
            flip_y=bool(self.scan.get("image_flip_y", False)),
            beam_presence_sigma=float(self.scan.get("beam_presence_sigma", 6.0)),
            beam_presence_min_area_px=int(
                self.scan.get("beam_presence_min_area_px", 50)
            ),
        )
        self._log_metadata = {
            "machine_id": request.context.machine.id,
            "backend": request.context.control_backend.name,
            "group": request.group.element_id,
        }

    def run(self) -> TimingEnergyScanResult:
        timing_lock = None
        energy_lock = None
        log = None
        try:
            epics.ca.use_initial_context()
            require_workflow_write_allowed(
                self.request.context,
                "power_source_timing",
                "Linked Timing Energy Scan",
            )
            if tuple(self.request.group.devices) != DEVICES:
                raise RuntimeError(
                    "Linked scan requires HV, LLRF, SSA, and KLY timing channels."
                )
            if self.manage_locks:
                timing_lock = TimingControlLock.for_machine(
                    self.request.context.machine.id,
                    {
                        "app": "power_source_timing",
                        "operation": "Linked Timing Energy Scan",
                    },
                )
                timing_lock.acquire()
                energy_lock = EnergyControlLock.for_machine(
                    self.request.context.machine.id,
                    {
                        "app": "power_source_timing",
                        "operation": "Linked Timing Energy Scan",
                    },
                )
                energy_lock.acquire()
            self._verify_triggers_enabled()
            runs_dir = self.runs_dir
            if runs_dir is None:
                runs_dir = resolve_app_runtime_paths(
                    Path(__file__).resolve().parent,
                    self.request.context,
                )["runs_dir"]
            metadata = {
                **self._log_metadata,
                "energy_pv": self.request.energy_pv,
                "image_shape": list(self.request.image_shape),
                "pixel_width_mm": self.request.pixel_width_mm,
                "background_used": self.request.remove_bg,
                "background": dict(self.request.background_metadata),
                "roi": (
                    self.request.roi.as_dict()
                    if self.request.roi is not None
                    else None
                ),
                "settings": {
                    **self.request.settings.__dict__,
                    "point_measurement": dict(self.point_measurement),
                    "energy_match": dict(self.scan),
                },
            }
            log = TimingEnergyScanLog.create(runs_dir, metadata)
            scanner = TimingEnergyScanner(
                self.request.settings,
                read_delays=self.read_delays,
                set_delays=self.set_delays,
                read_energy=self.read_energy,
                set_energy=self.set_energy,
                write_center_energy=self.write_energy,
                match_energy=self._match,
                cancel_requested=self.cancel_requested,
                progress_callback=lambda payload: self._progress(payload, log),
            )
            result = scanner.run()
            result = replace(
                result,
                csv_path=str(log.csv_path),
                json_path=str(log.json_path),
            )
            log.finish(result.to_mapping())
            return result
        except Exception as exc:
            result = TimingEnergyScanResult(
                status="FAILED",
                initial_delays_us={},
                initial_energy_mev=None,
                points=(),
                best_point=None,
                timing_restored=False,
                energy_restored=False,
                restore_errors={},
                message=str(exc),
                csv_path=str(log.csv_path) if log is not None else None,
                json_path=str(log.json_path) if log is not None else None,
            )
            if log is not None:
                log.finish(result.to_mapping())
            return result
        finally:
            if log is not None:
                log.close()
            if energy_lock is not None:
                energy_lock.release()
            if timing_lock is not None:
                timing_lock.release()
            if self._created_image_pv:
                try:
                    self.image_pv.disconnect()
                except Exception:
                    pass

    def _verify_triggers_enabled(self) -> None:
        disabled = []
        unreadable = []
        for device in DEVICES:
            raw = self.read_pv(self.request.group.pv(device, "enable"))
            try:
                enabled = bool(round(float(raw)))
            except (TypeError, ValueError, OverflowError):
                unreadable.append(device.upper())
                continue
            if not enabled:
                disabled.append(device.upper())
        if unreadable:
            raise RuntimeError(
                "Trigger state is unreadable: " + ", ".join(unreadable)
            )
        if disabled:
            raise RuntimeError(
                "All four timing triggers must already be enabled: "
                + ", ".join(disabled)
            )

    def read_delays(self) -> dict[str, float]:
        values = {}
        for device in DEVICES:
            raw = self.read_pv(self.request.group.pv(device, "delay_set"))
            try:
                value = float(raw)
            except (TypeError, ValueError) as exc:
                raise RuntimeError(
                    f"{device.upper()} delay setpoint is unreadable."
                ) from exc
            if not math.isfinite(value):
                raise RuntimeError(
                    f"{device.upper()} delay setpoint is not finite."
                )
            values[device] = value
        return values

    def read_energy(self) -> float:
        raw = self.read_pv(self.request.energy_pv)
        try:
            value = float(raw)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("Coordinated energy setpoint is unreadable.") from exc
        if not math.isfinite(value):
            raise RuntimeError("Coordinated energy setpoint is not finite.")
        return value

    def set_delays(
        self,
        targets_us: Mapping[str, float],
    ) -> dict[str, float]:
        names = [
            self.request.group.pv(device, "delay_set") for device in DEVICES
        ]
        values = [float(targets_us[device]) for device in DEVICES]
        results = self.write_many(
            names,
            values,
            wait="all",
            connection_timeout=2.0,
            put_timeout=5.0,
        )
        failed = [
            device
            for index, device in enumerate(DEVICES)
            if index >= len(results) or results[index] != 1
        ]
        if failed:
            raise RuntimeError(
                "Timing write failed: " + ", ".join(item.upper() for item in failed)
            )

        deadline = time.monotonic() + float(self.request.readback_timeout_s)
        latest: dict[str, float] = {}
        while True:
            latest.clear()
            all_matched = True
            for device in DEVICES:
                raw = self.read_pv(
                    self.request.group.pv(device, "delay_readback")
                )
                try:
                    actual = float(raw)
                except (TypeError, ValueError):
                    actual = math.nan
                if math.isfinite(actual):
                    latest[device] = actual
                if (
                    not math.isfinite(actual)
                    or abs(actual - float(targets_us[device]))
                    > float(self.request.readback_tolerance_us)
                ):
                    all_matched = False
            if all_matched:
                return dict(latest)
            if time.monotonic() >= deadline:
                details = ", ".join(
                    f"{device.upper()}="
                    + (
                        f"{latest[device]:g}"
                        if device in latest
                        else "unavailable"
                    )
                    for device in DEVICES
                )
                raise RuntimeError(
                    "Timing readback verification failed: "
                    f"{details}; tolerance={self.request.readback_tolerance_us:g} us."
                )
            time.sleep(
                min(
                    float(self.request.readback_poll_interval_s),
                    max(deadline - time.monotonic(), 0.0),
                )
            )

    def write_energy(self, value: float) -> None:
        target = float(value)
        if not self.write_pv(
            self.request.energy_pv,
            target,
            wait=True,
            timeout=10,
        ):
            raise RuntimeError("Coordinated energy setpoint write failed.")
        deadline = time.monotonic() + float(self.request.readback_timeout_s)
        tolerance = max(1e-6, abs(target) * 1e-9)
        while True:
            raw = self.read_pv(self.request.energy_pv)
            try:
                actual = float(raw)
            except (TypeError, ValueError):
                actual = math.nan
            if math.isfinite(actual) and abs(actual - target) <= tolerance:
                return
            if time.monotonic() >= deadline:
                rendered = f"{actual:g}" if math.isfinite(actual) else "unavailable"
                raise RuntimeError(
                    "Coordinated energy setpoint verification failed: "
                    f"target={target:g}, actual={rendered}."
                )
            time.sleep(
                min(
                    float(self.request.readback_poll_interval_s),
                    max(deadline - time.monotonic(), 0.0),
                )
            )

    def set_energy(self, value: float) -> None:
        self.write_energy(value)
        settle = max(float(self.request.settings.energy_settle_time_s), 0.0)
        if settle:
            time.sleep(settle)

    def _match(
        self,
        _offset_us: float,
        center: float,
        low: float,
        high: float,
        _attempt: int,
    ) -> TimingEnergyMatchResult:
        scan = dict(self.scan)
        scan.update(min=float(low), max=float(high), start_energy=float(center))
        scan["frame_samples"] = int(self.point_measurement["samples"])
        scan["min_valid_frames"] = int(
            self.point_measurement["min_valid_samples"]
        )
        scan["frame_interval_s"] = float(self.point_measurement["interval_s"])
        pipeline_name = "initial" if self._match_count == 0 else "tracking"
        configured_pipelines = dict(self.scan.get("pipelines", {}))
        pipeline = configured_pipelines.get(pipeline_name)
        if pipeline is None:
            pipeline = self.scan.get(f"{pipeline_name}_pipeline")
        objective = str(
            self.scan.get(f"{pipeline_name}_objective", "profile_lock")
        )
        scan["pipeline"] = normalize_pipeline(
            pipeline,
            legacy_objective=objective,
        )
        scan["objective"] = objective
        self._match_count += 1

        tuner = self.matcher_factory(
            flag_pv_obj=self.image_pv,
            flag_pixel=self.request.image_shape,
            bend_pv=self.request.energy_pv,
            design_eta_m=float(scan["design_eta_m"]),
            progress_callback=self._match_progress,
            remove_bg=self.request.remove_bg,
            bg_image=self.request.bg_image,
            roi=self.acquisition.roi,
            flip_y=bool(scan.get("image_flip_y", False)),
            settle_time_s=float(scan.get("settle_time_s", 3.0)),
            restore_initial_on_failure=bool(
                scan.get("restore_initial_on_failure", True)
            ),
            cancel_requested=self.cancel_requested,
            restore_initial_on_cancel=True,
            frame_samples=int(scan["frame_samples"]),
            min_valid_frames=int(scan["min_valid_frames"]),
            frame_interval_s=float(scan["frame_interval_s"]),
            pixel_width_mm=float(self.request.pixel_width_mm),
            profile_fit_method=str(
                scan.get("profile_fit_method", "Gauss fit")
            ),
            min_fit_r_squared=float(scan.get("min_fit_r_squared", 0.3)),
            beam_presence_sigma=float(scan.get("beam_presence_sigma", 6.0)),
            beam_presence_min_area_px=int(
                scan.get("beam_presence_min_area_px", 50)
            ),
            x_reference_mm=float(scan.get("x_reference_mm", 0.0)),
            center_lock_trigger_mm=scan.get("center_lock_trigger_mm"),
            center_tolerance_mm=float(
                scan.get("center_tolerance_mm", 0.2)
            ),
            max_iterations=int(scan.get("max_iterations", 6)),
            max_correction_step_mev=float(
                scan.get("max_correction_step_mev", 20.0)
            ),
            objective=objective,
            pipeline=scan["pipeline"],
        )
        best = tuner.run(
            B_min=float(low),
            B_max=float(high),
            start_energy=float(center),
            center_lock_config=dict(
                scan.get("stages", {}).get(
                    "center_lock",
                    scan.get("center_lock", {}),
                )
            ),
        )
        info = dict(tuner.center_lock_result or {})
        measurement = tuner.result_measurement
        if best is not None and measurement is not None:
            energy_setpoint = float(measurement["energy"])
            frame_energy = frame_energies_mev(
                energy_setpoint,
                measurement.get("frame_centers_mm"),
                scan.get("x_reference_mm", 0.0),
                scan["design_eta_m"],
            )
            statistics = aggregate_frame_energies(
                frame_energy,
                min_frames=int(self.point_measurement["min_valid_samples"]),
            )
            if statistics is None:
                return TimingEnergyMatchResult(
                    False,
                    "MEASUREMENT_FAILED",
                    message="No finite per-frame energy remained after measurement.",
                )
            beam_energy = float(statistics["mean_mev"])
            diagnostics = {
                key: value
                for key, value in {
                    **info,
                    **dict(tuner.acquisition.last_diagnostic or {}),
                    "center_offset_mm": (
                        float(measurement["center_mm"])
                        - float(scan.get("x_reference_mm", 0.0))
                    ),
                    "fit_method": measurement.get("fit_method"),
                    "fit_r_squared": measurement.get("fit_r_squared"),
                    "frame_centers_mm": measurement.get("frame_centers_mm"),
                    "frame_energies_mev": frame_energy,
                    "frame_rejection_reasons": measurement.get(
                        "frame_rejection_reasons"
                    ),
                    "frame_fit_methods": measurement.get("frame_fit_methods"),
                    "frame_fit_r_squared": measurement.get(
                        "frame_fit_r_squared"
                    ),
                    "frame_brightness": measurement.get("frame_brightness"),
                }.items()
                if key != "raw_image"
            }
            self.callbacks.diagnostic(
                measurement["raw_image"],
                energy_setpoint,
                {
                    "valid_frames": int(measurement["valid_frames"]),
                    "total_frames": int(measurement["total_frames"]),
                    "fit_r_squared": measurement.get("fit_r_squared"),
                    "center_mm": measurement.get("center_mm"),
                    "beam_energy_mev": beam_energy,
                    "energy_standard_deviation_mev": statistics[
                        "standard_deviation_mev"
                    ],
                },
            )
            return TimingEnergyMatchResult(
                True,
                str(tuner.get_last_status()),
                energy_mev=beam_energy,
                energy_setpoint_mev=energy_setpoint,
                energy_standard_deviation_mev=float(
                    statistics["standard_deviation_mev"]
                ),
                energy_standard_error_mev=float(
                    statistics["standard_error_mev"]
                ),
                brightness=float(measurement["brightness"]),
                valid_frames=int(measurement["valid_frames"]),
                total_frames=int(measurement["total_frames"]),
                message=tuner.get_last_message(),
                diagnostics=diagnostics,
            )

        diagnostic = dict(tuner.acquisition.last_diagnostic or {})
        return TimingEnergyMatchResult(
            False,
            str(tuner.get_last_status()),
            brightness=info.get("brightness"),
            valid_frames=diagnostic.get(
                "valid_frames",
                info.get("valid_frames"),
            ),
            total_frames=diagnostic.get("total_frames"),
            message=tuner.get_last_message(),
            diagnostics={**info, **diagnostic},
        )

    def _match_progress(self, payload: Mapping[str, Any]) -> None:
        update = dict(payload)
        raw_image = update.pop("raw_image", None)
        if raw_image is not None:
            self.callbacks.diagnostic(
                raw_image,
                float(update.get("energy_mev", 0.0)),
                {
                    "valid_frames": update.get("valid_frames"),
                    "total_frames": update.get("total_frames"),
                    "fit_r_squared": update.get("fit_r_squared"),
                    "center_mm": update.get("center_mm"),
                    "match_stage": update.get("stage"),
                },
            )
        self.callbacks.match_state(update)

    def _progress(
        self,
        payload: Mapping[str, Any],
        log: TimingEnergyScanLog,
    ) -> None:
        event = str(payload.get("event", ""))
        if event in {"timing_set", "match_start"}:
            self.callbacks.state("Running")
        elif event == "center_return":
            self.callbacks.state(
                "Returning to center "
                f"({float(payload.get('settle_time_s', 0.0)):g} s)"
            )
        elif event == "restore":
            self.callbacks.state("Restoring")
        if event in {
            "timing_set",
            "match_start",
            "center_return",
            "center_return_verified",
            "restore",
        }:
            log.write(
                event,
                **{
                    key: value
                    for key, value in payload.items()
                    if key != "event"
                },
            )
        if event == "point":
            point = dict(payload.get("point") or {})
            log.record_point(point)
            self.callbacks.point(point)
            self.callbacks.progress(
                int(point.get("acquisition_index", 0)) + 1,
                len(self.request.settings.offsets()),
            )

    def apply_best(
        self,
        result: TimingEnergyScanResult,
    ) -> dict[str, Any]:
        if result.status != "DONE":
            raise RuntimeError("Only a completed scan can apply its best point.")
        if not result.timing_restored or not result.energy_restored:
            raise RuntimeError(
                "Apply Best is disabled because scan-start state was not restored."
            )
        best = result.best_point
        if best is None or best.energy_setpoint_mev is None:
            raise RuntimeError("No valid best point is available.")

        require_workflow_write_allowed(
            self.request.context,
            "power_source_timing",
            "Apply Linked Timing Energy Best",
        )
        timing_lock = None
        energy_lock = None
        payload: dict[str, Any]
        try:
            if self.manage_locks:
                timing_lock = TimingControlLock.for_machine(
                    self.request.context.machine.id,
                    {
                        "app": "power_source_timing",
                        "operation": "Apply Linked Timing Energy Best",
                    },
                )
                timing_lock.acquire()
                energy_lock = EnergyControlLock.for_machine(
                    self.request.context.machine.id,
                    {
                        "app": "power_source_timing",
                        "operation": "Apply Linked Timing Energy Best",
                    },
                )
                energy_lock.acquire()
            self.callbacks.state("Applying best point")
            readbacks = self.set_delays(best.targets_us)
            if self.request.settings.timing_settle_time_s:
                time.sleep(self.request.settings.timing_settle_time_s)
            self.set_energy(float(best.energy_setpoint_mev))
            payload = {
                "status": "APPLIED",
                "offset_us": best.offset_us,
                "targets_us": dict(best.targets_us),
                "readbacks_us": dict(readbacks),
                "matched_energy_mev": best.matched_energy_mev,
                "energy_setpoint_mev": best.energy_setpoint_mev,
            }
        except Exception as exc:
            rollback_errors = {}
            try:
                self.set_delays(result.initial_delays_us)
            except Exception as rollback_exc:
                rollback_errors["timing"] = str(rollback_exc)
            try:
                if result.initial_energy_mev is not None:
                    self.set_energy(result.initial_energy_mev)
            except Exception as rollback_exc:
                rollback_errors["energy"] = str(rollback_exc)
            payload = {
                "status": "APPLY_FAILED",
                "message": str(exc),
                "rollback_errors": rollback_errors,
            }
        finally:
            if energy_lock is not None:
                energy_lock.release()
            if timing_lock is not None:
                timing_lock.release()

        if result.csv_path and result.json_path:
            try:
                TimingEnergyScanLog.append_apply(
                    result.csv_path,
                    result.json_path,
                    self._log_metadata,
                    payload,
                )
            except Exception as exc:
                payload["log_warning"] = (
                    f"Apply result could not be logged: {exc}"
                )
        return payload
