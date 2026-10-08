import json

from gotacc.gui.preferences import (
    DEFAULT_MACHINE_TIMING,
    load_machine_timing_preferences,
    save_machine_timing_preferences,
)


def test_machine_timing_preferences_round_trip(tmp_path):
    path = tmp_path / "gui_preferences.json"

    saved = save_machine_timing_preferences(0.75, 0.125, path)

    assert saved == {"set_interval": 0.75, "sample_interval": 0.125}
    assert load_machine_timing_preferences(path) == saved
    assert json.loads(path.read_text(encoding="utf-8"))["machine_timing"] == saved


def test_machine_timing_preferences_reject_invalid_values(tmp_path):
    path = tmp_path / "gui_preferences.json"
    path.write_text(
        json.dumps(
            {
                "machine_timing": {
                    "set_interval": -1,
                    "sample_interval": "not-a-number",
                }
            }
        ),
        encoding="utf-8",
    )

    assert load_machine_timing_preferences(path) == DEFAULT_MACHINE_TIMING
