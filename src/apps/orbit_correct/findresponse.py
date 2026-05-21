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
import half_linac.runtime_config as st

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    filename='findresponse.log'
)
logger = logging.getLogger(__name__)

class ResponseMatrixCalculator:
    """Calculate accelerator response matrix using corrector kicks."""
    
    def __init__(self, n_bpm: int = 41, n_cor: int = 41, 
                 d_value: float = 1e-5, n_averages: int = 2):
        """
        Initialize response matrix calculator.
        
        Args:
            n_bpm: Number of BPMs
            n_cor: Number of correctors
            d_value: Kick amplitude [rad]
            n_averages: Number of measurement averages
        """
        print('n_averages:', n_averages)
        self.N_BPM = n_bpm
        self.N_COR = n_cor
        self.d_value = d_value
        self.n_averages = n_averages
        self.timer_interval = st.runtime_machine
        
        # Initialize PV lists
        self.pvBPMx: List[str] = []
        self.pvBPMy: List[str] = []
        self.pvCORx: List[str] = []
        self.pvCORy: List[str] = []
        
        # Initialize matrix storage
        self.response_matrix: np.ndarray = np.zeros((2*self.N_BPM, 2*self.N_COR))
        
        logger.info("ResponseMatrixCalculator initialized")

    def _generate_pv_name(self, prefix: str, device_type: str, 
                         index: int, suffix: str) -> str:
        """Generate EPICS PV name with proper zero padding."""
        base = f"HALF:IN:{device_type}:"
        if index + 3 < 10:
            return f"{base}{prefix}0{index+3}:{suffix}"
        return f"{base}{prefix}{index+3}:{suffix}"

    def init_BPM_pv(self) -> None:
        """Initialize BPM PV names."""
        self.pvBPMx = [
            self._generate_pv_name("BPM", "BPM", j, "X:ao") 
            for j in range(self.N_BPM)
        ]
        self.pvBPMy = [
            self._generate_pv_name("BPM", "BPM", j, "Y:ao") 
            for j in range(self.N_BPM)
        ]
        logger.debug(f"Initialized {len(self.pvBPMx)} BPM X PVs")
        logger.debug(f"Initialized {len(self.pvBPMy)} BPM Y PVs")

    def init_COR_pv(self) -> None:
        """Initialize corrector PV names."""
        self.pvCORx = [
            self._generate_pv_name("XC", "COR", j, "ao") 
            for j in range(self.N_COR)
        ]
        self.pvCORy = [
            self._generate_pv_name("YC", "COR", j, "ao") 
            for j in range(self.N_COR)
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
                print(response)
                self.response_matrix[:, i] = response
                print(self.response_matrix)
            
            # Process Y correctors (offset by N_COR in matrix)
            for i, cor_pv in enumerate(self.pvCORy):
                logger.info(f"Processing Y corrector {i+1}/{len(self.pvCORy)}: {cor_pv}")
                response = self._measure_response(cor_pv, False)
                print(response)
                self.response_matrix[:, i + self.N_COR] = response
                
            logger.info("Response matrix calculation completed")
            
        except Exception as e:
            logger.error(f"Failed to calculate response matrix: {str(e)}")
            raise

    def save_matrix(self, filename: str = 'response.txt') -> None:
        """Save response matrix to file."""
        try:
            np.savetxt(filename, self.response_matrix)
            logger.info(f"Response matrix saved to {filename}")
        except Exception as e:
            logger.error(f"Failed to save matrix: {str(e)}")
            raise

if __name__ == '__main__':
    try:
        calculator = ResponseMatrixCalculator(n_averages=1)
        calculator.init_BPM_pv()
        calculator.init_COR_pv()
        calculator.calculate_response_matrix()
        calculator.save_matrix()
    except Exception as e:
        logger.critical(f"Fatal error: {str(e)}")
        raise
