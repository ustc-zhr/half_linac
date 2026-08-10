from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, Dict, Optional

from .controller import IntegralHVController
from .data_buffer import DataBuffer, Sample
from .epics_client import BaseClient, EpicsClient
from .profile_runtime import required_signal_keys
from .reference import create_feedback_components, manual_reference, reference_row
from .safety import SafetyChecker, SafetyReference


def create_client(config: Dict[str, Any]) -> BaseClient:
    pvs = config.get("pvs", {})
    pv_names = {key: str(pvs[key]["name"]) for key in required_signal_keys(config)}
    return EpicsClient(pv_names)


class FeedbackEngine:
    """One selected feedback unit and channel in a fixed session mode."""

    def __init__(
        self,
        config: Dict[str, Any],
        *,
        mode: str,
        feedback_channel_id: str,
        client: Optional[BaseClient] = None,
        write_authorizer: Optional[Callable[[], None]] = None,
    ) -> None:
        if mode not in {"monitor", "feedback"}:
            raise ValueError("mode must be 'monitor' or 'feedback'")
        if mode == "feedback" and write_authorizer is None:
            raise ValueError("feedback mode requires a write authorizer")
        channel_ids = {str(channel["id"]) for channel in config["rf_channels"]}
        if feedback_channel_id not in channel_ids:
            raise ValueError(
                f"Feedback channel {feedback_channel_id!r} does not belong to this unit."
            )

        self.config = config
        self.mode = mode
        self.feedback_channel_id = feedback_channel_id
        self.write_authorizer = write_authorizer
        control = config["control"]
        self.sample_period_s = float(control["sample_period_s"])
        self.update_period_s = float(control["update_period_s"])
        self.average_window_s = float(control["average_window_s"])
        self.required_keys = required_signal_keys(config)
        self.buffer = DataBuffer(max_age_s=self.average_window_s + 10.0)
        self.client = client if client is not None else create_client(config)

        self.state = "RUNNING"
        self.reference: Optional[SafetyReference] = None
        self.controller: Optional[IntegralHVController] = None
        self.safety: Optional[SafetyChecker] = None
        self.last_update_time = 0.0
        self._reference_set_pending = True
        self._stopped = False

        result = manual_reference(config)
        if result.reference is None:
            raise ValueError(f"Invalid manual reference: {result.reason}")
        self._set_reference(result.reference)

    def _set_reference(self, ref: SafetyReference) -> None:
        self.reference = ref
        self.controller, self.safety = create_feedback_components(
            self.config,
            ref,
            self.feedback_channel_id,
        )

    def _read_sample(self) -> Sample:
        pv_values = self.client.read_many(self.required_keys)
        values = {key: pv_values[key].value for key in self.required_keys}
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
            "feedback_unit_id": self.config["feedback_unit_id"],
            "feedback_channel_id": self.feedback_channel_id,
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

        aggregate = self.buffer.aggregate(self.average_window_s)
        if aggregate is None:
            return [self._row("NO_AGGREGATE", reason="Not enough data")]
        pre_safety = self.safety.check_aggregate(aggregate)
        if not pre_safety.ok:
            return [self._fault_row(aggregate, pre_safety.reason)]

        output = self.controller.compute(
            aggregate,
            hv_setpoint_now=aggregate["hv_setpoint"],
        )
        output_row = {
            "error_rel": output.error_rel,
            "delta_hv_raw": output.delta_hv_raw,
            "delta_hv": output.delta_hv,
            "hv_next": output.hv_next,
            "saturated_step": output.saturated_step,
            "saturated_total": output.saturated_total,
        }
        post_safety = self.safety.check_aggregate(aggregate, hv_next=output.hv_next)
        if not post_safety.ok:
            return [self._fault_row(aggregate, post_safety.reason, **output_row)]

        if self.mode == "monitor":
            return [self._row("MONITOR", aggregate, **output_row)]
        try:
            assert self.write_authorizer is not None
            self.write_authorizer()
        except Exception as exc:  # noqa: BLE001
            return [
                self._fault_row(
                    aggregate,
                    f"write authorization failed: {exc}",
                    **output_row,
                )
            ]
        try:
            self.client.put("hv_setpoint", output.hv_next)
        except Exception as exc:  # noqa: BLE001
            return [self._fault_row(aggregate, f"HV write failed: {exc}", **output_row)]
        return [
            self._row(
                "CAPUT_HV",
                aggregate,
                reason="normal_feedback_update",
                **output_row,
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
