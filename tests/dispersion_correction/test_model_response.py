import numpy as np
import pytest

from half_linac.src.apps.dispersion_correction import model_response
from half_linac.src.apps.dispersion_correction.model_response import (
    calculate_model_response,
    format_model_response,
    model_response_to_dict,
)
from half_linac.src.apps.dispersion_correction.profile_runtime import load_profile_run_config
from half_linac.src.shared.machine_profile import load_app_context, resolve_channel


def test_half_joint_correction_design_model_uses_joint_quadrupoles() -> None:
    context = load_app_context(
        "dispersion_correction",
        machine_id="half",
        control_backend="vm",
    )
    _, config = load_profile_run_config(
        context,
        section_id="bh04_sep_joint_correction",
    )

    result = calculate_model_response(
        context,
        config,
        model_source="design",
    )

    assert result.device_names == tuple(f"QT{index}" for index in range(30, 36))
    assert result.observable_names == (
        "BPM42 Dx",
        "BPM43 Dx",
        "BPM42 Dy",
        "BPM43 Dy",
    )
    assert "SEP2" in result.design_curve.element_names
    assert "SEP1" in result.design_curve.element_names


class FakeModelBackend:
    _base = {
        "QL01": 1.0,
        "QL02": 1.0,
        "QL03": 1.0,
        "QL04": 1.0,
        "QL05": 1.0,
        "QL06": 1.0,
        "QL07": 1.0,
        "QL08": 1.0,
        "QL09": 1.0,
        "QL10": 1.0,
        "QL11": 1.0,
        "QL12": 1.0,
    }
    _dx_sensitivity = {
        "QL01": 1.0,
        "QL06": 1.0,
        "QL02": 0.2,
        "QL05": 0.2,
        "QL03": 0.5,
        "QL04": 0.5,
        "QL07": 0.7,
        "QL08": 0.0,
        "QL09": 0.0,
        "QL10": 0.0,
        "QL11": 0.0,
        "QL12": 0.0,
    }
    _dxp_sensitivity = {
        "QL01": -0.2,
        "QL06": -0.2,
        "QL02": 0.8,
        "QL05": 0.8,
        "QL03": 0.3,
        "QL04": 0.3,
        "QL07": 0.0,
        "QL08": 0.0,
        "QL09": 0.0,
        "QL10": 0.0,
        "QL11": 0.0,
        "QL12": 0.0,
    }

    def __init__(self):
        self.optics_sequences = []

    def get_lattice_element(self, element_id):
        if element_id.startswith("BPM"):
            return {"NAME": element_id, "TYPE": "MONI", "L": "0"}
        return {"K1": str(self._base[element_id])}

    def get_line_elements(self, _entrance, _exit):
        return tuple(
            {"NAME": name, "TYPE": "QUAD", "K1": str(value)}
            for name, value in self._base.items()
        )

    def get_optics_profile(
        self,
        _entrance,
        _exit,
        *,
        lattice_overrides=None,
        seq="exit2exit",
    ):
        self.optics_sequences.append(seq)
        dx_mm = 0.25
        dxp_mrad = -0.1
        for device, fields in (lattice_overrides or {}).items():
            delta = float(fields["K1"]) - self._base[device]
            dx_mm += self._dx_sensitivity[device] * delta
            dxp_mrad += self._dxp_sensitivity[device] * delta
        return (
            {
                "element_name": "_BEG_",
                "s_m": 0.0,
                "dx_m": 0.0,
                "dxp_rad": 0.0,
                "dy_m": 0.0,
                "dyp_rad": 0.0,
                "beta_x_m": 10.0,
                "beta_y_m": 12.0,
            },
            {
                "element_name": "BPM06",
                "s_m": 10.0,
                "dx_m": dx_mm / 1000.0,
                "dxp_rad": dxp_mrad / 1000.0,
                "dy_m": 0.0,
                "dyp_rad": 0.0,
                "beta_x_m": 15.0,
                "beta_y_m": 14.0,
            },
            {
                "element_name": "BPM07",
                "s_m": 15.0,
                "dx_m": dx_mm / 1200.0,
                "dxp_rad": dxp_mrad / 1000.0,
                "dy_m": 0.0,
                "dyp_rad": 0.0,
                "beta_x_m": 18.0,
                "beta_y_m": 16.0,
            },
        )


