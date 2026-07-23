import math

import pytest

from half_linac.src.apps.dispersion_correction.measurement_import import load_dispersion_csv


def test_load_dispersion_csv_with_optional_uncertainty(tmp_path) -> None:
    path = tmp_path / "bl01_etax.csv"
    path.write_text(
        "bpm,etax_mm,etax_sigma_mm\n"
        "BPM03,-320.1,1.2\n"
        "BPM06,0.25,\n",
        encoding="utf-8",
    )

    result = load_dispersion_csv(
        path,
        section_id="bl01",
        allowed_bpms=("BPM03", "BPM04", "BPM05", "BPM06", "BPM07"),
    )

    assert result.section_id == "bl01"
    assert result.bpm_names == ("BPM03", "BPM06")
    assert result.etax_mm.tolist() == pytest.approx([-320.1, 0.25])
    assert result.etax_sigma_mm[0] == pytest.approx(1.2)
    assert math.isnan(result.etax_sigma_mm[1])
    assert result.source_path == str(path.resolve())


@pytest.mark.parametrize(
    ("contents", "message"),
    (
        ("bpm,dx_mm\nBPM03,1\n", r"missing column\(s\): etax_mm"),
        ("bpm,etax_mm\nBPM02,1\n", "not in the current model section"),
        ("bpm,etax_mm\nBPM03,1\nBPM03,2\n", "duplicate BPM BPM03"),
        ("bpm,etax_mm,etax_sigma_mm\nBPM03,1,-0.1\n", "must be non-negative"),
    ),
)
def test_load_dispersion_csv_rejects_invalid_input(tmp_path, contents, message) -> None:
    path = tmp_path / "invalid.csv"
    path.write_text(contents, encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        load_dispersion_csv(
            path,
            section_id="bl01",
            allowed_bpms=("BPM03",),
        )
