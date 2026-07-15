import pytest

from half_linac.src.apps.dispersion_correction.config import load_config


@pytest.mark.parametrize(
    ("json_path", "yaml_path"),
    [
        ("tests/dispersion_correction/fixtures/achromat_mvp.example.json", "tests/dispersion_correction/fixtures/achromat_mvp.example.yaml"),
        ("tests/dispersion_correction/fixtures/irfel_achromat.mock.json", "tests/dispersion_correction/fixtures/irfel_achromat.mock.yaml"),
        ("tests/dispersion_correction/fixtures/irfel_achromat.json", "tests/dispersion_correction/fixtures/irfel_achromat.yaml"),
    ],
)
def test_primary_json_configs_match_yaml_compatibility_files(json_path, yaml_path) -> None:
    assert load_config(json_path) == load_config(yaml_path)
