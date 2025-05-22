# Author: Biaobin Li
# Date: 2024-01-25
# 2024-08-29 changed by Shancai Zhang: run elegant when json file changed


import sys
import json
import numpy as np
from scipy.stats import truncnorm

import half_linac.setup as st


class errorVM():
    def __init__(self,sigma_default,jsonpath):
        self.sigma_default = sigma_default
        self.jsonpath = jsonpath
        # print('default',self.sigma_default)

    def gen_static_err(self,sigma=None):
        if sigma is None:
            sigma = self.sigma_default

        mu = 0  # 均值
        sigma = sigma*1e-6  # 标准差 m

        with open(self.jsonpath,"r") as f:
            lte = json.load(f)
        lattice = lte["lattice"]

        for key in lattice:
            if lattice[key]["TYPE"] == "QUAD":
                datax = truncnorm.rvs(-3, 3, loc=mu, scale=sigma)
                datay = truncnorm.rvs(-3, 3, loc=mu, scale=sigma)
                lattice[key]["DX"] = str(datax)
                lattice[key]["DY"] = str(datay)
        with open(self.jsonpath,"w") as f:
            f.write(json.dumps(lte, indent=4))

        print('static error is added:   Q DX/DY-',sigma,' m')

    def gen_jitter_err(self,sigma_ppm=None):
        sigma = sigma_ppm*1e-6  # fraction

        with open(self.jsonpath,"r") as f:
            lte = json.load(f)
        
        # Q:K1 jitter
        lte["control"]["error_element"]["amplitude"] = str(sigma)

        with open(self.jsonpath,"w") as f:
            f.write(json.dumps(lte, indent=4))

        print('jitter is added:   Q K1-',sigma_ppm,' ppm')

    def err_off(self):

        with open(self.jsonpath,"r") as f:
            lte = json.load(f)
        lattice = lte["lattice"]
        for key in lattice:
            if lattice[key]["TYPE"] == "QUAD":
                lattice[key]["DX"] = "0"
                lattice[key]["DY"] = "0"
        lte["control"]["error_element"]["amplitude"] = "0"
        with open(self.jsonpath,"w") as f:
            f.write(json.dumps(lte, indent=4))      

        print('static/jitter error is off')


sigma_default = 0
jsonpath = st.rootpath+"/virtual_machine/half_elegant/halflinac.json"

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
