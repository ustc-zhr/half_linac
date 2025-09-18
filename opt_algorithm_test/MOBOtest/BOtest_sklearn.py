# Python code to run a Gaussian Process-based Bayesian Optimization on the 2D Rosenbrock function.
# This will:
# 1) Define the Rosenbrock function (we treat it as a minimization problem).
# 2) Use a GaussianProcessRegressor as surrogate.
# 3) Use Expected Improvement (EI) acquisition to choose next points.
# 4) Run the BO loop and show:
#    - Contour plot of Rosenbrock with evaluated points.
#    - Convergence plot (best objective vs iteration).
#    - A small table of evaluated points and function values.
#
# Notes per environment tool rules:
# - Uses matplotlib (no seaborn).
# - Each chart is a separate figure.
# - Does not force specific colors.
# - Outputs are visible to the user.
import numpy as np
import pandas as pd
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import RBF, WhiteKernel, ConstantKernel as C
from scipy.optimize import minimize
import matplotlib.pyplot as plt
from math import sqrt, log, exp

np.random.seed(42)

# Rosenbrock function (2D)
def rosenbrock(x):
    # x is array-like length 2
    a = 1.0
    b = 100.0
    x0 = x[0]
    x1 = x[1]
    return (a - x0)**2 + b*(x1 - x0**2)**2

# We will minimize f(x). For GP-based BO which usually maximizes the acquisition,
# we model the negative function y = -f(x) so maximizing y corresponds to minimizing f.
def objective_to_model(x):
    return -rosenbrock(x)

# Expected Improvement (EI) acquisition (we maximize EI)
def expected_improvement(X_candidates, gp, y_best, xi=0.01):
    # X_candidates: (n_candidates, d)
    mu, sigma = gp.predict(X_candidates, return_std=True)
    sigma = sigma.reshape(-1, 1)
    mu = mu.reshape(-1, 1)
    # For numerical stability
    with np.errstate(divide='warn'):
        Z = (mu - y_best - xi) / (sigma + 1e-12)
        from scipy.stats import norm
        ei = (mu - y_best - xi) * norm.cdf(Z) + sigma * norm.pdf(Z)
        ei[sigma.flatten() == 0.0] = 0.0
    return ei.flatten()

# Helper: maximize acquisition using multi-start L-BFGS on bounds
def propose_location(acquisition, gp, bounds, y_best, n_restarts=20):
    dim = bounds.shape[0]
    best_x = None
    best_acq = -np.inf

    # starting points: random and grid
    for x0 in np.random.uniform(bounds[:,0], bounds[:,1], size=(n_restarts, dim)):
        res = minimize(lambda x: -acquisition(x.reshape(1, -1), gp, y_best),
                       x0=x0,
                       bounds=bounds,
                       method='L-BFGS-B')
        if not res.success:
            continue
        acq_val = acquisition(res.x.reshape(1,-1), gp, y_best)[0]
        if acq_val > best_acq:
            best_acq = acq_val
            best_x = res.x.copy()

    # fallback: sample grid and take best
    if best_x is None:
        grid = np.random.uniform(bounds[:,0], bounds[:,1], size=(1000, dim))
        vals = acquisition(grid, gp, y_best)
        idx = np.argmax(vals)
        best_x = grid[idx]
    return np.clip(best_x, bounds[:,0], bounds[:,1])

# Bounds for search (we'll use [-2, 2] for each dimension)
bounds = np.array([[-2.0, 2.0], [-2.0, 2.0]])

# Initial random samples
n_init = 6
X_init = np.random.uniform(bounds[:,0], bounds[:,1], size=(n_init, 2))
y_init = np.array([objective_to_model(x) for x in X_init]).reshape(-1, 1)

# Gaussian process setup
kernel = C(1.0, (1e-3, 1e3)) * RBF(length_scale=np.ones(2), length_scale_bounds=(1e-2, 1e2)) + WhiteKernel(noise_level=1e-6, noise_level_bounds=(1e-10,1e1))
gp = GaussianProcessRegressor(kernel=kernel, n_restarts_optimizer=5, normalize_y=True, random_state=42)

# BO loop
max_iter = 20
X_obs = X_init.copy()
y_obs = y_init.copy()
history_best = []

for it in range(max_iter):
    gp.fit(X_obs, y_obs.ravel())
    # current best (we model negative of objective so larger is better)
    y_best = y_obs.max()
    history_best.append(-y_best)  # store the real (positive) Rosenbrock value at best
    # propose next location
    x_next = propose_location(expected_improvement, gp, bounds, y_best, n_restarts=30)
    y_next = objective_to_model(x_next)
    # append
    X_obs = np.vstack([X_obs, x_next.reshape(1,-1)])
    y_obs = np.vstack([y_obs, np.array([[y_next]])])

# Final GP fit
gp.fit(X_obs, y_obs.ravel())
best_idx = np.argmax(y_obs)
best_x = X_obs[best_idx]
best_f = -y_obs[best_idx,0]

# Prepare table of evaluations
df = pd.DataFrame({
    'x1': X_obs[:,0],
    'x2': X_obs[:,1],
    'rosenbrock': (-y_obs).flatten()
})
# show sorted by rosenbrock (ascending)
df_sorted = df.sort_values('rosenbrock').reset_index(drop=True)

# Plot 1: contour of Rosenbrock and evaluated points
grid_points = 200
x1 = np.linspace(bounds[0,0], bounds[0,1], grid_points)
x2 = np.linspace(bounds[1,0], bounds[1,1], grid_points)
X1, X2 = np.meshgrid(x1, x2)
Z = np.vectorize(lambda a,b: rosenbrock([a,b]))(X1, X2)

plt.figure(figsize=(7,6))
cs = plt.contour(X1, X2, Z, levels=30)
plt.clabel(cs, inline=1, fontsize=8)
plt.scatter(X_obs[:,0], X_obs[:,1])
plt.title('Rosenbrock function contour with BO evaluation points')
plt.xlabel('x1')
plt.ylabel('x2')
plt.grid(True)
plt.show()

# Plot 2: convergence (best found rosenbrock value vs iteration)
plt.figure(figsize=(7,5))
plt.plot(np.arange(1, len(history_best)+1), history_best, marker='o')
plt.title('Convergence: best found Rosenbrock value per iteration')
plt.xlabel('Iteration')
plt.ylabel('Best Rosenbrock value (lower is better)')
plt.grid(True)
plt.show()

# Display top-10 evaluations
# import caas_jupyter_tools as cjt
# cjt.display_dataframe_to_user("BO evaluations (top 10)", df_sorted.head(10))

# Print summary
print("Best found x:", best_x)
print("Best found Rosenbrock value:", best_f)
print("\nTop 5 evaluations (lowest Rosenbrock):")
print(df_sorted.head(5).to_string(index=False))
