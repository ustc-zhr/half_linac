import numpy as np
from scipy.optimize import differential_evolution
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import RBF, ConstantKernel as C
from scipy.stats import norm
import time
import matplotlib.pyplot as plt

# -------------------------
# utilities: pareto / hv
# -------------------------
def is_pareto_efficient(points):
    # returns mask of pareto-efficient points (minimization)
    pts = np.asarray(points)
    n = pts.shape[0]
    is_efficient = np.ones(n, dtype=bool)
    for i in range(n):
        if is_efficient[i]:
            dominated = np.all(pts <= pts[i], axis=1) & np.any(pts < pts[i], axis=1)
            is_efficient[dominated] = False
    return is_efficient

def hypervolume_estimate(points, ref_point, n_samples=2000, rng=None):
    """
    Monte Carlo estimate of hypervolume dominated by Pareto front wrt reference point.
    Assumes minimization objectives; hypervolume is volume of objective space dominated
    by Pareto front and bounded by ref_point.
    points: (N_points, P)
    ref_point: length P (must be dominated by all points of interest)
    """
    rng = np.random.default_rng(rng)
    points = np.asarray(points)
    P = points.shape[1]
    # domain box: lower bound = min observed values (or zeros), upper = ref_point
    lower = np.min(points, axis=0)
    upper = np.asarray(ref_point)
    # if any lower >= upper, return 0
    if np.any(lower >= upper):
        # shrink lower a bit
        lower = np.minimum(lower, upper - 1e-6)
    samples = rng.random((n_samples, P)) * (upper - lower) + lower  # uniform in box
    # dominated if exists a point that is <= sample in all dims
    dominated = np.zeros(n_samples, dtype=bool)
    for p in points:
        dominated |= np.all(p <= samples, axis=1)
    frac = dominated.mean()
    box_vol = np.prod(upper - lower)
    return frac * box_vol

# exact 2D hypervolume (fast) for diagnostics
def hv_2d_exact(points, ref):
    pts = np.array(points)
    # filter points that dominate ref? we treat ref as upper-right
    P = pts[pts[:,0] <= ref[0]]
    if P.size == 0:
        return 0.0
    # sort by first objective ascending
    P = P[np.argsort(P[:,0])]
    hv = 0.0
    cur_x = ref[0]
    for x,y in P[::-1]:
        dx = cur_x - x
        if dx > 0:
            hv += dx * (ref[1] - y)
            cur_x = x
    return hv

