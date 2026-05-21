
from gotacc.algorithms.single_objective import BOOptimizer, TuRBOOptimizer
from test_function_single import *

if __name__ == "__main__":
####bo
    # # fuction for test
    # dim = 2
    # func_type = "rosenbrock" # "sphere", "rosenbrock", "ackley"
    # func, bounds = setup_objective(func_type, dim=dim)

    # opt = BOOptimizer(
    #     func=rosenbrock,
    #     bounds=bounds,
    #     kernel_type="rbf",# "rbf", "matern", "rbfwhite", "maternwhite"
    #     gp_restarts=5,
    #     acq="ucb",
    #     acq_para=2.0,
    #     acq_para_kwargs={"beta_strategy": "inv_decay", "beta_lam": 0.01}, # "exp_decay" "inv_decay" "stage" "fixed"
    #     acq_optimizer="optimize_acqf", # ['random', 'sobol', 'optimize_acqf']  optimize_acqf为botorch自带的多起点优化器(默认基于L-BFGS-B)
    #     acq_opt_kwargs={"num_restarts": 8, "raw_samples": 256, "n_candidates": 8192}, # only for 'random' and 'sobol': 'n_candidates'
    #     n_init=5,
    #     n_iter=20,
    #     random_state=120
    #     )

    # opt.optimize()

    # # Results display
    # opt.save_history()
    # opt.plot_convergence()

####turbo
    # Test configuration
    dim = 10
    func_type = "rosenbrock"
    func, bounds = setup_objective(func_type, dim=dim)

    # Create TuRBO optimizer
    turbo = TuRBOOptimizer(
        func=func,
        bounds=bounds,
        kernel_type="matern",
        gp_restarts=5,
        acq="ei",  # TuRBO typically uses EI
        acq_optimizer="sobol", # ['random', 'sobol', 'optimize_acqf']
        acq_opt_kwargs={"num_restarts": 8, "raw_samples": 512, "n_candidates": 8192},
        n_trust_regions=1,  # TuRBO-1
        success_tolerance=3,
        failure_tolerance=5,
        length_init=0.8,
        length_min=0.5**7,
        n_init=5,
        n_iter=15,
        random_state=99,
        verbose=True
    )

    # Run optimization
    turbo.optimize()

    # Display results
    turbo.save_history()
    turbo.plot_convergence()