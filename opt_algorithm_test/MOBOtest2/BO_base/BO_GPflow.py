# minimal_gpflow_ucb.py
# Minimal reproducible single-objective optimization: GPflow + UCB + grid search
# Author: assistant (example)
# Usage: python minimal_gpflow_ucb.py

import os
# Force single-threaded BLAS/OMP to reduce rare double-free / threading issues
# os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"
# os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
# os.environ["OMP_NUM_THREADS"] = "1"
# os.environ["OPENBLAS_NUM_THREADS"] = "1"
# os.environ["MKL_NUM_THREADS"] = "1"
# os.environ["NUMEXPR_NUM_THREADS"] = "1"
# os.environ["TF_ENABLE_ONEDNN_OPTS"] = "0"

import numpy as np
import matplotlib.pyplot as plt
import gpflow
import tensorflow as tf

np.random.seed(0)
tf.random.set_seed(0)

def objective(x):
    """True objective to minimize. x: (n,2) array -> (n,1) outputs"""
    # simple bowl centered at (1,1)
    return np.linalg.norm(x - np.array([1.0, 1.0]), axis=1).reshape(-1,1)

def ucb_acquisition(gpr, Xcand, beta=2.0, minimize=True):
    """
    Compute UCB acquisition values on Xcand.
    For minimization, use a_lower = mean - beta*std (lower is better).
    Return acquisition array of shape (n_cand,).
    """
    # gpflow.model.GPR.predict_f expects 2D float64
    mu, var = gpr.predict_f(Xcand)          # TensorFlow tensors
    mu = mu.numpy().ravel()
    std = np.sqrt(var.numpy().ravel() + 1e-12)
    if minimize:
        acq = mu - beta * std   # lower is better
    else:
        acq = mu + beta * std   # higher is better
    return acq

def grid_argmin(acq_vals, Xcand):
    idx = np.argmin(acq_vals)
    return Xcand[idx:idx+1, :].copy()  # return 2D array (1,d)

def main():
    # domain: 2D box
    bounds = np.array([[-2.0, 2.0], [-2.0, 2.0]])
    n_init = 5
    dim = 2

    # initial random samples
    X = np.random.uniform(bounds[:,0], bounds[:,1], size=(n_init, dim))
    Y = objective(X)

    # gpflow kernel and GPR model
    kernel = gpflow.kernels.SquaredExponential(lengthscales=1.0, variance=0.5)
    gpr = gpflow.models.GPR(data=(X.astype(np.float64), Y.astype(np.float64)),
                            kernel=kernel,
                            noise_variance=1e-4)

    # Optionally optimize hyperparameters (short optimization)
    opt = gpflow.optimizers.Scipy()
    try:
        opt.minimize(gpr.training_loss, gpr.trainable_variables, options=dict(maxiter=100))
    except Exception as e:
        print("Warning: hyperparameter optimization failed or raised:", e)

    # candidate grid for acquisition optimization (coarse grid)
    nx = 80
    x1 = np.linspace(bounds[0,0], bounds[0,1], nx)
    x2 = np.linspace(bounds[1,0], bounds[1,1], nx)
    X1, X2 = np.meshgrid(x1, x2)
    Xcand = np.vstack([X1.ravel(), X2.ravel()]).T.astype(np.float64)

    n_iter = 15
    beta = 2.0
    minimize = True

    history_X = X.copy()
    history_Y = Y.copy()

    for it in range(n_iter):
        # compute acquisition on grid
        acq = ucb_acquisition(gpr, Xcand, beta=beta, minimize=minimize)

        # pick argmin on the grid
        x_next = grid_argmin(acq, Xcand)   # shape (1,2)
        y_next = objective(x_next)

        print(f"Iter {it+1}: x_next = {x_next.ravel()}, y_next = {y_next.ravel()}")

        # append data
        history_X = np.vstack([history_X, x_next])
        history_Y = np.vstack([history_Y, y_next])

        # update gpr.data (set new dataset)
        gpr.data = (history_X.astype(np.float64), history_Y.astype(np.float64))

        # (optional) re-optimize hyperparameters a bit
        try:
            opt.minimize(gpr.training_loss, gpr.trainable_variables, options=dict(maxiter=30))
        except Exception:
            pass

    # final predictions on grid for plotting
    Mu, Var = gpr.predict_f(Xcand.astype(np.float64))
    Mu = Mu.numpy().reshape(nx, nx)
    Std = np.sqrt(Var.numpy().reshape(nx, nx))

    # plotting
    fig, axs = plt.subplots(1,3, figsize=(15,4))
    cs0 = axs[0].contourf(X1, X2, Mu, levels=30)
    axs[0].scatter(history_X[:,0], history_X[:,1], c='w', edgecolors='k')
    axs[0].set_title("GP mean")
    fig.colorbar(cs0, ax=axs[0])

    cs1 = axs[1].contourf(X1, X2, Std, levels=30)
    axs[1].scatter(history_X[:,0], history_X[:,1], c='w', edgecolors='k')
    axs[1].set_title("GP std")
    fig.colorbar(cs1, ax=axs[1])

    # acquisition (for minimization use mu - beta*std)
    Acq_grid = (Mu - beta * Std) if minimize else (Mu + beta * Std)
    cs2 = axs[2].contourf(X1, X2, Acq_grid, levels=30)
    axs[2].scatter(history_X[:,0], history_X[:,1], c='w', edgecolors='k')
    axs[2].set_title("UCB acquisition (lower better)")
    fig.colorbar(cs2, ax=axs[2])

    plt.tight_layout()
    plt.show()

if __name__ == "__main__":
    main()