# -------------------------
# MOBO class
# -------------------------
class MultiObjectiveBO:
    def __init__(self, bounds, n_objectives, kernel=None, noise=1e-6, rng=None):
        """
        bounds: list of (min, max) for each input dimension
        n_objectives: number of objective functions
        kernel: sklearn kernel or None (default RBF)
        noise: gaussian noise level for GP (scalar)
        """
        self.bounds = np.asarray(bounds)
        self.dim = self.bounds.shape[0]
        self.n_obj = n_objectives
        self.rng = np.random.default_rng(rng)
        self.noise = noise
        if kernel is None:
            kernel = C(1.0, (1e-3, 1e3)) * RBF(length_scale=np.ones(self.dim), length_scale_bounds=(1e-3, 1e3))
        self.kernel = kernel
        # GP per objective
        self.gps = [GaussianProcessRegressor(kernel=self.kernel, alpha=noise, normalize_y=True, n_restarts_optimizer=5, random_state=self.rng.integers(1e9)) for _ in range(self.n_obj)]
        self.X = None  # (N, dim)
        self.Y = None  # (N, n_obj)  (minimization)
        self.constraint_gp = None  # optional GP for constraint g(x) <= h

    def initialize(self, X_init, Y_init):
        X_init = np.asarray(X_init)
        Y_init = np.asarray(Y_init)
        assert X_init.ndim == 2 and Y_init.ndim == 2
        assert X_init.shape[0] == Y_init.shape[0]
        assert Y_init.shape[1] == self.n_obj
        self.X = X_init.copy()
        self.Y = Y_init.copy()
        self._fit_gps()

    def _fit_gps(self):
        for i in range(self.n_obj):
            self.gps[i].fit(self.X, self.Y[:, i])
        if self.constraint_gp is not None:
            self.constraint_gp.fit(self.X, self.constraint_Y)

    def add_observation(self, x, y, g_obs=None):
        x = np.atleast_2d(x)
        y = np.atleast_1d(y)
        if self.X is None:
            self.X = x.copy()
            self.Y = np.atleast_2d(y).reshape(1, -1)
        else:
            self.X = np.vstack([self.X, x])
            self.Y = np.vstack([self.Y, y])
        if g_obs is not None:
            if not hasattr(self, "constraint_Y"):
                self.constraint_Y = np.array([g_obs])
            else:
                self.constraint_Y = np.hstack([self.constraint_Y, np.atleast_1d(g_obs)])
        self._fit_gps()

    def predict(self, x):
        """
        Predict mean and std for each objective at x (minimization).
        x: (k, dim) or (dim,)
        returns: mu (k, n_obj), sigma (k, n_obj)
        """
        x = np.atleast_2d(x)
        mus = []
        sigs = []
        for gp in self.gps:
            mu, std = gp.predict(x, return_std=True)
            mus.append(mu)
            sigs.append(std)
        mu = np.vstack(mus).T
        sigma = np.vstack(sigs).T
        return mu, sigma

    # -------------------------
    # acquisition functions
    # -------------------------
    def ucb_hvi(self, x, beta=0.01, ref_point=None, hv_mc_samples=2000):
        """
        Upper confidence bound hypervolume improvement (minimization).
        compute y_ucb = mu - sqrt(beta)*sigma (optimistic for minimization)
        estimate hypervolume improvement HI(P, y_ucb).
        """
        x = np.atleast_2d(x)
        mu, sigma = self.predict(x)
        y_ucb = mu - np.sqrt(beta) * sigma  # shape (1, n_obj) if x single
        # current pareto set in objective space
        cur_pareto = self.Y[is_pareto_efficient(self.Y)]
        if ref_point is None:
            # default reference: a bit larger than max observed
            ref_point = np.max(self.Y, axis=0) + 0.1 * (np.ptp(self.Y, axis=0) + 1e-6)
        # hv of current
        hv0 = hypervolume_estimate(cur_pareto, ref_point, n_samples=hv_mc_samples, rng=self.rng)
        # hv with candidate
        hv1 = hypervolume_estimate(np.vstack([cur_pareto, y_ucb[0]]), ref_point, n_samples=hv_mc_samples, rng=self.rng)
        return float(hv1 - hv0)

    def ehvi_mc(self, x, mc_samples=64, ref_point=None, hv_mc_samples=2000):
        """
        Monte Carlo EHVI approximation:
          - sample y from predictive Gaussian per objective independently,
          - for each sample compute hypervolume improvement, average.
        Note: expensive. Use small mc_samples for quick runs.
        """
        x = np.atleast_2d(x)
        mu, sigma = self.predict(x)  # shape (1, n_obj)
        mu = mu[0]; sigma = sigma[0]
        cur_pareto = self.Y[is_pareto_efficient(self.Y)]
        if ref_point is None:
            ref_point = np.max(self.Y, axis=0) + 0.1 * (np.ptp(self.Y, axis=0) + 1e-6)
        hv0 = hypervolume_estimate(cur_pareto, ref_point, n_samples=hv_mc_samples, rng=self.rng)
        draws = self.rng.normal(loc=mu[None,:], scale=sigma[None,:], size=(mc_samples, self.n_obj))
        hi_vals = []
        for y in draws:
            hv1 = hypervolume_estimate(np.vstack([cur_pareto, y]), ref_point, n_samples=hv_mc_samples, rng=self.rng)
            hi_vals.append(max(0.0, hv1 - hv0))
        return float(np.mean(hi_vals))

    # optionally apply constraint probability multiplication and proximal penalty
    def acquisition(self, x, method='ehvi', beta=0.01, pref_mask=None, constraint=None,
                    prox_xy=None, prox_precision=4.0, ehvi_mc_samples=64, hv_mc_samples=2000):
        """
        method: 'ehvi' or 'ucb'
        constraint: tuple (g_gp, h) or None. If provided, g_gp is a GP that predicts g(x),
                    and h is threshold: require g(x) <= h. We multiply acquisition by P[g(x)<=h].
                    Alternatively, if a constraint GP wasn't provided, you can pass None.
        prox_xy: last evaluated x0 (for proximal), or None
        prox_precision: scalar or matrix controlling proximal penalty
        """
        x = np.atleast_2d(x)
        # compute base acquisition
        if method == 'ucb':
            base = np.array([self.ucb_hvi(xx, beta=beta, ref_point=None, hv_mc_samples=hv_mc_samples) for xx in x])
        else:
            base = np.array([self.ehvi_mc(xx, mc_samples=ehvi_mc_samples, ref_point=None, hv_mc_samples=hv_mc_samples) for xx in x])
        # apply constraint probability if provided
        if constraint is not None:
            g_gp, h = constraint
            # g_gp must have a predict method that returns mu, sigma
            mu_g, sigma_g = g_gp.predict(x, return_std=True)
            # P[g(x) <= h] = Phi((h - mu)/sigma)
            p_ok = norm.cdf((h - mu_g) / (sigma_g + 1e-12))
            base = base * p_ok
        # proximal
        if prox_xy is not None:
            x0 = np.atleast_1d(prox_xy)
            # compute multivariate gaussian weight centered at x0 with precision prox_precision * I
            diff = x - x0
            # if prox_precision scalar
            if np.isscalar(prox_precision):
                w = np.exp(-0.5 * np.sum(diff**2, axis=1) * prox_precision)
            else:
                # prox_precision as matrix
                L = prox_precision
                w = np.exp(-0.5 * np.einsum('ij,ij->i', diff @ L, diff))
            base = base * w
        return base

    # -------------------------
    # propose next x by optimizing acquisition
    # -------------------------
    def propose_location(self, acq_method='ehvi', n_restarts=8, beta=0.01, constraint=None,
                         prox_xy=None, prox_precision=4.0, ehvi_mc_samples=64, hv_mc_samples=2000,
                         popsize=15, maxiter=200):
        """
        Optimize acquisition via differential_evolution (global).
        """
        bounds = [tuple(b) for b in self.bounds]
        def neg_acq_flat(x_flat):
            a = self.acquisition(x_flat[None,:], method=acq_method, beta=beta, constraint=constraint,
                                 prox_xy=prox_xy, prox_precision=prox_precision,
                                 ehvi_mc_samples=ehvi_mc_samples, hv_mc_samples=hv_mc_samples)
            return -float(a[0])
        result = differential_evolution(neg_acq_flat, bounds, popsize=popsize, maxiter=maxiter, polish=True, seed=self.rng.integers(1e9))
        return result.x, -result.fun

