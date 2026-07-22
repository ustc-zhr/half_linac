import numpy as np

from half_linac.src.apps.dispersion_correction import model_response
from half_linac.src.apps.dispersion_correction.model_response import calculate_model_response
from half_linac.src.apps.dispersion_correction.profile_runtime import load_profile_run_config
from half_linac.src.shared.machine_profile import load_app_context


class FakeModelBackend:
    _base = {
        "QL01": 1.0,
        "QL02": 1.0,
        "QL03": 1.0,
        "QL04": 1.0,
        "QL05": 1.0,
        "QL06": 1.0,
    }
    _sensitivity = {
        "BPM06": {"QL01": 1.0, "QL06": 1.0, "QL02": 0.2, "QL05": 0.2, "QL03": 0.5, "QL04": 0.5},
        "BPM07": {"QL01": -0.2, "QL06": -0.2, "QL02": 0.8, "QL05": 0.8, "QL03": 0.3, "QL04": 0.3},
    }

    def get_lattice_element(self, element_id):
        return {"K1": str(self._base[element_id])}

    def get_matrix_element(self, _entrance, bpm, row, column, *, lattice_overrides=None, **_kwargs):
        assert (row, column) == (0, 5)
        value_mm = {"BPM06": 0.25, "BPM07": -0.1}[bpm]
        for device, fields in (lattice_overrides or {}).items():
            value_mm += self._sensitivity[bpm][device] * (float(fields["K1"]) - self._base[device])
        return value_mm / 1000.0


def test_half_bl01_model_response_builds_ranked_orthogonal_knobs(monkeypatch) -> None:
    context = load_app_context(
        "dispersion_correction",
        machine_id="half",
        control_backend="vm",
    )
    _, config = load_profile_run_config(context)
    monkeypatch.setattr(model_response, "build_model_backend", lambda _context: FakeModelBackend())

    result = calculate_model_response(context, config)

    np.testing.assert_allclose(result.baseline_dispersion_mm, [0.25, -0.1])
    assert result.response_matrix.shape == (2, 3)
    assert result.retained_rank == 2
    assert len(result.derived_knobs) == 2
    assert set(result.derived_knobs[0].devices) == {
        "QL01",
        "QL02",
        "QL03",
        "QL04",
        "QL05",
        "QL06",
    }
