from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from epics import caget, caput

from half_linac.src.apps.rf_phase_scan.energy_match_tuner import RFPhaseEnergyMatcher
from half_linac.src.apps.rf_phase_scan.image_acquisition import RFImageAcquisition
from half_linac.src.apps.rf_phase_scan.phase_energy_scan import (
    EnergyMatchResult,
    PhaseEnergyScanner,
    PhaseScanSettings,
    aggregate_frame_energies,
    wait_for_phase_readback,
)
from half_linac.src.apps.rf_phase_scan.phase_scan_log import PhaseEnergyScanLog
from half_linac.src.shared.energy_tuning import normalize_pipeline
from half_linac.src.shared.machine_profile import (
    EnergyControlLock,
    require_workflow_write_allowed,
)
from half_linac.src.shared.machine_profile.app_runtime import resolve_app_runtime_paths


def frame_energies_mev(
    energy_setpoint: float,
    frame_centers_mm,
    x_reference_mm: float,
    design_eta_m: float,
) -> list[float]:
    if not frame_centers_mm:
        return []
    setpoint = float(energy_setpoint)
    reference = float(x_reference_mm)
    eta = float(design_eta_m)
    return [
        float(setpoint * (1.0 + (float(center) - reference) * 1e-3 / eta))
        for center in frame_centers_mm
    ]


@dataclass(frozen=True)
class RFPhaseScanRequest:
    context: Any
    target: Any
    settings: PhaseScanSettings
    scan: Mapping[str, Any]
    image_pv: Any
    image_shape: tuple[int, int]
    pixel_width_mm: float
    phase_pv: str
    phase_readback_pv: str
    phase_verification: Mapping[str, Any]
    energy_pv: str
    remove_bg: bool
    bg_image: Any
    background_metadata: Mapping[str, Any]
    point_measurement: Mapping[str, Any]
    roi: Any = None
    energy_machine_low_mev: float | None = None
    energy_machine_high_mev: float | None = None


@dataclass(frozen=True)
class RFPhaseScanCallbacks:
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


