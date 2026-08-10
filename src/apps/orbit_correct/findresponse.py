from __future__ import annotations

import json
import signal
import time
import logging
import sys
from pathlib import Path

_REPO_BOOTSTRAP_ROOT = next(
    parent for parent in Path(__file__).resolve().parents if (parent / "repo_bootstrap.py").is_file()
)
if str(_REPO_BOOTSTRAP_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_BOOTSTRAP_ROOT))

from repo_bootstrap import ensure_repo_import_path

ensure_repo_import_path(__file__)

import numpy as np
from typing import List
from epics import caget, caget_many, caput
from half_linac.src.shared.machine_profile import (
    load_app_context,
    require_workflow_write_allowed,
    resolve_channel,
    resolve_write_target,
)
from half_linac.src.apps.orbit_correct.profile_runtime import (
    FINDRESPONSE_LOG_PATH,
    display_unit,
    effective_corrector_limit,
    load_orbit_runtime_settings,
    write_response_matrix_snapshot,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    filename=FINDRESPONSE_LOG_PATH
)
logger = logging.getLogger(__name__)


class ResponseMeasurementInterrupted(Exception):
    """Raised when response measurement receives a shutdown signal."""


class ResponseMatrixCalculator:
    """Calculate accelerator response matrix using corrector kicks."""
    
    def __init__(self, d_value: float = 1e-5, n_averages: int = 2,
                 wait_s: float | None = None,
                 sample_interval_s: float | None = None):
        """
        Initialize response matrix calculator.
        
        Args:
            d_value: Kick amplitude [rad]
            n_averages: Number of measurement averages
            wait_s: Settling time after each corrector update [s]
            sample_interval_s: Wait time between repeated BPM samples [s]
        """
        self.app_context = load_app_context("orbit_correct")
        require_workflow_write_allowed(
            self.app_context,
            "orbit",
            "Response matrix measurement",
        )
        self.machine_profile = self.app_context.profile
        self.orbit_workflow = self.app_context.orbit_workflow
        self.machine_mode = self.app_context.control_backend.name
        self.orbit_runtime = load_orbit_runtime_settings(self.app_context)
        self.bpm_position_scale_to_m = self.orbit_runtime["bpm_position_scale_to_m"]
        if self.orbit_workflow is None:
            raise ValueError("Orbit workflow is not available in the current app context.")
        self.profile_max_value = self.orbit_runtime["corrector_upperlimit"]
        self.max_value_unit = display_unit(self.orbit_runtime["corrector_upperlimit_unit"])
        self.progress_path = Path(self.orbit_runtime["response_progress_path"])
        self.bpm_ids = list(self.orbit_workflow.bpms)
        self.xcor_ids = list(self.orbit_workflow.xcors)
        self.ycor_ids = list(self.orbit_workflow.ycors)
        self.corrector_limits = {
            element_id: effective_corrector_limit(
                self.app_context,
                element_id,
                self.profile_max_value,
                self.orbit_runtime["corrector_upperlimit_unit"],
            )
            for element_id in self.xcor_ids + self.ycor_ids
        }

        self.N_BPM = len(self.bpm_ids)
        self.N_COR = len(self.xcor_ids)
        self.d_value = self._select_response_kick(d_value)
        self.n_averages = self._select_positive_int(n_averages, "n_averages")
        default_wait_s = self.orbit_runtime["response_wait_s"]
        self.timer_interval = self._select_nonnegative_float(
            default_wait_s if wait_s is None else wait_s,
            "wait_s",
        )
        default_sample_interval_s = self.orbit_runtime["response_sample_interval_s"]
        self.sample_interval = self._select_nonnegative_float(
            default_sample_interval_s if sample_interval_s is None else sample_interval_s,
            "sample_interval_s",
        )
        
        # Initialize PV lists
        self.pvBPMx: List[str] = []
        self.pvBPMy: List[str] = []
        self.pvCORx: List[str] = []
        self.pvCORy: List[str] = []
        
        # Initialize matrix storage
        self.response_matrix: np.ndarray = np.zeros((2*self.N_BPM, 2*self.N_COR))
        signal.signal(signal.SIGTERM, self._handle_shutdown_signal)
        signal.signal(signal.SIGINT, self._handle_shutdown_signal)
        
        logger.info(
            "ResponseMatrixCalculator initialized: kick=%s, averages=%s, wait_s=%s, "
            "sample_interval_s=%s, shape=%s",
            self.d_value,
            self.n_averages,
            self.timer_interval,
            self.sample_interval,
            self.response_matrix.shape,
        )

    def _handle_shutdown_signal(self, signum, frame):
        raise ResponseMeasurementInterrupted(f"Response matrix measurement interrupted by signal {signum}.")

    def _write_progress(
        self,
        completed: int,
        total: int,
        *,
        current: str = "",
        status: str = "running",
    ) -> None:
        self.progress_path.parent.mkdir(parents=True, exist_ok=True)
        total = max(int(total), 0)
        completed = min(max(int(completed), 0), total) if total else 0
        percent = int(round(completed / total * 100)) if total else 0
        payload = {
            "completed": completed,
            "total": total,
            "percent": percent,
            "current": current,
            "status": status,
        }
        tmp_path = self.progress_path.with_suffix(f"{self.progress_path.suffix}.tmp")
        tmp_path.write_text(json.dumps(payload), encoding="utf-8")
        tmp_path.replace(self.progress_path)

    def _select_response_kick(self, value: float) -> float:
        selected = float(value)
        if selected <= 0:
            raise ValueError("response kick must be greater than 0.")
        if selected > self.profile_max_value:
            raise ValueError(
                f"response kick {selected:g} {self.max_value_unit} exceeds profile limit "
                f"{self.profile_max_value:g} {self.max_value_unit}."
            )
        return selected

    @staticmethod
    def _select_positive_int(value: int, label: str) -> int:
        selected = int(value)
        if selected <= 0:
            raise ValueError(f"{label} must be greater than 0.")
        return selected

    @staticmethod
    def _select_nonnegative_float(value: float, label: str) -> float:
        selected = float(value)
        if selected < 0:
            raise ValueError(f"{label} must be >= 0.")
        return selected

    @staticmethod
    def _read_scalar_pv(pv_name: str) -> float:
        value = caget(pv_name)
        if value is None:
            raise ValueError(f"Failed to read PV: {pv_name}")
        value = float(value)
        if not np.isfinite(value):
            raise ValueError(f"PV {pv_name} returned non-finite value: {value!r}")
        return value

    @staticmethod
    def _write_scalar_pv(pv_name: str, value: float) -> None:
        status = caput(pv_name, value, wait=True, timeout=2.0)
        if not status:
            raise ValueError(f"Failed to write PV {pv_name} to {value:g}.")

    def _read_bpm_values(self, pv_names: List[str], label: str) -> np.ndarray:
        values = caget_many(pv_names)
        if values is None or len(values) != len(pv_names):
            raise ValueError(
                f"Failed to read {label} BPM PVs: expected {len(pv_names)} values, got "
                f"{0 if values is None else len(values)}."
            )
        if any(value is None for value in values):
            missing = [pv for pv, value in zip(pv_names, values) if value is None]
            raise ValueError(f"Failed to read {label} BPM PVs: {', '.join(missing)}")
        array = np.asarray(values, dtype=float)
        if not np.all(np.isfinite(array)):
            raise ValueError(f"{label} BPM readings contain NaN or Inf.")
        return array * self.bpm_position_scale_to_m

    def init_BPM_pv(self) -> None:
        """Initialize BPM PV names."""
        self.pvBPMx = [
            resolve_channel(self.app_context, bpm_id, "x")
            for bpm_id in self.bpm_ids
        ]
        self.pvBPMy = [
            resolve_channel(self.app_context, bpm_id, "y")
            for bpm_id in self.bpm_ids
        ]
        logger.debug(f"Initialized {len(self.pvBPMx)} BPM X PVs")
        logger.debug(f"Initialized {len(self.pvBPMy)} BPM Y PVs")

    def init_COR_pv(self) -> None:
        """Initialize corrector PV names."""
        self.pvCORx = [
            resolve_write_target(self.app_context, cor_id).pv_name
            for cor_id in self.xcor_ids
        ]
        self.pvCORy = [
            resolve_write_target(self.app_context, cor_id).pv_name
            for cor_id in self.ycor_ids
        ]
        logger.debug(f"Initialized {len(self.pvCORx)} COR X PVs")
        logger.debug(f"Initialized {len(self.pvCORy)} COR Y PVs")

    def _measure_response(
        self, element_id: str, cor_pv: str, is_x_corrector: bool
    ) -> np.ndarray:
        """
        Measure response for a single corrector.
        
        Args:
            cor_pv: Corrector PV name
            is_x_corrector: True for X corrector, False for Y
            
        Returns:
            Response vector (2*N_BPM elements)
        """
        original_value = None
        try:
            # Store original value
            original_value = self._read_scalar_pv(cor_pv)
            limit = self.corrector_limits[element_id]
            if not limit.contains(original_value):
                raise ValueError(
                    f"Current value for {element_id} is outside its effective limit: "
                    f"{original_value:g} {self.max_value_unit}, expected {limit.describe()}."
                )
            if not limit.contains(original_value + self.d_value):
                raise ValueError(
                    f"Response kick would exceed the effective limit for {element_id}: "
                    f"{original_value + self.d_value:g} {self.max_value_unit}, "
                    f"expected {limit.describe()}."
                )
                
            # Pre-allocate measurement arrays
            bpm_x_plus = np.zeros((self.n_averages, self.N_BPM))
            bpm_y_plus = np.zeros((self.n_averages, self.N_BPM))
            bpm_x_minus = np.zeros((self.n_averages, self.N_BPM))
            bpm_y_minus = np.zeros((self.n_averages, self.N_BPM))
            
            # Kick measurement. The configured value is the actual PV step.
            self._write_scalar_pv(cor_pv, original_value + self.d_value)
            time.sleep(self.timer_interval)
            
            for i in range(self.n_averages):
                bpm_x_plus[i] = self._read_bpm_values(self.pvBPMx, "X")
                bpm_y_plus[i] = self._read_bpm_values(self.pvBPMy, "Y")
                if i < self.n_averages - 1:
                    time.sleep(self.sample_interval)
            
            # Baseline measurement at the original setpoint.
            self._write_scalar_pv(cor_pv, original_value)
            time.sleep(self.timer_interval)
            
            for i in range(self.n_averages):
                bpm_x_minus[i] = self._read_bpm_values(self.pvBPMx, "X")
                bpm_y_minus[i] = self._read_bpm_values(self.pvBPMy, "Y")
                if i < self.n_averages - 1:
                    time.sleep(self.sample_interval)
            
            # Calculate response
            mean_x_plus = np.mean(bpm_x_plus, axis=0)
            mean_y_plus = np.mean(bpm_y_plus, axis=0)
            mean_x_minus = np.mean(bpm_x_minus, axis=0)
            mean_y_minus = np.mean(bpm_y_minus, axis=0)
            
            response_x = (mean_x_plus - mean_x_minus) / self.d_value
            response_y = (mean_y_plus - mean_y_minus) / self.d_value
            
            return np.concatenate([response_x, response_y])
            
        except Exception as e:
            logger.error(f"Error measuring response for {cor_pv}: {str(e)}")
            raise
        finally:
            if original_value is not None:
                try:
                    self._write_scalar_pv(cor_pv, original_value)
                    logger.info("Restored %s to %s after response measurement.", cor_pv, original_value)
                except Exception as restore_error:
                    logger.error(
                        "Failed to restore %s to %s: %s",
                        cor_pv,
                        original_value,
                        restore_error,
                    )

    def calculate_response_matrix(self) -> None:
        """Calculate full response matrix.[dx/dcorr]""" 
        logger.info("Starting response matrix calculation")
        total_steps = len(self.pvCORx) + len(self.pvCORy)
        completed_steps = 0
        self._write_progress(completed_steps, total_steps, current="Starting")
        
        try:
            # Process X correctors
            for i, cor_pv in enumerate(self.pvCORx):
                logger.info(f"Processing X corrector {i+1}/{len(self.pvCORx)}: {cor_pv}")
                self._write_progress(
                    completed_steps,
                    total_steps,
                    current=f"X {i + 1}/{len(self.pvCORx)}",
                )
                response = self._measure_response(self.xcor_ids[i], cor_pv, True)
                self.response_matrix[:, i] = response
                completed_steps += 1
                self._write_progress(
                    completed_steps,
                    total_steps,
                    current=f"X {i + 1}/{len(self.pvCORx)}",
                )
                logger.debug(
                    "Filled response matrix column %d/%d from %s; response norm=%.6g",
                    i + 1,
                    2 * self.N_COR,
                    cor_pv,
                    np.linalg.norm(response),
                )
            
            # Process Y correctors (offset by N_COR in matrix)
            for i, cor_pv in enumerate(self.pvCORy):
                logger.info(f"Processing Y corrector {i+1}/{len(self.pvCORy)}: {cor_pv}")
                self._write_progress(
                    completed_steps,
                    total_steps,
                    current=f"Y {i + 1}/{len(self.pvCORy)}",
                )
                response = self._measure_response(self.ycor_ids[i], cor_pv, False)
                self.response_matrix[:, i + self.N_COR] = response
                completed_steps += 1
                self._write_progress(
                    completed_steps,
                    total_steps,
                    current=f"Y {i + 1}/{len(self.pvCORy)}",
                )
                logger.debug(
                    "Filled response matrix column %d/%d from %s; response norm=%.6g",
                    i + self.N_COR + 1,
                    2 * self.N_COR,
                    cor_pv,
                    np.linalg.norm(response),
                )
                
            logger.info("Response matrix calculation completed")
            
        except Exception as e:
            logger.error(f"Failed to calculate response matrix: {str(e)}")
            self._write_progress(
                completed_steps,
                total_steps,
                current=str(e),
                status="failed",
            )
            raise

    def save_matrix(self, filename: str | Path | None = None) -> None:
        """Save response matrix to file."""
        try:
            if filename is None:
                metadata = write_response_matrix_snapshot(self.app_context, self.response_matrix)
                logger.info(
                    "Response matrix saved to %s and set active",
                    metadata["matrix_file"],
                )
                total_steps = len(self.pvCORx) + len(self.pvCORy)
                self._write_progress(
                    total_steps,
                    total_steps,
                    current="Completed",
                    status="completed",
                )
                return

            output_path = Path(filename)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            np.savetxt(output_path, self.response_matrix)
            logger.info(f"Response matrix saved to {output_path}")
            total_steps = len(self.pvCORx) + len(self.pvCORy)
            self._write_progress(
                total_steps,
                total_steps,
                current="Completed",
                status="completed",
            )
        except Exception as e:
            logger.error(f"Failed to save matrix: {str(e)}")
            total_steps = len(self.pvCORx) + len(self.pvCORy)
            self._write_progress(
                0,
                total_steps,
                current=str(e),
                status="failed",
            )
            raise

if __name__ == '__main__':
    try:
        d_value = float(sys.argv[1]) if len(sys.argv) > 1 else 1e-5
        n_averages = int(sys.argv[2]) if len(sys.argv) > 2 else 1
        wait_s = float(sys.argv[3]) if len(sys.argv) > 3 else None
        sample_interval_s = float(sys.argv[4]) if len(sys.argv) > 4 else None
        calculator = ResponseMatrixCalculator(
            d_value=d_value,
            n_averages=n_averages,
            wait_s=wait_s,
            sample_interval_s=sample_interval_s,
        )
        calculator.init_BPM_pv()
        calculator.init_COR_pv()
        calculator.calculate_response_matrix()
        calculator.save_matrix()
    except Exception as e:
        logger.critical(f"Fatal error: {str(e)}")
        raise
