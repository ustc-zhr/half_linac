from __future__ import annotations

from dataclasses import replace

from half_linac.src.shared.control_point import (
    ControlDefaults,
    control_defaults_from_profile,
    WatchdogStatus,
    collect_control_points,
    evaluate_watchdog,
    sample_watchdog,
)
from half_linac.src.shared.machine_profile import load_profile


def test_control_points_do_not_guess_missing_safety_parameters() -> None:
    profile = load_profile("half")
    real = collect_control_points(profile, "real")
    vm = collect_control_points(profile, "vm")

    xc00 = next(point for point in real if point.key == "XC00/current_set")
    assert xc00.readback_pv == "IN:PS:LE07:XC00:current:ai"
    assert xc00.limit.describe() == "[-5, 5] A"
    assert xc00.tolerance == 0.01
    assert "readback tolerance is not configured" not in xc00.configuration_issues
    assert vm == ()
    assert not any(point.logical_channel.endswith("_readback") for point in real)


def test_half_llrf_control_points_use_phase_and_amplitude_readbacks() -> None:
    points = {
        point.key: point
        for point in collect_control_points(load_profile("half"), "real")
    }

    assert points["LLRFPB/phase_set"].readback_pv == "IN:MW:LLRFPB:GET_PHASE"
    assert points["LLRFPB/amplitude_set"].readback_pv == "IN:MW:LLRFPB:GET_AMP"


def test_watchdog_uses_configured_tolerance_inclusively() -> None:
    point = next(
        point
        for point in collect_control_points(
            load_profile("half"),
            "real",
            defaults=ControlDefaults(
                tolerance_by_kind_channel={"corr/current_set": 0.01}
            ),
        )
        if point.key == "XC00/current_set"
    )
    assert evaluate_watchdog(point, 1.0, 1.01).status == WatchdogStatus.MATCH
    assert evaluate_watchdog(point, 1.0, 1.011).status == WatchdogStatus.MISMATCH
    assert evaluate_watchdog(point, 1.0, float("nan")).status == WatchdogStatus.UNAVAILABLE
    assert evaluate_watchdog(replace(point, tolerance=None), 1.0, 1.0).status == WatchdogStatus.NOT_CONFIGURED


def test_layered_defaults_apply_broad_rules_and_allow_rare_overrides() -> None:
    defaults = ControlDefaults(
        tolerance=1.0,
        tolerance_by_kind={"corr": 0.1},
        tolerance_by_channel={"current_set": 0.05},
        tolerance_by_kind_channel={"corr/current_set": 0.01},
        tolerance_by_point={"XC00/current_set": 0.005},
        settle_s=0.2,
        timeout_s=3.0,
    )
    points = collect_control_points(load_profile("half"), "real", defaults=defaults)
    xc00 = next(point for point in points if point.key == "XC00/current_set")
    xc01 = next(point for point in points if point.key == "XC01/current_set")

    assert xc00.tolerance == 0.005
    assert xc01.tolerance == 0.01
    assert xc00.settle_s == 0.2
    assert xc00.timeout_s == 3.0


def test_profile_control_defaults_are_loaded_for_both_consumers() -> None:
    profile = load_profile("half")
    defaults = control_defaults_from_profile(profile, "real")

    assert defaults.settle_s == 0.3
    assert defaults.timeout_s == 5.0
    assert defaults.tolerance_by_kind_channel == {
        "corr/current_set": 0.01,
        "quad/current_set": 0.05,
        "bend/current_set": 0.05,
        "solenoid/current_set": 0.01,
        "modulator/voltage_set": 20.0,
    }


def test_sample_watchdog_reads_each_pair_and_keeps_failures_structured() -> None:
    point = next(
        point
        for point in collect_control_points(
            load_profile("half"),
            "real",
            defaults=ControlDefaults(
                tolerance_by_kind_channel={"corr/current_set": 0.01}
            ),
        )
        if point.key == "XC00/current_set"
    )
    reads = []

    def read(pv_name):
        reads.append(pv_name)
        return {point.setpoint_pv: 1.0, point.readback_pv: 1.005}[pv_name]

    result = sample_watchdog((point,), read)

    assert result[0].status == WatchdogStatus.MATCH
    assert reads == [point.setpoint_pv, point.readback_pv]
