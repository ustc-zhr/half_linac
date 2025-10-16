# PV lists  & scan range
import numpy as np
# 全部的knob
def knob_para():
    cor_x_all = ['XC01', 'XC02', 'XC03', 'XC04', 'XC05', 'XC06', 'XC07', 'XC08', 'XC09', 'XC10', 'XC11']
    cor_y_all = ['YC01', 'YC02', 'YC03', 'YC04', 'YC05', 'YC06', 'YC07', 'YC08', 'YC09', 'YC10', 'YC11']
    quad_all = ['QT01', 'QT02']

    # 选择的knobs
    indices = []  # 选择第x、x、x个correctors
    cor_x_select = [cor_x_all[i] for i in indices]
    cor_y_select = [cor_y_all[i] for i in indices]

    indices = [0, 1]  # 选择第x、x、x个quads
    quad_select = [quad_all[i] for i in indices]

    # 将选择的knob转换为pv名列表和边界列表
    cor_x_pvlist = []
    cor_x_bounds = []
    for cor in cor_x_select:
        # cor_x_pvlist.append(f"HLS:IN:COR:{cor}:ao")
        cor_x_pvlist.append(f"HALF:IN:COR:{cor}:ao")
        cor_x_bounds.append((-5, 5))  # 每个PV对应一个(-5,5)的边界

    cor_y_pvlist = []
    cor_y_bounds = []
    for cor in cor_y_select:
        # cor_y_pvlist.append(f"HLS:IN:COR:{cor}:ao")
        cor_y_pvlist.append(f"HALF:IN:COR:{cor}:ao")
        cor_y_bounds.append((-5, 5))

    quad_pvlist = []
    quad_bounds = []
    for quad in quad_select:
        # quad_pvlist.append(f"HLS:IN:QUAD:{quad}:K1")
        quad_pvlist.append(f"HALF:IN:QUAD:{quad}:K1")
        quad_bounds.append((-2, 2))

    # 合并所有PV列表和边界列表
    knob_pvlist = cor_x_pvlist + cor_y_pvlist + quad_pvlist
    knob_bounds = cor_x_bounds + cor_y_bounds + quad_bounds

    return knob_pvlist, knob_bounds

def obj_para():
    obj_pvnames = ['HALF:IN:FLAG:PRF07:sigx', 'HALF:IN:FLAG:PRF07:sigy']
    obj_weights = [-1.0, -1.0]
    obj_samples = 1
    obj_math = ['mean', 'mean'] # 'mean' or 'std'
    interval = 8

    return obj_pvnames, obj_weights, obj_samples, obj_math, interval    

def bo_para():
    acq = "ucb"
    kernel_type = "rbf" # "rbf", "matern", "matern5/2", "matern3/2", "white"
    acq_optimizer = "sobol" #
    kernel_para = 2.576

    init_points = 5
    maxIt = 10

    return  kernel_type, acq, acq_para, acq_optimizer, maxIt, init_points

if __name__ == "__main__":
    knob_pvlist, knob_bounds = knob_para()
    print("PV列表:")
    print(knob_pvlist)
    print("\n相对边界:")
    print((knob_bounds))
    print("\n设置参数:")
    print(bo_para()) 
