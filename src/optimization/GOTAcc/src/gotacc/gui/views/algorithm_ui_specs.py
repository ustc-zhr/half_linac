from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class ParameterUiSpec:
    label: str
    section: str = "Advanced"
    choices: tuple[str, ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    decimals: int = 6
    hidden: bool = False


PARAMETER_UI_SPECS: dict[str, ParameterUiSpec] = {
    "n_init": ParameterUiSpec("Initial Samples", "Basic", minimum=1),
    "acq": ParameterUiSpec("Acquisition", "Basic"),
    "acq_para": ParameterUiSpec("Acquisition Parameter", "Basic", minimum=0.0),
    "q_batch_size": ParameterUiSpec("q-Batch Size", "Basic", minimum=1),
    "n_trust_regions": ParameterUiSpec("Trust Regions", "Basic", minimum=1),
    "length_init": ParameterUiSpec("Initial Region Length", "Basic", minimum=0.0, maximum=1.0),
    "step": ParameterUiSpec("Initial Step", "Basic", minimum=0.0, maximum=1.0),
    "noise": ParameterUiSpec("Noise Estimate", "Basic", minimum=0.0),
    "pop_size": ParameterUiSpec("Population Size", "Basic", minimum=2),
    "evals_per_gen": ParameterUiSpec("Evaluations per Generation", "Basic", minimum=1),
    "n_generations": ParameterUiSpec("Generation Limit", "Basic", minimum=0),
    "acq_mode": ParameterUiSpec("Acquisition Mode", "Basic"),
    "ref_point": ParameterUiSpec("Reference Point", "Basic"),
    "archive_size": ParameterUiSpec("Archive Size", "Basic", minimum=1),
    "kernel_type": ParameterUiSpec("Kernel", choices=("matern", "rbf")),
    "gp_restarts": ParameterUiSpec("GP Fit Restarts", minimum=0),
    "acq_para_kwargs": ParameterUiSpec("Acquisition Schedule"),
    "acq_optimizer": ParameterUiSpec(
        "Acquisition Optimizer",
        choices=("optimize_acqf", "random", "sobol"),
    ),
    "acq_opt_kwargs": ParameterUiSpec("Acquisition Optimizer Options"),
    "success_tolerance": ParameterUiSpec("Success Tolerance", minimum=1),
    "failure_tolerance": ParameterUiSpec("Failure Tolerance", minimum=1),
    "length_min": ParameterUiSpec("Minimum Region Length", minimum=0.0, maximum=1.0),
    "tol": ParameterUiSpec("Relative Tolerance", minimum=0.0, decimals=10),
    "maxIt": ParameterUiSpec("Direction Cycle Limit", minimum=1),
    "m1": ParameterUiSpec("Mutation Offspring", minimum=0),
    "m2": ParameterUiSpec("Crossover Offspring", minimum=0),
    "m3": ParameterUiSpec("PSO Offspring", minimum=0),
    "ucb_beta": ParameterUiSpec("UCB Exploration", minimum=0.0),
    "ucb_beta_kwargs": ParameterUiSpec("UCB Schedule"),
    "use_all_history_for_gp": ParameterUiSpec("Use All GP History"),
    "gp_history_max": ParameterUiSpec("GP History Limit", minimum=1),
    "mutation_prob": ParameterUiSpec("Mutation Probability", minimum=0.0, maximum=1.0),
    "crossover_prob": ParameterUiSpec("Crossover Probability", minimum=0.0, maximum=1.0),
    "mutation_eta": ParameterUiSpec("Mutation Eta", minimum=0.0),
    "crossover_eta": ParameterUiSpec("Crossover Eta", minimum=0.0),
    "w": ParameterUiSpec("Inertia Weight (w)", minimum=0.0),
    "c1": ParameterUiSpec("Self-Best Influence (c1)", minimum=0.0),
    "c2": ParameterUiSpec("Group-Best Influence (c2)", minimum=0.0),
    "device": ParameterUiSpec("Compute Device", "Execution", choices=("cpu", "cuda")),
    "dtype": ParameterUiSpec("Numeric Precision", "Execution", choices=("float64", "float32")),
    "verbose": ParameterUiSpec("Optimizer Progress Logs", "Execution"),
    # Objective direction is owned by the task objective configuration. Keep a
    # stored legacy value intact without exposing a second direction control.
    "maximize": ParameterUiSpec("Maximize", hidden=True),
}


ALGORITHM_PARAMETER_OVERRIDES: dict[str, dict[str, ParameterUiSpec]] = {
    "BO": {
        "acq": replace(PARAMETER_UI_SPECS["acq"], choices=("ucb", "ei", "pi")),
    },
    "ConsBO": {
        "acq": replace(PARAMETER_UI_SPECS["acq"], choices=("ei",)),
    },
    "MOBO": {
        "acq": replace(PARAMETER_UI_SPECS["acq"], choices=("ehvi", "qehvi", "qnehvi")),
    },
    "ConsMOBO": {
        "acq": replace(PARAMETER_UI_SPECS["acq"], choices=("qehvi", "qnehvi")),
    },
    "MGGPO": {
        "acq_mode": replace(PARAMETER_UI_SPECS["acq_mode"], choices=("ucb", "ehvi", "combine")),
    },
    "ConsMGGPO": {
        "acq_mode": replace(PARAMETER_UI_SPECS["acq_mode"], choices=("ucb", "ehvi", "combine")),
    },
    "MGGPO-SO": {
        "acq_mode": replace(PARAMETER_UI_SPECS["acq_mode"], choices=("ucb", "ei", "pi", "combine")),
    },
    "ConsMGGPO-SO": {
        "acq_mode": replace(PARAMETER_UI_SPECS["acq_mode"], choices=("ucb", "ei", "pi", "combine")),
    },
}


def parameter_ui_spec(algorithm: str, name: str) -> ParameterUiSpec:
    override = ALGORITHM_PARAMETER_OVERRIDES.get(str(algorithm), {}).get(str(name))
    if override is not None:
        return override
    return PARAMETER_UI_SPECS.get(
        str(name),
        ParameterUiSpec(str(name).replace("_", " ").strip().title()),
    )


def visible_parameter_names(algorithm: str, names: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    return tuple(name for name in names if not parameter_ui_spec(algorithm, name).hidden)
