
# minimal_sklearn_ucb.py
# 2D version
import numpy as np
import matplotlib.pyplot as plt
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import RBF, ConstantKernel as C

np.random.seed(0)

def objective(x):
    # return np.linalg.norm(x - np.array([1.0,1.0]), axis=1).reshape(-1,1)
    return np.sum(100.0 * (x[:,1:] - x[:,:-1]**2.0)**2.0 + (1 - x[:,:-1])**2.0,
                  axis=1).reshape(-1, 1)

def ucb_acquisition(gpr, Xcand, beta=2.0, minimize=True):
    mu, std = gpr.predict(Xcand, return_std=True)
    if minimize:
        acq = mu.ravel() - beta * std.ravel()
    else:
        acq = mu.ravel() + beta * std.ravel()
    return acq



def main():
    bounds = np.array([[-2,2],[-2,2]])
    n_init = 5
    X = np.random.uniform(bounds[:,0], bounds[:,1], size=(n_init,2))
    Y = objective(X)

    kernel = C(0.5) * RBF(length_scale=1.0)
    gpr = GaussianProcessRegressor(kernel=kernel, alpha=1e-6, normalize_y=True)
    gpr.fit(X, Y.ravel())

    # grid
    nx = 80
    x1 = np.linspace(bounds[0,0], bounds[0,1], nx)
    x2 = np.linspace(bounds[1,0], bounds[1,1], nx)
    X1, X2 = np.meshgrid(x1, x2)
    Xcand = np.vstack([X1.ravel(), X2.ravel()]).T

    history_X = X.copy()
    history_Y = Y.copy()

    for it in range(50):
        acq = ucb_acquisition(gpr, Xcand, beta=0.1, minimize=True)
        idx = np.argmin(acq)
        x_next = Xcand[idx:idx+1, :]
        y_next = objective(x_next)
        print(f"Iter {it+1}: x_next = {x_next.ravel()}, y_next = {y_next.ravel()}")

        history_X = np.vstack([history_X, x_next])
        history_Y = np.vstack([history_Y, y_next])
        gpr.fit(history_X, history_Y.ravel())

    # plotting (optional)
    Mu, Std = gpr.predict(Xcand, return_std=True)
    Mu = Mu.reshape(nx, nx)
    Std = Std.reshape(nx, nx)

    fig, axs = plt.subplots(1,3, figsize=(15,4))
    cs0 = axs[0].contourf(X1, X2, Mu, levels=30); axs[0].scatter(history_X[:,0], history_X[:,1], c='w', edgecolors='k'); axs[0].set_title("GP mean")
    cs1 = axs[1].contourf(X1, X2, Std, levels=30); axs[1].scatter(history_X[:,0], history_X[:,1], c='w', edgecolors='k'); axs[1].set_title("GP std")
    Acq_grid = Mu - 2.0 * Std
    cs2 = axs[2].contourf(X1, X2, Acq_grid, levels=30); axs[2].scatter(history_X[:,0], history_X[:,1], c='w', edgecolors='k'); axs[2].set_title("UCB (lower better)")
    # plt.tight_layout(); plt.show()

    # 收敛
    fig2, ax = plt.subplots(figsize=(8, 4))
    ax.plot(np.arange(len(history_Y)), history_Y, 'o-', label='Objective Value')
    ax.axhline(y=0, color='r', linestyle='--', label='True Minimum (0)')
    ax.set_xlabel('Iteration')
    ax.set_ylabel('Objective Value')
    ax.set_title('Convergence Curve')
    ax.legend()
    # plt.tight_layout()
    # plt.show()

    # 采样点分布
    fig3, ax = plt.subplots(figsize=(6, 6))
    h = ax.hist2d(history_X[:, 0], history_X[:, 1], bins=20, cmap='viridis')
    ax.scatter([1.0], [1.0], c='red', s=100, marker='*', label='True Minimum')
    ax.set_xlabel('x1')
    ax.set_ylabel('x2')
    ax.set_title('Sampling Points Distribution')
    plt.colorbar(h[3], ax=ax, label='Density')
    ax.legend()
    plt.tight_layout()
    plt.show()

if __name__ == "__main__":
    main()
