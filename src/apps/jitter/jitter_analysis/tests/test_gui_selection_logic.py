from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jitter_analysis.gui.selection_logic import normalize_selection_for_available_pvs


def test_normalize_selection_keeps_valid_ids_and_active_knob():
    selection = normalize_selection_for_available_pvs(
        selected_knob_ids=["k1", "missing", "k2"],
        active_knob_id="k2",
        selected_object_ids=["obj1", "missing_obj"],
        available_knob_ids={"k1", "k2"},
        available_object_ids={"obj1"},
    )

    assert selection == {
        "selected_knob_ids": ["k1", "k2"],
        "active_knob_id": "k2",
        "selected_object_ids": ["obj1"],
    }


def test_normalize_selection_falls_back_to_first_selected_knob():
    selection = normalize_selection_for_available_pvs(
        selected_knob_ids=["missing", "k1", "k2"],
        active_knob_id="missing",
        selected_object_ids=[],
        available_knob_ids={"k1", "k2"},
        available_object_ids=set(),
    )

    assert selection["selected_knob_ids"] == ["k1", "k2"]
    assert selection["active_knob_id"] == "k1"


def test_normalize_selection_clears_active_knob_when_no_selected_knobs_survive():
    selection = normalize_selection_for_available_pvs(
        selected_knob_ids=["missing"],
        active_knob_id="missing",
        selected_object_ids=["missing_obj"],
        available_knob_ids={"k1"},
        available_object_ids={"obj1"},
    )

    assert selection == {
        "selected_knob_ids": [],
        "active_knob_id": None,
        "selected_object_ids": [],
    }
