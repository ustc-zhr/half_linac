from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jitter_analysis.acquisition.plans import KnobScanPlan


def test_knob_scan_plan_keeps_values():
    plan = KnobScanPlan(
        knob_id="hc01_current",
        target_ids=["bpm01_x"],
        scan_values=[-0.1, 0.0, 0.1],
        settle_delay_sec=0.5,
        sample_count_per_step=10,
    )
    assert plan.scan_values[1] == 0.0
    assert plan.sample_count_per_step == 10