# -------------------------
# Example: toy problem from paper
# -------------------------
def toy_problem(x):
    # x shape (dim,) with dim=2
    x = np.asarray(x)
    f1 = np.linalg.norm(x - np.ones_like(x))
    f2 = np.linalg.norm(x + np.ones_like(x))
    return np.array([f1, f2])

def run_toy_example():
    # 2D input, 2 objectives
    bounds = [(-2.0, 2.0), (-2.0, 2.0)]
    mobo = MultiObjectiveBO(bounds=bounds, n_objectives=2, rng=123)
    # initial latin hypercube-like points
    X0 = np.array([[ -1.5, -1.5],
                   [  1.5,  1.5],
                   [ -1.0,  1.0],
                   [  1.0, -1.0],
                   [  0.0,  0.0]])
    Y0 = np.array([toy_problem(x) for x in X0])
    mobo.initialize(X0, Y0)
    n_iter = 25
    trace = []
    for it in range(n_iter):
        x_next, acq_val = mobo.propose_location(acq_method='ehvi', ehvi_mc_samples=64, hv_mc_samples=2000, maxiter=80, popsize=10)
        y_next = toy_problem(x_next)
        mobo.add_observation(x_next, y_next)
        trace.append((x_next, y_next, acq_val))
        print(f"iter {it+1:02d}: x={x_next}, y={y_next}, acq={acq_val:.4e}")
    # plot pareto front projection
    Ys = mobo.Y
    pareto_mask = is_pareto_efficient(Ys)
    plt.scatter(Ys[:,0], Ys[:,1], c='gray', label='observations')
    plt.scatter(Ys[pareto_mask,0], Ys[pareto_mask,1], c='red', label='Pareto approx')
    # analytical front (for toy)
    t = np.linspace(-1,1,200)
    pf = np.vstack([np.linalg.norm(np.stack([t,t],axis=1) - 1, axis=1),
                    np.linalg.norm(np.stack([t,t],axis=1) + 1, axis=1)]).T
    plt.plot(pf[:,0], pf[:,1], c='green', label='analytical Pareto')
    plt.xlabel('f1'); plt.ylabel('f2'); plt.legend(); plt.title('Toy MOBO results')
    plt.show()

# -------------------------
# ZDT1 multi-objective test function
# -------------------------
def zdt1(x):
    x = np.asarray(x)
    n = len(x)
    f1 = x[0]
    g = 1 + 9 * np.sum(x[1:]) / (n - 1)
    f2 = g * (1 - np.sqrt(f1 / g))
    return np.array([f1, f2])

def run_zdt1_example():
    dim = 30   # 常见维度
    bounds = [(0.0, 1.0)] * dim
    mobo = MultiObjectiveBO(bounds=bounds, n_objectives=2, rng=42)

    # 初始化样本 (随机拉丁超立方近似)
    n_init = 10
    X0 = np.random.rand(n_init, dim)
    Y0 = np.array([zdt1(x) for x in X0])
    mobo.initialize(X0, Y0)

    n_iter = 40
    for it in range(n_iter):
        x_next, acq_val = mobo.propose_location(
            acq_method='ehvi',
            ehvi_mc_samples=64,
            hv_mc_samples=2000,
            maxiter=60,
            popsize=10
        )
        y_next = zdt1(x_next)
        mobo.add_observation(x_next, y_next)
        print(f"iter {it+1:02d}: f1={y_next[0]:.4f}, f2={y_next[1]:.4f}, acq={acq_val:.3e}")

    # 绘制结果
    Ys = mobo.Y
    pareto_mask = is_pareto_efficient(Ys)

    import matplotlib.pyplot as plt
    plt.scatter(Ys[:,0], Ys[:,1], c="gray", label="observations")
    plt.scatter(Ys[pareto_mask,0], Ys[pareto_mask,1], c="red", label="Pareto approx")

    # 解析 Pareto 前沿
    f1s = np.linspace(0, 1, 200)
    f2s = 1 - np.sqrt(f1s)
    plt.plot(f1s, f2s, c="green", label="true Pareto")

    plt.xlabel("f1")
    plt.ylabel("f2")
    plt.legend()
    plt.title("MOBO on ZDT1")
    plt.show()

if __name__ == "__main__":
    # run_toy_example()
    run_zdt1_example()
