"""A clearly synthetic reader exercising the real capture path without CA."""
from datetime import datetime, timezone
import numpy as np
from half_linac.src.shared.machine_profile.resolver import resolve_channel

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
    def __init__(self, context, request=None, *, points=None, state=None, record_reads=True):
        if request is not None:
            points, _ = build_real_capture_plan(context, request)
            state, _ = prepare_model(context, request)
        self.values, self.reads = {}, []
        self.record_reads = record_reads
        self.images = {resolve_channel(context, d.element, "image", "real"):
                       context.profile.get_element(d.element).image_geometry["real"]
                       for d in (request.diagnostics if request is not None else ())}
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
            count = int(np.prod(owner.images[name]["shape"])) if name in owner.images else 1
            def get_with_metadata(self, **kwargs):
                assert kwargs["use_monitor"] is False
                if owner.record_reads:
                    owner.reads.append(name)
                if name in owner.images:
                    w, h = owner.images[name]["shape"]
                    x, y = np.arange(w)[None, :], np.arange(h)[:, None]
                    value = (10000*np.exp(-.5*(((x-w/2)/(w/12))**2+((y-h/2)/(h/10))**2))).astype(np.uint16).ravel()
                    unit = "counts"
                else:
                    value, unit = owner.values[name]
                return {"value": value, "units": unit, "timestamp": datetime.now(timezone.utc).timestamp(),
                        "status": 0, "severity": 0, "count": self.count}
            def put(self, *args, **kwargs):
                raise AssertionError("Synthetic real reader cannot write PVs")
            def clear_callbacks(self):
                pass
            def disconnect(self):
                pass
        return Pv()


def capture_demo(context, request, *, timeout_s=5.0, image_capture=None):
    reader = SimulatedRealReader(context, request)
    snapshot, evidence = capture_real_once(context, request, timeout_s=timeout_s, pv_factory=reader,
                                            poll_function=lambda interval: None, synthetic=True, image_capture=image_capture)
    return snapshot, evidence
