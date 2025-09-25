import numpy as np
from scipy import optimize
from scipy.stats import qmc  
import cma

from acquisition_function import *


def optimize_acq_local(gpr, x0, bounds, acq_fn, minimize=True):
    # x0: 1D seed, bounds: array shape (d,2)
    d = x0.size
    lb = bounds[:,0]
    ub = bounds[:,1]

    def obj(x):
        # objective for scipy.minimize (we keep argmin convention in your acquisition)
        return acq_fn(gpr, x.reshape(1,-1))

    # use L-BFGS-B with numeric grad (scipy will approximate jacobian)
    res = optimize.minimize(obj, x0, bounds=optimize.Bounds(lb, ub),
                            method='L-BFGS-B', options={'maxiter':200})
    return res.x, res.fun

# ----- 随机采样优化器  -----
def optimize_acquisition_random(gpr, bounds, acq, beta, xi, minimize, Y_best,
                                n_candidates=1000):
    dim = bounds.shape[0]
    lb, ub = bounds[:,0], bounds[:,1]
    Xcand = np.random.uniform(lb, ub, size=(n_candidates, dim))
    vals = acquisition(gpr, Xcand, acq=acq, beta=beta, xi=xi,
                       minimize=minimize, Y_best=Y_best)
    best_idx = np.argmin(vals)
    return Xcand[best_idx].reshape(1, -1)

# ----- Sobol采样优化器  -----
def optimize_acquisition_sobol(gpr, bounds, acq, beta, xi, minimize, Y_best,
                                n_candidates=1000):
    dim = bounds.shape[0]
    lb, ub = bounds[:,0], bounds[:,1]
    sampler = qmc.Sobol(d=dim, scramble=True, seed=0)
    Xcand_unit = sampler.random(n_candidates)
    Xcand = qmc.scale(Xcand_unit, bounds[:,0], bounds[:,1])
    vals = acquisition(gpr, Xcand, acq=acq, beta=beta, xi=xi,
                       minimize=minimize, Y_best=Y_best)
    best_idx = np.argmin(vals)
    return Xcand[best_idx].reshape(1, -1)


# ----- Sobol+局部优化采样优化器  -----
def optimize_acquisition_sobol_local(gpr, bounds, acq, beta, xi, minimize, Y_best,
                                n_candidates=1000):
    dim = bounds.shape[0]
    lb, ub = bounds[:,0], bounds[:,1]
    sampler = qmc.Sobol(d=dim, scramble=True, seed=0)
    Xcand_unit = sampler.random(n_candidates)
    Xcand = qmc.scale(Xcand_unit, bounds[:,0], bounds[:,1])
    vals = acquisition(gpr, Xcand, acq=acq, beta=beta, xi=xi,
                       minimize=minimize, Y_best=Y_best)
    best_idx = np.argmin(vals)

    k = 5
    seed_idx = np.argsort(vals)[:k]  # argmin convention
    seeds = Xcand[seed_idx]
    #
    best_val = np.inf
    best_x = None
    for s in seeds:
        x_opt, val = optimize_acq_local(gpr, s, bounds, 
                                        acq_fn=lambda model, xx: acquisition(model, xx, acq=acq, beta=beta, xi=xi, minimize=True, Y_best=Y_best))
        if val < best_val:
            best_val = val
            best_x = x_opt

    return best_x.reshape(1,-1)


# ----- CMA-ES 优化器 -----
def optimize_acquisition_cma(gpr, bounds, acq, beta, xi, minimize, Y_best,
                             popsize=20, max_iter=100):
    dim = bounds.shape[0]
    lb, ub = bounds[:,0], bounds[:,1]
    x0 = 0.5 * (lb + ub)
    sigma0 = 0.25 * (ub - lb).mean()

    def obj(xx):
        xx = np.clip(xx, lb, ub)
        return float(acquisition(gpr, xx.reshape(1,-1),
                                 acq=acq, beta=beta, xi=xi,
                                 minimize=minimize, Y_best=Y_best))

    es = cma.CMAEvolutionStrategy(x0.tolist(), sigma0,
                                  {'popsize': popsize, 'bounds': [lb.tolist(), ub.tolist()]})
    best_x, best_val = None, np.inf
    for _ in range(max_iter):
        Xs = es.ask()
        vals = [obj(np.array(xx)) for xx in Xs]
        es.tell(Xs, vals)
        es.disp()
        idx = int(np.argmin(vals))
        if vals[idx] < best_val:
            best_val = vals[idx]
            best_x = np.array(Xs[idx])
    return best_x.reshape(1,-1)

# ----- GA 优化器 -----
def optimize_acquisition_ga(gpr, bounds, acq, beta, xi, minimize, Y_best,
                            popsize=50, generations=100, crossover_rate=0.8, mutation_rate=0.1):
    dim = bounds.shape[0]
    lb, ub = bounds[:,0], bounds[:,1]
    pop = np.random.uniform(lb, ub, size=(popsize, dim))

    def fitness(X):
        return np.array([acquisition(gpr, x.reshape(1,-1),
                                     acq=acq, beta=beta, xi=xi,
                                     minimize=minimize, Y_best=Y_best)
                         for x in X]).ravel()

    for gen in range(generations):
        fit = fitness(pop)
        # 选择（锦标赛）
        idx = np.random.randint(0, popsize, size=(popsize, 2))
        winners = np.where(fit[idx[:,0]] < fit[idx[:,1]], idx[:,0], idx[:,1])
        parents = pop[winners]

        # 交叉
        offspring = parents.copy()
        for i in range(0, popsize, 2):
            if np.random.rand() < crossover_rate and i+1 < popsize:
                cp = np.random.randint(1, dim)
                offspring[i, cp:], offspring[i+1, cp:] = \
                    offspring[i+1, cp:].copy(), offspring[i, cp:].copy()

        # 变异
        mutation = np.random.rand(*offspring.shape) < mutation_rate
        offspring = np.clip(offspring + mutation * np.random.normal(0, 0.1, offspring.shape),
                            lb, ub)
        pop = offspring

    final_fit = fitness(pop)
    best_idx = np.argmin(final_fit)
    return pop[best_idx].reshape(1,-1)