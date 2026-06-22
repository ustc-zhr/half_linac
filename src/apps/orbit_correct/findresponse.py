from __future__ import annotations

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
from typing import List, Tuple
from epics import caget, caget_many, caput, caput_many
from half_linac.src.shared.machine_profile import (
    load_app_context,
    require_workflow_write_allowed,
    resolve_channel,
)
from half_linac.src.apps.orbit_correct.profile_runtime import (
    FINDRESPONSE_LOG_PATH,
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

class ResponseMatrixCalculator:
    """Calculate accelerator response matrix using corrector kicks."""
    
    def __init__(self, n_bpm: int | None = None, n_cor: int | None = None, 
                 d_value: float = 1e-5, n_averages: int = 2,
                 wait_s: float | None = None):
        """
        Initialize response matrix calculator.
        
        Args:
            n_bpm: Number of BPMs
            n_cor: Number of correctors
            d_value: Kick amplitude [rad]
            n_averages: Number of measurement averages
            wait_s: Settling time after each corrector update [s]
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
        if self.orbit_workflow is None:
            raise ValueError("Orbit workflow is not available in the current app context.")
        self.profile_max_value = self.orbit_runtime["corrector_upperlimit"]
        self.max_value_unit = self.orbit_runtime["corrector_upperlimit_unit"]
        self.bpm_ids = list(self.orbit_workflow.bpms)
        self.xcor_ids = list(self.orbit_workflow.xcors)
        self.ycor_ids = list(self.orbit_workflow.ycors)

        self.N_BPM = len(self.bpm_ids) if n_bpm is None else len(self.bpm_ids)
        self.N_COR = len(self.xcor_ids) if n_cor is None else len(self.xcor_ids)
        self.d_value = self._select_response_kick(d_value)
        self.n_averages = self._select_positive_int(n_averages, "n_averages")
        default_wait_s = self.orbit_runtime["response_wait_s"]
        self.timer_interval = self._select_nonnegative_float(
            default_wait_s if wait_s is None else wait_s,
            "wait_s",
        )
        
        # Initialize PV lists
        self.pvBPMx: List[str] = []
        self.pvBPMy: List[str] = []
        self.pvCORx: List[str] = []
        self.pvCORy: List[str] = []
        
        # Initialize matrix storage
        self.response_matrix: np.ndarray = np.zeros((2*self.N_BPM, 2*self.N_COR))
        
        logger.info(
            "ResponseMatrixCalculator initialized: kick=%s, averages=%s, wait_s=%s, shape=%s",
            self.d_value,
            self.n_averages,
            self.timer_interval,
            self.response_matrix.shape,
        )

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
            resolve_channel(self.app_context, cor_id, "setpoint")
            for cor_id in self.xcor_ids
        ]
        self.pvCORy = [
            resolve_channel(self.app_context, cor_id, "setpoint")
            for cor_id in self.ycor_ids
        ]
        logger.debug(f"Initialized {len(self.pvCORx)} COR X PVs")
        logger.debug(f"Initialized {len(self.pvCORy)} COR Y PVs")

    def _measure_response(self, cor_pv: str, is_x_corrector: bool) -> np.ndarray:
        """
        Measure response for a single corrector.
        
        Args:
            cor_pv: Corrector PV name
            is_x_corrector: True for X corrector, False for Y
            
        Returns:
            Response vector (2*N_BPM elements)
        """
        try:
            # Store original value
            original_value = caget(cor_pv)
            if original_value is None:
                raise ValueError(f"Failed to read PV: {cor_pv}")
                
            # Pre-allocate measurement arrays
            bpm_x_plus = np.zeros((self.n_averages, self.N_BPM))
            bpm_y_plus = np.zeros((self.n_averages, self.N_BPM))
            bpm_x_minus = np.zeros((self.n_averages, self.N_BPM))
            bpm_y_minus = np.zeros((self.n_averages, self.N_BPM))
            
            # Positive kick measurement
            caput(cor_pv, original_value + 2*self.d_value)
            time.sleep(self.timer_interval)
            
            for i in range(self.n_averages):
                bpm_x_plus[i] = caget_many(self.pvBPMx)
                bpm_y_plus[i] = caget_many(self.pvBPMy)
                if i < self.n_averages - 1:
                    time.sleep(self.timer_interval)
            
            # Negative kick measurement
            caput(cor_pv, original_value - 0*self.d_value)
            time.sleep(self.timer_interval)
            
            for i in range(self.n_averages):
                bpm_x_minus[i] = caget_many(self.pvBPMx)
                bpm_y_minus[i] = caget_many(self.pvBPMy)
                if i < self.n_averages - 1:
                    time.sleep(self.timer_interval)
            
            # Restore original value
            caput(cor_pv, original_value)
            time.sleep(self.timer_interval)
            
            # Calculate response
            mean_x_plus = np.mean(bpm_x_plus, axis=0)
            mean_y_plus = np.mean(bpm_y_plus, axis=0)
            mean_x_minus = np.mean(bpm_x_minus, axis=0)
            mean_y_minus = np.mean(bpm_y_minus, axis=0)
            
            response_x = (mean_x_plus - mean_x_minus) / (2 * self.d_value)
            response_y = (mean_y_plus - mean_y_minus) / (2 * self.d_value)
            
            return np.concatenate([response_x, response_y])
            
        except Exception as e:
            logger.error(f"Error measuring response for {cor_pv}: {str(e)}")
            caput(cor_pv, original_value)  # Try to restore
            raise

    def calculate_response_matrix(self) -> None:
        """Calculate full response matrix.[dx/dcorr]""" 
        logger.info("Starting response matrix calculation")
        
        try:
            # Process X correctors
            for i, cor_pv in enumerate(self.pvCORx):
                logger.info(f"Processing X corrector {i+1}/{len(self.pvCORx)}: {cor_pv}")
                response = self._measure_response(cor_pv, True)
                self.response_matrix[:, i] = response
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
                response = self._measure_response(cor_pv, False)
                self.response_matrix[:, i + self.N_COR] = response
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
                return

            output_path = Path(filename)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            np.savetxt(output_path, self.response_matrix)
            logger.info(f"Response matrix saved to {output_path}")
        except Exception as e:
            logger.error(f"Failed to save matrix: {str(e)}")
            raise

if __name__ == '__main__':
    try:
        d_value = float(sys.argv[1]) if len(sys.argv) > 1 else 1e-5
        n_averages = int(sys.argv[2]) if len(sys.argv) > 2 else 1
        wait_s = float(sys.argv[3]) if len(sys.argv) > 3 else None
        calculator = ResponseMatrixCalculator(
            d_value=d_value,
            n_averages=n_averages,
            wait_s=wait_s,
        )
        calculator.init_BPM_pv()
        calculator.init_COR_pv()
        calculator.calculate_response_matrix()
        calculator.save_matrix()
    except Exception as e:
        logger.critical(f"Fatal error: {str(e)}")
        raise
