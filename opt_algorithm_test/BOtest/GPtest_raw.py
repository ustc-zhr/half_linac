import matplotlib.pyplot as plt
import numpy as np


def kernel_func(xs, ys, sigma=1, l=1):
    """Sqared Exponential kernel as above but designed to return the whole
    covariance matrix - i.e. the pairwise covariance of the vectors xs & ys.
    Also with two parameters which are discussed at the end.
    """
    # Pairwise difference matrix.
    dx = np.expand_dims(xs, 1) - np.expand_dims(ys, 0)
    return (sigma ** 2) * np.exp(-((dx / l) ** 2) / 2)

def mean_func(x):
    """The mean function. As discussed, we can let the mean always be zero."""
    return np.zeros_like(x)

def f(x):
    coefs = [6, -2.5, -2.4, -0.1, 0.2, 0.03]
    total = 0
    for exp, coef in enumerate(coefs):
        total += coef * (x ** exp)
    return total

if __name__ == '__main__':
    x_obs = np.array([-4, -1.5, 0, 1.5, 2.5, 2.7])

    y_obs = f(x_obs)

    x_s = np.linspace(-8, 7, 80)

    K = kernel_func(x_obs, x_obs)
    K_s = kernel_func(x_obs, x_s)
    K_ss = kernel_func(x_s, x_s)


    K_sTKinv = np.matmul(K_s.T, np.linalg.pinv(K))

    mu_s = mean_func(x_s) + np.matmul(K_sTKinv, y_obs - mean_func(x_obs))
    Sigma_s = K_ss - np.matmul(K_sTKinv, K_s)

    plt.figure(figsize=(8, 5))
    plt.title("Gaussian Process Regression")
    plt.ylim(-7, 8)


    # 1. 绘制真实函数（黑色虚线）
    y_true = f(x_s)
    plt.plot(x_s, y_true, 'k--', linewidth=3, alpha=0.4, label='True f(x)')

    # 2. 绘制观测数据点（十字标记）
    plt.scatter(x_obs, y_obs, s=100, marker='x', c='red', label='Training data')

    # 3. 绘制不确定性区域（灰色半透明区域）
    stds = np.sqrt(Sigma_s.diagonal())
    plt.fill_between(x_s, mu_s - 3*stds, mu_s + 3*stds, 
                    color='gray', alpha=0.2, label='Uncertainty: ±3' + r"$\sigma$")

    for _ in range(3):
        y_s = np.random.multivariate_normal(mu_s, Sigma_s)
        plt.plot(x_s, y_s, linewidth=1)

    plt.plot(x_s, mu_s, 'b-', linewidth=3, alpha=0.4, label='Mean')


    plt.legend(loc='best')
    plt.xlabel('x')
    plt.ylabel('f(x)')
    # plt.grid(True, linestyle='--', alpha=0.7)

    plt.tight_layout()
    plt.show()