def test_optics_sequence_keeps_thick_entrance_exit_to_exit() -> None:
    backend = FakeModelBackend()
    backend.get_lattice_element = lambda _name: {
        "NAME": "Q_ENTRANCE",
        "TYPE": "QUAD",
        "L": "0.1",
    }

    assert model_response._optics_sequence(backend, "Q_ENTRANCE") == "exit2exit"


def test_half_bl01_design_source_reports_zero_design_reference_deltas(monkeypatch) -> None:
    context = load_app_context(
        "dispersion_correction",
        machine_id="half",
        control_backend="vm",
    )
    _, config = load_profile_run_config(context)
    backend = FakeModelBackend()
    monkeypatch.setattr(model_response, "build_model_backend", lambda _context: backend)

    result = calculate_model_response(context, config)

    assert backend.optics_sequences == ["ent2exit"]
    np.testing.assert_allclose(result.selected_values, [0.25, -0.1])
    np.testing.assert_allclose(result.design_reference_values, [0.25, -0.1])
    assert result.observable_components == ("dx", "dxp")
    assert result.observable_units == ("mm", "mrad")
    assert result.selected_curve.element_names[-1] == "BPM07"
    assert result.device_names == ("QL01", "QL02", "QL03", "QL04", "QL05", "QL06")
    assert all(value == 0.0 for value in result.design_reference_deltas.values())
    payload = model_response_to_dict(result)
    assert payload["observables"][1]["unit"] == "mrad"
    assert payload["design_reference_curve"]["element_names"][-1] == "BPM07"
    report = format_model_response(result)
    assert "Quadrupole design reference" in report
    assert "not a beam-based correction recommendation" in report
    assert "response matrix" not in report.lower()


def test_half_bl01_current_vm_snapshot_overlays_design_and_preserves_section_quads(
    monkeypatch,
) -> None:
    context = load_app_context(
        "dispersion_correction",
        machine_id="half",
        control_backend="vm",
    )
    _, config = load_profile_run_config(context)
    monkeypatch.setattr(model_response, "build_model_backend", lambda _context: FakeModelBackend())
    live_k1 = dict(FakeModelBackend._base)
    live_k1["QL01"] = 1.1
    live_k1["QL07"] = 2.0
    pv_values = {
        resolve_channel(context, name, "k1", "vm"): value
        for name, value in live_k1.items()
    }

    result = calculate_model_response(
        context,
        config,
        model_source="live",
        pv_reader=pv_values.__getitem__,
    )

    assert result.model_source == "live_from_vm"
    assert result.design_curve is not None
    np.testing.assert_allclose(result.design_curve.dx_mm[-2], 0.25)
    np.testing.assert_allclose(result.selected_values[0], 1.05)
    np.testing.assert_allclose(result.design_reference_values[0], 0.95)
    assert result.selected_k1["QL01"] == 1.1
    assert result.design_k1["QL01"] == 1.0
    assert result.design_reference_deltas["QL01"] == pytest.approx(-0.1)
    assert "QL07" not in result.device_names
    assert result.snapshot_metadata is not None
    assert len(result.snapshot_metadata["fields"]) == 12
    assert result.entrance_condition == "D=D'=0 assumed at BPM02"
    assert "QL07.K1 = 2" in format_model_response(result)


def test_design_reference_restores_large_ql03_ql04_deviation_without_solver_limit(
    monkeypatch,
) -> None:
    context = load_app_context(
        "dispersion_correction",
        machine_id="half",
        control_backend="vm",
    )
    _, config = load_profile_run_config(context)
    monkeypatch.setattr(model_response, "build_model_backend", lambda _context: FakeModelBackend())
    live_k1 = dict(FakeModelBackend._base)
    live_k1["QL03"] = 12.0
    live_k1["QL04"] = 12.0
    pv_values = {
        resolve_channel(context, name, "k1", "vm"): value
        for name, value in live_k1.items()
    }

    result = calculate_model_response(
        context,
        config,
        model_source="live",
        pv_reader=pv_values.__getitem__,
    )

    assert result.design_reference_deltas["QL03"] == pytest.approx(-11.0)
    assert result.design_reference_deltas["QL04"] == pytest.approx(-11.0)
    np.testing.assert_allclose(result.selected_values, [11.25, 6.5])
    np.testing.assert_allclose(result.design_reference_values, [0.25, -0.1])
