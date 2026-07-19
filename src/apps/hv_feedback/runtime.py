from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, Dict, Optional

from .controller import IntegralHVController
from .data_buffer import DataBuffer, Sample
from .epics_client import BaseClient, EpicsClient
from .reference import create_feedback_components, manual_reference, reference_row
from .safety import SafetyChecker, SafetyReference


REQUIRED_KEYS = (
    "hv_setpoint",
    "hv_readback",
    "acc1_amp",
    "acc1_phase",
    "buncher_amp",
    "buncher_phase",
)


def create_client(cfg: Dict[str, Any]) -> BaseClient:
    pvs = cfg.get("pvs", {})
    pv_names = {key: str(pvs[key]["name"]) for key in REQUIRED_KEYS}
    return EpicsClient(pv_names)


class FeedbackEngine:
    """One feedback session with a fixed read-only or write-enabled operation."""

    def __init__(
        self,
        cfg: Dict[str, Any],
        *,
        mode: str,
        client: Optional[BaseClient] = None,
        write_authorizer: Optional[Callable[[], None]] = None,
    ) -> None:
        if mode not in {"monitor", "feedback"}:
            raise ValueError("mode must be 'monitor' or 'feedback'")
        if mode == "feedback" and write_authorizer is None:
            raise ValueError("feedback mode requires a write authorizer")

        self.cfg = cfg
        self.mode = mode
        self.write_authorizer = write_authorizer
        control = cfg["control"]
        self.sample_period_s = float(control["sample_period_s"])
        self.update_period_s = float(control["update_period_s"])
        self.average_window_s = float(control["average_window_s"])
        self.buffer = DataBuffer(max_age_s=self.average_window_s + 10.0)
        self.client = client if client is not None else create_client(cfg)

        self.state = "RUNNING"
        self.reference: Optional[SafetyReference] = None
        self.controller: Optional[IntegralHVController] = None
        self.safety: Optional[SafetyChecker] = None
        self.last_update_time = 0.0
        self._reference_set_pending = True
        self._stopped = False

        result = manual_reference(cfg)
        if result.reference is None:
            raise ValueError(f"Invalid manual reference: {result.reason}")
        self._set_reference(result.reference)

    def _set_reference(self, ref: SafetyReference) -> None:
        self.reference = ref
        self.controller, self.safety = create_feedback_components(self.cfg, ref)

    def _read_sample(self) -> Sample:
        pv_values = self.client.read_many(REQUIRED_KEYS)
        values = {key: pv_values[key].value for key in REQUIRED_KEYS}
        ok = all(value.ok and value.value is not None for value in pv_values.values())
        errors = {
            key: value.error
            for key, value in pv_values.items()
            if not value.ok or value.value is None
        }
        return Sample(timestamp=time.time(), values=values, ok=ok, errors=errors)

    def _row(
        self,
        event: str,
        values: Optional[Dict[str, object]] = None,
        **extra: object,
    ) -> Dict[str, object]:
        row: Dict[str, object] = {
            "timestamp": time.time(),
            "mode": self.mode,
            "state": self.state,
            "event": event,
        }
        if values:
            row.update(values)
        row.update(reference_row(self.reference))
        row.update(extra)
        return row

    def _fault_row(
        self,
        values: Dict[str, object],
        reason: str,
        **extra: object,
    ) -> Dict[str, object]:
        self.state = "HOLD"
        return self._row("HOLD", values, reason=reason, **extra)

    def _control_update_rows(self) -> list[Dict[str, object]]:
        if self.controller is None or self.safety is None:
            raise RuntimeError("Controller/safety not initialized")

        agg = self.buffer.aggregate(self.average_window_s)
        if agg is None:
            return [self._row("NO_AGGREGATE", reason="Not enough data")]

        pre_safety = self.safety.check_aggregate(agg)
        if not pre_safety.ok:
            return [self._fault_row(agg, pre_safety.reason)]

        out = self.controller.compute(agg, hv_setpoint_now=agg["hv_setpoint"])
        out_row = {
            "error_rel": out.error_rel,
            "delta_hv_raw": out.delta_hv_raw,
            "delta_hv": out.delta_hv,
            "hv_next": out.hv_next,
            "saturated_step": out.saturated_step,
            "saturated_total": out.saturated_total,
        }
        post_safety = self.safety.check_aggregate(agg, hv_next=out.hv_next)
        if not post_safety.ok:
            return [self._fault_row(agg, post_safety.reason, **out_row)]

        if self.mode == "monitor":
            return [self._row("MONITOR", agg, **out_row)]

        try:
            assert self.write_authorizer is not None
            self.write_authorizer()
        except Exception as exc:  # noqa: BLE001
            return [
                self._fault_row(
                    agg,
                    f"write authorization failed: {exc}",
                    **out_row,
                )
            ]

        try:
            self.client.put("hv_setpoint", out.hv_next)
        except Exception as exc:  # noqa: BLE001
            return [self._fault_row(agg, f"HV write failed: {exc}", **out_row)]
        return [
            self._row(
                "CAPUT_HV",
                agg,
                reason="normal_feedback_update",
                **out_row,
            )
        ]

    def step(self) -> list[Dict[str, object]]:
        if self._stopped:
            return []
        rows: list[Dict[str, object]] = []
        if self._reference_set_pending:
            rows.append(self._row("REFERENCE_SET", reason="manual_reference"))
            self._reference_set_pending = False

        sample = self._read_sample()
        self.buffer.append(sample)
        rows.append(
            self._row(
                "SAMPLE",
                sample.values,
                reason="" if sample.ok else str(sample.errors),
            )
        )

        if self.safety is None:
            raise RuntimeError("Safety checker not initialized")
        sample_safety = self.safety.check_sample_ok(sample.ok, sample.errors)
        if not sample_safety.ok:
            rows.append(self._fault_row(sample.values, sample_safety.reason))
            return rows
        if self.state == "HOLD":
            return rows

        self.state = "RUNNING"
        now = time.time()
        if now - self.last_update_time >= self.update_period_s:
            rows.extend(self._control_update_rows())
            self.last_update_time = now
        return rows

    def stop_row(self) -> Dict[str, object]:
        self._stopped = True
        self.state = "STOPPED"
        return self._row("STOP")
