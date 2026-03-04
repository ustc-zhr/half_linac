import time
import numpy as np
from epics import caget, caput


class EnergyFeedbackController:
    """
    Pulse-to-pulse energy feedback controller for Linac.

    Energy diagnostic:
        - BPM at dispersive section (single or double BPM)

    Actuators:
        - LLRF IQ (amplitude / phase)
        - Klystron modulator high voltage (HV)
        - Symmetric phase trim of the last two accelerating structures

    Feedback modes:
        - "IQ"
        - "HV"
        - "PHASE_PAIR"
        - "HYBRID"
    """

    # -------------------------------
    # Initialization
    # -------------------------------
    def __init__(
        self,
        mode="HV",
        rep_rate=1.0,

        # ---- BPM configuration ----
        pv_bpm_x="BPM:DSP:X",
        pv_bpm_valid="BPM:DSP:VALID",

        use_two_bpm=False,
        pv_bpm2_x=None,

        # ---- Dispersion / optics ----
        D1=1.2,          # [m]
        D2=None,         # [m]
        alpha=None,      # optics factor a2/a1

        # ---- Actuators: HV ----
        pv_hv_set="MOD:HV:SET",
        pv_hv_rb="MOD:HV:RB",

        # ---- Actuators: IQ ----
        pv_i_set="LLRF:IQ:I_SET",
        pv_q_set="LLRF:IQ:Q_SET",

        # ---- Actuators: Phase pair ----
        pv_phi1_set="LLRF:LAST1:PHASE_SET",
        pv_phi2_set="LLRF:LAST2:PHASE_SET",

        # ---- Gains ----
        gain_hv=2.0,          # kV / delta
        gain_iq=0.15,
        gain_phi=0.02,        # rad / delta (HYBRID)
        gain_phase=5.0,       # deg / delta (PHASE_PAIR)

        # ---- Safety limits ----
        max_hv_step=0.5,      # kV / pulse
        max_iq_step=0.02,
        max_phase_step=0.2    # deg / pulse
    ):

        # Mode & timing
        self.mode = mode.upper()
        self.rep_rate = rep_rate

        # BPM
        self.pv_bpm_x = pv_bpm_x
        self.pv_bpm_valid = pv_bpm_valid
        self.use_two_bpm = use_two_bpm
        self.pv_bpm2_x = pv_bpm2_x

        # Optics
        self.D1 = D1
        self.D2 = D2
        self.alpha = alpha

        # HV
        self.pv_hv_set = pv_hv_set
        self.pv_hv_rb = pv_hv_rb

        # IQ
        self.pv_i_set = pv_i_set
        self.pv_q_set = pv_q_set

        # Phase pair
        self.pv_phi1_set = pv_phi1_set
        self.pv_phi2_set = pv_phi2_set

        # Gains
        self.gain_hv = gain_hv
        self.gain_iq = gain_iq
        self.gain_phi = gain_phi
        self.gain_phase = gain_phase

        # Limits
        self.max_hv_step = max_hv_step
        self.max_iq_step = max_iq_step
        self.max_phase_step = max_phase_step

        self._validate_config()

    # -------------------------------
    # Configuration check
    # -------------------------------
    def _validate_config(self):
        if self.mode not in ("IQ", "HV", "HYBRID", "PHASE_PAIR"):
            raise ValueError(f"Unknown feedback mode: {self.mode}")

        if self.use_two_bpm:
            if self.pv_bpm2_x is None:
                raise ValueError("Second BPM PV not specified")
            if self.D2 is None or self.alpha is None:
                raise ValueError("D2 and alpha must be provided")

    # -------------------------------
    # Utility
    # -------------------------------
    @staticmethod
    def _clamp(val, lim):
        return max(min(val, lim), -lim)

    # -------------------------------
    # Energy error computation
    # -------------------------------
    def compute_delta(self):
        x1 = caget(self.pv_bpm_x)

        if self.use_two_bpm:
            x2 = caget(self.pv_bpm2_x)
            delta = (x2 - self.alpha * x1) / (self.D2 - self.alpha * self.D1)
        else:
            delta = x1 / self.D1

        return delta

    # -------------------------------
    # Actuator updates
    # -------------------------------
    def _apply_hv_feedback(self, delta):
        hv = caget(self.pv_hv_rb)
        dhv = self._clamp(-self.gain_hv * delta, self.max_hv_step)
        caput(self.pv_hv_set, hv + dhv)
        return f"dHV={dhv:+.3f} kV"

    def _apply_iq_feedback(self, delta):
        I = caget(self.pv_i_set)
        Q = caget(self.pv_q_set)
        phi = np.arctan2(Q, I)

        dI = self._clamp(-self.gain_iq * np.cos(phi) * delta,
                          self.max_iq_step)
        dQ = self._clamp(-self.gain_iq * np.sin(phi) * delta,
                          self.max_iq_step)

        caput(self.pv_i_set, I + dI)
        caput(self.pv_q_set, Q + dQ)
        return f"dI={dI:+.3e}, dQ={dQ:+.3e}"

    def _apply_phase_pair_feedback(self, delta):
        """
        Symmetric phase trim on the last two accelerating structures.
        Does NOT increase energy spread.
        """
        dphi = self._clamp(-self.gain_phase * delta, self.max_phase_step)

        phi1 = caget(self.pv_phi1_set)
        phi2 = caget(self.pv_phi2_set)

        caput(self.pv_phi1_set, phi1 + dphi)
        caput(self.pv_phi2_set, phi2 - dphi)

        return f"dPhi1={dphi:+.3f} deg, dPhi2={-dphi:+.3f} deg"

    def _apply_hybrid_feedback(self, delta):
        # HV part
        hv = caget(self.pv_hv_rb)
        dhv = self._clamp(-self.gain_hv * delta, self.max_hv_step)
        caput(self.pv_hv_set, hv + dhv)

        # IQ phase trim
        I = caget(self.pv_i_set)
        Q = caget(self.pv_q_set)
        A = np.hypot(I, Q)
        phi = np.arctan2(Q, I) - self.gain_phi * delta

        caput(self.pv_i_set, A * np.cos(phi))
        caput(self.pv_q_set, A * np.sin(phi))

        return f"dHV={dhv:+.3f} kV, dPhiIQ={-self.gain_phi*delta:+.3e}"

    # -------------------------------
    # One feedback step
    # -------------------------------
    def step(self):

        # BPM validity check
        if caget(self.pv_bpm_valid) != 1:
            return None

        delta = self.compute_delta()

        # Optional deadband
        if abs(delta) < 2e-4:
            return None

        if self.mode == "HV":
            action = self._apply_hv_feedback(delta)
        elif self.mode == "IQ":
            action = self._apply_iq_feedback(delta)
        elif self.mode == "PHASE_PAIR":
            action = self._apply_phase_pair_feedback(delta)
        elif self.mode == "HYBRID":
            action = self._apply_hybrid_feedback(delta)

        print(f"[P2P] mode={self.mode:11s} delta={delta:+.3e}  {action}")

        time.sleep(1.0 / self.rep_rate)
        return delta


# ---------------------------------------------------------
# Example usage
# ---------------------------------------------------------
if __name__ == "__main__":

    ctrl = EnergyFeedbackController(
        mode="PHASE_PAIR", # "IQ" "HV" "PHASE_PAIR" "HYBRID"
        rep_rate=1,

        pv_bpm_x="BPM:DSP:X",
        pv_bpm_valid="BPM:DSP:VALID",

        use_two_bpm=True,
        pv_bpm2_x="BPM2:DSP:X",

        D1=1.20,
        D2=0.35,
        alpha=0.85,

        pv_hv_set="MOD:HV:SET",
        pv_hv_rb="MOD:HV:RB",

        pv_phi1_set="LLRF:LAST1:PHASE_SET",
        pv_phi2_set="LLRF:LAST2:PHASE_SET"
    )

    while True:
        ctrl.step()
