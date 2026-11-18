"""A clearly synthetic reader exercising the real capture path without CA."""
from datetime import datetime, timezone

from .models import Diagnostic, ShadowRequest
from .pipeline import prepare_model
from .real_capture import build_real_capture_plan, capture_real_once


def demo_request():
    return ShadowRequest(
        model_line="ALL_MAIN", reference_element="QT01", reference_edge="entrance",
        energy_mev=2200, x_moments=(4e-6, 0, 1e-8), y_moments=(3e-6, 0, 2e-8),
        beam_basis="assumed", beam_provenance="SYNTHETIC offline real-capture demo; fabricated beam and observations, not real measurements",
        diagnostics=tuple(Diagnostic(screen, channel, "mm", "rms")
                          for screen in ("PRF06", "PRF07", "PRF08", "PRF09") for channel in ("sigx", "sigy")))


class SimulatedRealReader:
    def __init__(self, context, request):
        points, _ = build_real_capture_plan(context, request)
        state, _ = prepare_model(context, request)
        self.values, self.reads = {}, []
        for index, point in enumerate(points):
            endpoint = point.endpoint
            if endpoint.element_kind == "quad":
                number = float(state["lattice"][endpoint.element_id]["K1"])
                self.values[endpoint.pv_name] = (number + (0.02 if endpoint.logical_channel == "K1" else 0), "m^-2")
            else:
                self.values[endpoint.pv_name] = (1.0 + index % 8 * 0.1, "mm")

    def __call__(self, name):
        owner = self
        class Pv:
            connected = True
            count = 1
            def get_with_metadata(self, **kwargs):
                assert kwargs["use_monitor"] is False
                owner.reads.append(name)
                value, unit = owner.values[name]
                return {"value": value, "units": unit, "timestamp": datetime.now(timezone.utc).timestamp(),
                        "status": 0, "severity": 0, "count": 1}
            def put(self, *args, **kwargs):
                raise AssertionError("Synthetic real reader cannot write PVs")
            def clear_callbacks(self):
                pass
            def disconnect(self):
                pass
        return Pv()


def capture_demo(context, request, *, timeout_s=5.0):
    reader = SimulatedRealReader(context, request)
    snapshot, evidence = capture_real_once(context, request, timeout_s=timeout_s, pv_factory=reader,
                                            poll_function=lambda interval: None, synthetic=True)
    return snapshot, evidence
