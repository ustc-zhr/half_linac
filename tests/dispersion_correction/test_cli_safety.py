import pytest

from half_linac.src.apps.dispersion_correction.cli import _load_runtime_config
def test_external_epics_config_cannot_authorize_write_operation() -> None:
    with pytest.raises(PermissionError, match="active half_linac machine profile"):
        _load_runtime_config(
            "tests/dispersion_correction/fixtures/irfel_achromat.json",
            write_operation=True,
        )


def test_external_offline_config_remains_available_for_development() -> None:
    loaded = _load_runtime_config(
        "tests/dispersion_correction/fixtures/achromat_mvp.example.json",
        write_operation=True,
    )
    assert loaded.backend.type == "offline"
