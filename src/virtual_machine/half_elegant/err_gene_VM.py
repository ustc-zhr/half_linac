import sys
from scipy.stats import truncnorm
from pathlib import Path

import half_linac.setup as st
from half_linac.src.virtual_machine.half_elegant.runtime_state import update_runtime_state


class errorVM():
    """通过更改half_linac.json文件的方式给虚拟机器添加误差"""
    def __init__(self,sigma_default,jsonpath):
        self.sigma_default = sigma_default
        self.jsonpath = Path(jsonpath)
        # print('default',self.sigma_default)

    def gen_static_err(self,sigma=None):
        """直接更改Q铁横向位置偏差"""
        if sigma is None:
            sigma = self.sigma_default

        mu = 0  # 均值
        sigma = sigma*1e-6  # 标准差 m

        def apply_static_error(lte):
            lattice = lte["lattice"]

            for key in lattice:
                if lattice[key]["TYPE"] == "QUAD":
                    datax = truncnorm.rvs(-3, 3, loc=mu, scale=sigma)
                    datay = truncnorm.rvs(-3, 3, loc=mu, scale=sigma)
                    lattice[key]["DX"] = str(datax)
                    lattice[key]["DY"] = str(datay)
            return True

        update_runtime_state(self.jsonpath, apply_static_error)

        print('static error is added:   Q DX/DY-',sigma,' m')

    def gen_jitter_err(self,sigma_ppm=None):
        """直接通过error_element增加Q铁的动态随机抖动"""
        sigma = sigma_ppm*1e-6  # fraction

        def apply_jitter_error(lte):
            lte["control"]["error_element"]["amplitude"] = str(sigma)
            return True

        update_runtime_state(self.jsonpath, apply_jitter_error)

        print('jitter is added:   Q K1-',sigma_ppm,' ppm')

    def err_off(self):

        def disable_errors(lte):
            lattice = lte["lattice"]
            for key in lattice:
                if lattice[key]["TYPE"] == "QUAD":
                    lattice[key]["DX"] = "0"
                    lattice[key]["DY"] = "0"

            lte["control"]["error_element"]["amplitude"] = "0"
            return True

        update_runtime_state(self.jsonpath, disable_errors)

        print('static/jitter error is off')


sigma_default = 0
jsonpath = Path(st.rootpath) / "src/virtual_machine/half_elegant/halflinac.json"

if __name__=='__main__':
    error_ele = errorVM(sigma_default,jsonpath)
    print(sys.argv)
    try:
        if sys.argv[1] == "gene_err":
            sta_err_quad_sigma_DXDY = float(sys.argv[2])# 
            jit_err_quad_sigma_K1 = float(sys.argv[3])#

            error_ele.gen_static_err(sta_err_quad_sigma_DXDY)
            error_ele.gen_jitter_err(jit_err_quad_sigma_K1)
        elif sys.argv[1] == "err_off":
            error_ele.err_off()
    except:
        pass
