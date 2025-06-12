# Author: Biaobin Li
# Date: 2024-01-25
# 2024-08-29 changed by Shancai Zhang: run elegant when json file changed

import sys
import json
import numpy as np
from scipy.stats import truncnorm

import half_linac.setup as st

# default para
sigma_default = 0
jsonpath = st.rootpath+"/virtual_machine/half_elegant/halflinac.json"

class errorVM():
    def __init__(self,sigma_default,jsonpath):
        self.sigma_default = sigma_default
        self.jsonpath = jsonpath
        # print('default',self.sigma_default)

    def gen_static_err(self,sigma=None):
        if sigma is None:
            sigma = self.sigma_default
        # 设定高斯分布的参数
        
        mu = 0  # 均值
        sigma = sigma*1e-6  # 标准差 m

        # 生成高斯分布的随机数
#        datax = np.random.normal(mu, sigma, 1)[0]  # 假设生成10000个数据点
#        datay = np.random.normal(mu, sigma, 1)[0]
        # 3σ截断
        lower_bound = mu - 3 * sigma
        upper_bound = mu + 3 * sigma

        f = open(self.jsonpath, "r")
        lte = json.load(f)
        lattice = lte["lattice"]
        f.close()
        for key in lattice:
            if lattice[key]["TYPE"] == "QUAD":
                datax = np.random.normal(mu, sigma, 1)[0]
                datay = np.random.normal(mu, sigma, 1)[0]
                while not (lower_bound <= datax <= upper_bound):
                    datax = np.random.normal(mu, sigma, 1)[0]
                lattice[key]["DX"] = str(datax)
                while not (lower_bound <= datay <= upper_bound):
                    datay = np.random.normal(mu, sigma, 1)[0]
                lattice[key]["DY"] = str(datay)
        f = open(self.jsonpath, "w")
        f.write(json.dumps(lte, indent=4))
        f.close()

        print('static error is added:   Q DX/DY-',sigma,' m')

    def gen_jitter_err(self,sigma_ppm=None):
        if sigma is None:
            sigma = self.sigma_default
        sigma = sigma_ppm*1e-6  # fraction

        with open(self.jsonpath,"r") as f:
            lte = json.load(f)
        lattice = lte["lattice"]

        # Q:K1 jitter
        for key in lattice:
            if lattice[key]["TYPE"] == "QUAD":
                datax =  truncnorm.rvs(-3, 3, loc=0, scale=1, size=1)
                lattice[key]["K1"] = str(float(lattice[key]["K1"])*(1+sigma*datax))
        with open(self.jsonpath,"w") as f:
            f.write(json.dumps(lte, indent=4))

        print('jitter is added:   Q K1-',sigma_ppm,' ppm')

    def err_off(self):

        f = open(self.jsonpath, "r")
        lte = json.load(f)
        lattice = lte["lattice"]
        f.close()
        for key in lattice:
            if lattice[key]["TYPE"] == "QUAD":
                lattice[key]["DX"] = "0"
                lattice[key]["DY"] = "0"
        f = open(self.jsonpath, "w")
        f.write(json.dumps(lte, indent=4))
        f.close()      

        print('static error is off')




if __name__=='__main__':
    error_ele = errorVM(sigma_default,jsonpath)
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
