from half_linac.src.apps.dispersion_correction.config import load_config
from half_linac.src.apps.dispersion_correction.dryrun import build_operation_plan, format_operation_plan


def test_irfel_real_config_dryrun_warns_about_phase_calibration() -> None:
    plan = build_operation_plan(load_config("tests/dispersion_correction/fixtures/irfel_achromat.json"))

    assert plan["backend"] == {"type": "epics", "mode": "read_only"}
    assert plan["energy"]["name"] == "KLY1_CH3_PHASE"
    assert [item["name"] for item in plan["bpms"]] == ["BPM9", "BPM10"]
    assert [item["name"] for item in plan["knobs"]] == ["Q13_Q16_sym", "Q14_Q15_sym"]
    assert any("phase-to-dp/p" in warning for warning in plan["warnings"])
    assert not any("Charge PV" in warning for warning in plan["warnings"])
    assert not any("Loss PV" in warning for warning in plan["warnings"])


def test_irfel_mock_dryrun_text_contains_device_names() -> None:
    plan = build_operation_plan(load_config("tests/dispersion_correction/fixtures/irfel_achromat.mock.json"))
    text = format_operation_plan(plan)

    assert "Backend: offline (read_only)" in text
    assert "BPMs: BPM9, BPM10" in text
    assert "+/-0.25 deg for +/-0.0001 dp/p" in text
    assert "Q13_Q16_sym: QM13*1, QM16*1" in text
    assert "Q14_Q15_sym: QM14*1, QM15*1" in text
    assert "scan=+/-0.0005 A" in text
