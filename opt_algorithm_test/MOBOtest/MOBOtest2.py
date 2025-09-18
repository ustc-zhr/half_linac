import numpy as np
import time
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import RBF, ConstantKernel as C
import matplotlib.pyplot as plt

# -------- Pareto & Hypervolume ----------
def is_pareto_efficient(points):
    pts = np.asarray(points)
    n = pts.shape[0]
    is_efficient = np.ones(n, dtype=bool)
    for i in range(n):
        if is_efficient[i]:
            dominated = np.all(pts <= pts[i], axis=1) & np.any(pts < pts[i], axis=1)
            is_efficient[dominated] = False
    return is_efficient

def hypervolume_estimate(points, ref_point, n_samples=200, rng=None):
    rng = np.random.default_rng(rng)
    points = np.asarray(points)
    P = points.shape[1]
    lower = np.min(points, axis=0)
    upper = np.asarray(ref_point)
    samples = rng.random((n_samples, P)) * (upper - lower) + lower
    dominated = np.zeros(n_samples, dtype=bool)
    for p in points:
        dominated |= np.all(p <= samples, axis=1)
    frac = dominated.mean()
    return frac * np.prod(upper - lower)

# -------- Fast MOBO ----------
class FastMOBO:
    def __init__(self, bounds, n_obj, rng=None):
        self.bounds = np.asarray(bounds)
        self.dim = len(bounds)
        self.n_obj = n_obj
        self.rng = np.random.default_rng(rng)
        kernel = C(1.0) * RBF(length_scale=np.ones(self.dim))
        # 设置 n_restarts_optimizer=0
        self.gps = [
            GaussianProcessRegressor(kernel=kernel, alpha=1e-6, normalize_y=True, n_restarts_optimizer=0) 
            for _ in range(n_obj)
        ]
        self.X, self.Y = None, None

    def initialize(self, X_init, Y_init):
        self.X = np.asarray(X_init)
        self.Y = np.asarray(Y_init)
        # print(self.X)
        # print(self.Y)
        self._fit()

    def _fit(self):
        for i in range(self.n_obj):
            self.gps[i].fit(self.X, self.Y[:, i])

    def add(self, x, y):
        self.X = np.vstack([self.X, x])
        self.Y = np.vstack([self.Y, y])
        self._fit()

    def predict(self, x):
        # Ensure x is 2D
        x = np.atleast_2d(x)

        mu, sigma = [], []
        for gp in self.gps:
            m, s = gp.predict(x, return_std=True)
            mu.append(m)
            sigma.append(s)
        
        return np.vstack(mu).T, np.vstack(sigma).T

    def acquisition_ucb_hvi(self, x, beta=0.01, hv_samples=200):
        mu, sigma = self.predict(x)
        y_ucb = mu - np.sqrt(beta) * sigma
        
        # 计算当前的超体积增益
        pareto = self.Y[is_pareto_efficient(self.Y)]
        ref = np.max(self.Y, axis=0) + 0.1
        hv0 = hypervolume_estimate(pareto, ref, hv_samples, rng=self.rng)
        hv1 = hypervolume_estimate(np.vstack([pareto, y_ucb[0]]), ref, hv_samples, rng=self.rng)
        return hv1 - hv0

    def propose(self, n_candidates=200, beta=0.01):
        # Generate random candidates within bounds
        cand = self.rng.random((n_candidates, self.dim))
        for i in range(self.dim):
            low, high = self.bounds[i]
            cand[:, i] = cand[:, i] * (high - low) + low
        
        # Evaluate acquisition function on candidates
        scores = [self.acquisition_ucb_hvi(x[None, :], beta=beta) for x in cand]
        idx = np.argmax(scores)

        return cand[idx], scores[idx]

# -------- ZDT1 test ----------
def zdt1(x):
    x = np.asarray(x)
    n = len(x)
    f1 = x[0]
    g = 1 + 9 * np.sum(x[1:]) / (n - 1)
    f2 = g * (1 - np.sqrt(f1 / g))
    return np.array([-f1, -f2])

def run_fast_mobo_zdt1():
    dim = 30
    bounds = [(0, 1)] * dim
    mobo = FastMOBO(bounds, n_obj=2, rng=42)

    # 初始样本
    X0 = np.random.rand(5, dim)
    Y0 = np.array([zdt1(x) for x in X0])
    mobo.initialize(X0, Y0)

    n_iter = 100
    total_time = 0.0

    # 迭代优化
    for it in range(n_iter):
        t0 = time.perf_counter()
        x_next, score = mobo.propose(n_candidates=300, beta=0.2)
        y_next = zdt1(x_next)
        mobo.add(x_next, y_next)
        t1 = time.perf_counter()
        iter_time = t1 - t0
        total_time += iter_time

        print(f"iter {it+1:02d}: f1={y_next[0]:.4f}, f2={y_next[1]:.4f}, "
              f"acq={score:.3e}, time={iter_time:.3f} s")

    avg_time = total_time / n_iter
    print("="*50)
    print(f"Total time over {n_iter} iterations: {total_time:.3f} s")
    print(f"Average time per iteration: {avg_time:.3f} s")
    print("="*50)

    # 可视化
    Y = mobo.Y
    mask = is_pareto_efficient(Y)
    plt.scatter(Y[:,0], Y[:,1], c="gray", label="samples")
    plt.scatter(Y[mask,0], Y[mask,1], c="red", label="Pareto approx")

    # Plot true Pareto front
    f1s = np.linspace(0,1,200)
    f2s = 1 - np.sqrt(f1s)
    plt.plot(-f1s, -f2s, c="green", label="true Pareto")

    plt.xlabel("f1"); plt.ylabel("f2"); plt.legend()
    plt.title("Fast MOBO on ZDT1 (with timing)")
    plt.show()


if __name__ == "__main__":
    run_fast_mobo_zdt1()
