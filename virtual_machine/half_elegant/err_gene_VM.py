# Author: Biaobin Li
# Date: 2024-01-25
# 2024-08-29 changed by Shancai Zhang: run elegant when json file changed


import sys
import json
import numpy as np

import half_linac.setup as st


class errorVM():
    def __init__(self,sigma_default,jsonpath):
        self.sigma_default = sigma_default
        self.jsonpath = jsonpath
        # print('default',self.sigma_default)

    def gen_random_Q_err(self,sigma=None):
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



sigma_default = 0
jsonpath = st.rootpath+"/virtual_machine/half_elegant/halflinac.json"
if __name__=='__main__':
    error_ele = errorVM(sigma_default,jsonpath)
    # print(sys.argv[1])
    try:
        if sys.argv[1] == "gene_err":
            # print(sys.argv[2])
            sigma = float(sys.argv[2])# 
            # print(sigma)
            error_ele.gen_random_Q_err(sigma)
        elif sys.argv[1] == "err_off":
            error_ele.err_off()
    except:
        pass
