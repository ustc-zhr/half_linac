from BOoptimizer import BOOptimizer
from EpicsIOC import Obj_EpicsIoc
import sys
import logging
import time
import numpy as np
import para_setup

# configure the optimization log
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('BOopt.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)



# main script to run Bayesian Optimization with EPICS IOC
if __name__ == "__main__":

    start_time = time.time()
    try:  
        # 从para_setup获取knob和obj和setup的参数
        knobs_pvnames, knobs_bounds = para_setup.knob_para()
        obj_pvnames, obj_weights, obj_samples, obj_math, interval = para_setup.obj_para()
        

        # 建立与目标函数的通道
        objhub = Obj_EpicsIoc(knobs_pvnames=knobs_pvnames,
                            obj_pvnames=obj_pvnames, obj_weights=obj_weights, obj_samples=obj_samples, obj_math=obj_math, 
                            interval=interval)
        
        # 得到绝对边界且转化为符合优化器要求的边界格式(字典)
        ini_values = objhub.init_knob_value()
        # ini_values = [1,2]  # test values
        vrange = np.array([[ini_values[i] + knobs_bounds[i][0], ini_values[i] + knobs_bounds[i][1]] for i in range(len(ini_values))])
        bounds =  vrange
        print("Optimization bounds:", bounds)
        
        # 创建优化对象
        kernel_type, acq, acq_para, acq_optimizer, maxIt, init_points = para_setup.bo_para()
        bo = BOOptimizer(
            func=objhub.evaluate_func,
            bounds=bounds,
            kernel_type=kernel_type,
            acq=acq,
            acq_para=acq_para,
            acq_optimizer=acq_optimizer,
            n_init=init_points,
            n_iter=maxIt
        )

        # 运行优化
        bo.optimize()
        print(f'time: {time.time() - start_time:.2f} s')

        # 绘制收敛曲线
        bo.plot_convergence()

    
    except Exception as e:
        logger.error(f"错误: {e}", exc_info=True)