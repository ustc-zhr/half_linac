from epics import caget, PV, caput_many, caget_many
import time
import numpy as np

import para_setup # 用于获取变量的pv名和变化范围


class Obj_EpicsIoc:
    def __init__(self, knobs_pvnames=None,
                 obj_pvnames=None, obj_weights=None, obj_samples=None, obj_math=None, 
                 interval=None):
        # EPICS相关
        self.knobs_pvnames =  knobs_pvnames

        self.obj_pvnames = obj_pvnames
        self.obj_weights = obj_weights
        self.obj_samples = obj_samples
        self.obj_math = obj_math

        self.interval = interval

        self.data = []# 用于记录所有评估目标函数的数据



    def _record_evaluate(self, x, obj_val):
        """记录优化过程中的数据"""
        self.data.append(np.concatenate((x, [obj_val])))

        # 将数据保存到文件
        np.savetxt('../template.opt',
                  np.array(self.data),
                  fmt='%.6f')

    def init_knob_value(self):
        
        knobs_pvvalue = caget_many(self.knobs_pvnames)
        if None in knobs_pvvalue:
                raise RuntimeError("knob初始值pv读取失败")
        
        return knobs_pvvalue

    
    def evaluate_func(self, **dictparams):
        """评估目标函数"""
        # dict -> list/array
        x = [dictparams[k] for k in sorted(dictparams.keys())] 
        
        # pv输入参数
        caput_many(self.knobs_pvnames, x)
        time.sleep(self.interval)
        
        # 多次采样获取目标函数值
        total = np.zeros((self.obj_samples, len(self.obj_pvnames)))
        for i in range(self.obj_samples):
            total[i, :] = caget_many(self.obj_pvnames)
            time.sleep(self.interval)
        
        # 对多次采样的目标进行数学处理：平均或标准差
        results = []
        for col, op in zip(total.T, self.obj_math):
            if op == 'mean':
                results.append(np.mean(col))
            elif op == 'std':
                results.append(np.std(col))
        # 加上权重
        print('zzz')
        print(results)
        print(self.obj_weights)
        obj_val = np.dot(results, self.obj_weights)

        # 记录
        self._record_evaluate(x, obj_val)
        
        return obj_val
    

if __name__ == "__main__":

    # 获取knob和obj的参数
    knobs_pvnames, knobs_bounds = para_setup.knob_para()
    obj_pvnames, obj_weights, obj_samples, obj_math, interval = para_setup.obj_para()

    # 建立与目标函数的通道
    objhub = Obj_EpicsIoc(knobs_pvnames=knobs_pvnames,
                          obj_pvnames=obj_pvnames, obj_weights=obj_weights, obj_samples=obj_samples, obj_math=obj_math, 
                          interval=interval)
    
    # 计算绝对边界且转化为符合优化器要求的边界格式(字典)
    ini_values = objhub.init_knob_value()
    # ini_values = [1,2,3,4,5]  # test values
    vrange = np.array([[ini_values[i] + knobs_bounds[i][0], ini_values[i] + knobs_bounds[i][1]] for i in range(len(ini_values))])
    # bounds =  {f"x{i+1}": tuple(row) for i, row in enumerate(vrange)}


    
    