class RFPhaseScanExecutor:
    """Synchronous, UI-independent execution of one RF phase scan."""

    def __init__(
        self,
        request: RFPhaseScanRequest,
        *,
        cancel_requested: Callable[[], bool] | None = None,
        callbacks: RFPhaseScanCallbacks | None = None,
        runs_dir: Path | None = None,
        manage_lock: bool = True,
    ) -> None:
        self.request = request
        self.cancel_requested = cancel_requested or (lambda: False)
        self.callbacks = callbacks or RFPhaseScanCallbacks()
        self.runs_dir = None if runs_dir is None else Path(runs_dir)
        self.manage_lock = bool(manage_lock)
        self.scan = dict(request.scan)
        self.point_measurement = dict(request.point_measurement)
        self._match_count = 0
        self.acquisition = RFImageAcquisition(
            request.image_pv,
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

    def run(self) -> dict[str, Any]:
        request = self.request
        log = None
        lock = None
        try:
            self.callbacks.state("Preparing")
            require_workflow_write_allowed(
                request.context, "rf_phase_scan", "RF Crest & Gain"
            )
            if self.manage_lock:
                lock = EnergyControlLock.for_machine(
                    request.context.machine.id,
                    {"app": "rf_phase_scan", "operation": "RF Crest & Gain"},
                )
                lock.acquire()
            initial_phase = caget(request.phase_pv)
            initial_energy = caget(request.energy_pv)
            if initial_phase is None or initial_energy is None:
                raise RuntimeError("Initial phase and energy setpoints must be readable.")
            if self.runs_dir is None:
                paths = resolve_app_runtime_paths(
                    Path(__file__).resolve().parent, request.context
                )
                runs_dir = paths["runs_dir"]
            else:
                runs_dir = self.runs_dir
            metadata = dict(request.background_metadata or {})
            log = PhaseEnergyScanLog.create(
                runs_dir,
                {
                    "machine_id": request.context.machine.id,
                    "backend": request.context.control_backend.name,
                    "station_id": "eny",
                    "element_id": request.target.element_id,
                    "phase_pv": request.phase_pv,
                    "phase_readback_pv": request.phase_readback_pv,
                    "energy_pv": request.energy_pv,
                    "initial_phase_deg": float(initial_phase),
                    "initial_energy_mev": float(initial_energy),
                    "background_used": request.remove_bg,
                    "background_path": metadata.get("path"),
                    "background_shape": metadata.get("shape"),
                    "roi_enabled": self.acquisition.roi is not None,
                    "roi": (
                        self.acquisition.roi.as_dict()
                        if self.acquisition.roi is not None
                        else None
                    ),
                    "phase_mode": request.settings.phase_mode,
                    "point_measurement": dict(self.point_measurement),
                    "measurement_samples": self.point_measurement["samples"],
                    "measurement_interval_s": self.point_measurement["interval_s"],
                    "measurement_min_valid_samples": self.point_measurement[
                        "min_valid_samples"
                    ],
                    "energy_match_settings": dict(self.scan),
                },
            )
            scanner = PhaseEnergyScanner(
                settings=request.settings,
                read_phase=lambda: caget(request.phase_pv),
                set_phase=self.set_phase,
                read_energy=lambda: caget(request.energy_pv),
                set_energy=self.set_energy,
                match_energy=self._match,
                set_center_energy=self.write_energy,
                center_return_settle_time_s=2.0 * self.energy_settle_time_s(),
                cancel_requested=self.cancel_requested,
                progress_callback=lambda payload: self._progress(payload, log),
            )
            result = scanner.run().to_mapping()
            result["csv_path"] = str(log.csv_path)
            result["json_path"] = str(log.json_path)
            log.finish(result)
            return result
        except Exception as exc:
            return {
                "status": "FAILED",
                "message": str(exc),
                "points": [],
                "fit": None,
                "phase_restored": None,
                "energy_restored": None,
            }
        finally:
            if log is not None:
                log.close()
            if lock is not None:
                lock.release()

    def set_phase(self, value: float) -> None:
        request = self.request
        if not caput(request.phase_pv, float(value), wait=True, timeout=5):
            raise RuntimeError("LLRF phase setpoint write failed.")
        wait_for_phase_readback(
            lambda: caget(request.phase_readback_pv),
            float(value),
            tolerance_deg=float(request.phase_verification["tolerance_deg"]),
            timeout_s=float(request.phase_verification["timeout_s"]),
            poll_interval_s=float(request.phase_verification["poll_interval_s"]),
        )

    def energy_settle_time_s(self) -> float:
        return max(float(self.scan.get("settle_time_s", 3.0)), 0.0)

    def write_energy(self, value: float) -> None:
        request = self.request
        if not caput(request.energy_pv, float(value), wait=True, timeout=10):
            raise RuntimeError("Coordinated energy setpoint write failed.")

    def set_energy(self, value: float) -> None:
        self.write_energy(value)
        settle_time_s = self.energy_settle_time_s()
        if settle_time_s:
            time.sleep(settle_time_s)

    def _match(self, center: float, low: float, high: float, attempt: int):
        request = self.request
        scan = dict(self.scan)
        scan.update(min=float(low), max=float(high), start_energy=float(center))
        scan["frame_samples"] = int(self.point_measurement["samples"])
        scan["min_valid_frames"] = int(
            self.point_measurement["min_valid_samples"]
        )
        scan["frame_interval_s"] = float(self.point_measurement["interval_s"])
        pipeline_name = "initial" if self._match_count == 0 else "tracking"
        configured_pipelines = self.scan.get("pipelines", {})
        scan["pipeline"] = configured_pipelines.get(pipeline_name)
        if scan["pipeline"] is None:
            scan["pipeline"] = self.scan.get(f"{pipeline_name}_pipeline")
        scan["objective"] = str(
            self.scan.get(f"{pipeline_name}_objective", "profile_lock")
        )
        self._match_count += 1
        scan["pipeline"] = normalize_pipeline(
            scan.get("pipeline"), legacy_objective=scan["objective"]
        )
        tuner = RFPhaseEnergyMatcher(
            flag_pv_obj=request.image_pv,
            flag_pixel=request.image_shape,
            bend_pv=request.energy_pv,
            design_eta_m=float(scan["design_eta_m"]),
            progress_callback=self._match_progress,
            remove_bg=request.remove_bg,
            bg_image=request.bg_image,
            roi=self.acquisition.roi,
            flip_y=bool(scan.get("image_flip_y", False)),
            settle_time_s=float(scan.get("settle_time_s", 3)),
            restore_initial_on_failure=bool(
                scan.get("restore_initial_on_failure", True)
            ),
            cancel_requested=self.cancel_requested,
            restore_initial_on_cancel=True,
            frame_samples=int(scan.get("frame_samples", 3)),
            min_valid_frames=int(scan.get("min_valid_frames", 2)),
            frame_interval_s=float(scan.get("frame_interval_s", 0.2)),
            pixel_width_mm=float(request.pixel_width_mm),
            profile_fit_method=str(scan.get("profile_fit_method", "Gauss fit")),
            min_fit_r_squared=float(scan.get("min_fit_r_squared", 0.7)),
            beam_presence_sigma=float(scan.get("beam_presence_sigma", 6.0)),
            beam_presence_min_area_px=int(
                scan.get("beam_presence_min_area_px", 50)
            ),
            x_reference_mm=float(scan.get("x_reference_mm", 0.0)),
            center_lock_trigger_mm=scan.get("center_lock_trigger_mm"),
            center_tolerance_mm=float(scan.get("center_tolerance_mm", 0.2)),
            max_iterations=int(scan.get("max_iterations", 6)),
            max_correction_step_mev=float(
                scan.get("max_correction_step_mev", 25)
            ),
            objective=str(scan.get("objective", "profile_lock")),
            pipeline=scan.get("pipeline"),
        )
        best = tuner.run(
            B_min=float(low),
            B_max=float(high),
            start_energy=float(center),
            center_lock_config=dict(
                scan.get("stages", {}).get(
                    "center_lock", scan.get("center_lock", {})
                )
            ),
        )
        info = tuner.center_lock_result or {}
        measurement = tuner.result_measurement
        if best is not None and measurement is not None:
            energy_setpoint = float(measurement["energy"])
            frame_energy = frame_energies_mev(
                energy_setpoint,
                measurement.get("frame_centers_mm"),
                scan.get("x_reference_mm", 0.0),
                scan["design_eta_m"],
            )
            center_offset_mm = float(measurement["center_mm"]) - float(
                scan.get("x_reference_mm", 0.0)
            )
            energy_statistics = aggregate_frame_energies(frame_energy, min_frames=1)
            if energy_statistics is None:
                return EnergyMatchResult(
                    False,
                    "MEASUREMENT_FAILED",
                    message="No finite per-frame energy remained after measurement.",
                )
            beam_energy = energy_statistics["mean_mev"]
            residual_energy = beam_energy - energy_setpoint
            measurement_mode = str(
                info.get("measurement_mode", "position_inference")
            )
            center_lock_applied = bool(info.get("center_lock_applied", False))
            self.callbacks.diagnostic(
                measurement["raw_image"],
                energy_setpoint,
                {
                    "valid_frames": int(measurement["valid_frames"]),
                    "total_frames": int(measurement["total_frames"]),
                    "fit_r_squared": measurement["fit_r_squared"],
                    "beam_energy_mev": beam_energy,
                    "energy_standard_deviation_mev": energy_statistics[
                        "standard_deviation_mev"
                    ],
                    "measurement_mode": measurement_mode,
                    "center_lock_applied": center_lock_applied,
                },
            )
            return EnergyMatchResult(
                ok=True,
                status=tuner.get_last_status(),
                energy_mev=beam_energy,
                energy_setpoint_mev=energy_setpoint,
                residual_energy_mev=residual_energy,
                energy_standard_deviation_mev=energy_statistics[
                    "standard_deviation_mev"
                ],
                energy_standard_error_mev=energy_statistics[
                    "standard_error_mev"
                ],
                measurement_mode=measurement_mode,
                center_lock_applied=center_lock_applied,
                message=tuner.get_last_message(),
                center_offset_mm=center_offset_mm,
                brightness=float(measurement["brightness"]),
                valid_frames=int(measurement["valid_frames"]),
                total_frames=int(measurement["total_frames"]),
                invalid_frames=int(measurement["invalid_frames"]),
                fit_method=str(measurement["fit_method"]),
                fit_r_squared=measurement["fit_r_squared"],
                center_spread_mm=measurement.get("center_spread_mm"),
                frame_centers_mm=measurement.get("frame_centers_mm"),
                frame_energies_mev=frame_energy,
                frame_rejection_reasons=measurement.get(
                    "frame_rejection_reasons"
                ),
                frame_fit_methods=measurement.get("frame_fit_methods"),
                frame_fit_r_squared=measurement.get("frame_fit_r_squared"),
                frame_brightness=measurement.get("frame_brightness"),
                fallback_reasons=measurement.get("fallback_reasons"),
                gaussian_frames=measurement.get("gaussian_frames"),
                centroid_frames=measurement.get("centroid_frames"),
                beam_threshold=measurement.get("beam_threshold"),
                beam_area_px=measurement.get("beam_area_px"),
                beam_major_axis_px=measurement.get("beam_major_axis_px"),
                beam_minor_axis_px=measurement.get("beam_minor_axis_px"),
                beam_aspect_ratio=measurement.get("beam_aspect_ratio"),
                beam_orientation_rad=measurement.get("beam_orientation_rad"),
            )
        diagnostic = dict(tuner.acquisition.last_diagnostic or {})
        return EnergyMatchResult(
            ok=False,
            status=tuner.get_last_status(),
            message=tuner.get_last_message(),
            center_offset_mm=info.get("final_offset_mm"),
            brightness=info.get("brightness"),
            valid_frames=diagnostic.get("valid_frames", info.get("valid_frames")),
            total_frames=diagnostic.get("total_frames"),
            invalid_frames=diagnostic.get("invalid_frames"),
            fit_method=info.get("fit_method"),
            fit_r_squared=info.get("fit_r_squared"),
            center_spread_mm=diagnostic.get("center_spread_mm"),
            frame_centers_mm=diagnostic.get("frame_centers_mm"),
            frame_rejection_reasons=diagnostic.get("frame_rejection_reasons"),
            frame_fit_methods=diagnostic.get("frame_fit_methods"),
            frame_fit_r_squared=diagnostic.get("frame_fit_r_squared"),
            frame_brightness=diagnostic.get("frame_brightness"),
            fallback_reasons=diagnostic.get("fallback_reasons"),
            gaussian_frames=diagnostic.get("gaussian_frames"),
            centroid_frames=diagnostic.get("centroid_frames"),
        )

    def _match_progress(self, payload: Mapping[str, Any]) -> None:
        update = dict(payload)
        raw_image = update.pop("raw_image", None)
        if raw_image is not None:
            self.callbacks.diagnostic(
                raw_image,
                float(update["energy_mev"]),
                {
                    "valid_frames": update.get("valid_frames"),
                    "total_frames": update.get("total_frames"),
                    "fit_r_squared": update.get("fit_r_squared"),
                    "match_stage": update.get("stage"),
                },
            )
        self.callbacks.match_state(update)

    def _progress(self, payload: Mapping[str, Any], log: PhaseEnergyScanLog) -> None:
        event = str(payload.get("event", ""))
        if event in {"phase_set", "match_start"}:
            self.callbacks.state("Running")
        elif event == "center_return":
            settle_time_s = float(payload.get("settle_time_s", 0.0))
            self.callbacks.state(
                f"Stabilizing at center ({settle_time_s:g} s)"
            )
        elif event == "restore":
            self.callbacks.state("Restoring")
        if event in {"phase_set", "match_start", "center_return", "restore"}:
            log.write(
                event,
                **{key: value for key, value in payload.items() if key != "event"},
            )
        if event == "point":
            point = dict(payload.get("point") or {})
            log.record_point(point)
            self.callbacks.point(point)
            self.callbacks.progress(
                int(point.get("acquisition_index", point.get("index", 0))) + 1,
                int(self.request.settings.points),
            )
