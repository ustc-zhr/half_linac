from __future__ import annotations

from .types import FeatureRecipe, TrainingTaskDefinition


UPSTREAM_CHANNELS = (
    "bpm02_x",
    "bpm03_x",
    "bpm02_s",
    "bpm03_s",
    "bpm04_s",
)

RF_HV_CHANNELS = (
    "gun_hv",
    "gun_current",
    "ch1_asum",
    "ch2_asum",
    "ch3_asum",
    "ch4_asum",
    "shb_ref_amp",
    "shb_ref_pha",
    "shb_vm_amp",
    "shb_vm_pha",
    "shb_pplus_amp",
    "shb_pplus_pha",
    "shb_pc_amp",
    "shb_pc_pha",
    "kly1_pc_amp",
    "kly1_ref_pha",
    "kly1_vm_amp",
    "kly1_vm_pha",
    "kly1_ssa_amp",
    "kly1_ssa_pha",
    "kly1_for_amp",
    "kly1_for_pha",
    "kly1_acc1_amp",
    "kly1_acc1_pha",
    "kly1_acc1load_amp",
    "kly1_acc1load_pha",
    "kly1_bc_amp",
    "kly1_bc_pha",
    "kly1_bcload_amp",
    "kly1_bcload_pha",
    "modulator_hv1_set__readback",
)

TEMPERATURE_CHANNELS = (
    "acc_t1_temperature",
    "acc_t2_temperature",
    "water_25degree_temperature",
    "jsg11_temperature",
)

FIR_BPM04_FORECAST = TrainingTaskDefinition(
    id="fir_bpm04_forecast",
    version="1",
    label="FIR BPM04 Next-Window Forecast",
    machine="IRFEL",
    target_channel="bpm04_x",
    gate_channel="bpm04_s",
    required_channels=("bpm02_x", "bpm03_x", "bpm04_x", "bpm04_s"),
    feature_recipes=(
        FeatureRecipe("target_history", "Target history", (), True),
        FeatureRecipe("upstream", "Upstream BPM", UPSTREAM_CHANNELS, False),
        FeatureRecipe("target_upstream", "Target + upstream BPM", UPSTREAM_CHANNELS, True),
        FeatureRecipe("rf_hv", "RF / HV", RF_HV_CHANNELS, False),
        FeatureRecipe(
            "all",
            "All compatible",
            UPSTREAM_CHANNELS + RF_HV_CHANNELS + TEMPERATURE_CHANNELS,
            True,
        ),
    ),
)

TASK_REGISTRY = {FIR_BPM04_FORECAST.id: FIR_BPM04_FORECAST}


def get_training_task(task_id: str) -> TrainingTaskDefinition:
    try:
        return TASK_REGISTRY[str(task_id)]
    except KeyError as exc:
        raise ValueError(f"Unsupported training task: {task_id}") from exc
