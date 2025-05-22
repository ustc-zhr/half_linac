# Author: Shancai Zhang
# Date: 2024-07-25

import time
import numpy as np
from epics import caget, caget_many,caput,caput_many
import half_linac.setup as st


N_BPM = 41 #41
N_COR = 41 #41
d_value = 0.01*0.001
timer_interval = st.runtime_machine

#cor_x_list = ['XC03', 'XC04', 'XC05', 'XC06', 'XC07', 'XC08', 'XC09', 'XC10', 'XC11', 'XC12', 'XC13', 'XC14', 'XC15', 'XC16', 'XC17', 'XC18', 'XC19', 'XC20', 'XC21', 'XC22', 'XC23', 'XC24', 'XC25', 'XC26', 'XC27', 'XC28', 'XC29', 'XC30', 'XC31', 'XC32', 'XC33', 'XC34', 'XC35', 'XC36', 'XC37', 'XC38', 'XC39', 'XC40', 'XC41', 'XC42', 'XC43']
#cor_y_list = ['YC03', 'YC04', 'YC05', 'YC06', 'YC07', 'YC08', 'YC09', 'YC10', 'YC11', 'YC12', 'YC13', 'YC14', 'YC15', 'YC16', 'YC17', 'YC18', 'YC19', 'YC20', 'YC21', 'YC22', 'YC23', 'YC24', 'YC25', 'YC26', 'YC27', 'YC28', 'YC29', 'YC30', 'YC31', 'YC32', 'YC33', 'YC34', 'YC35', 'YC36', 'YC37', 'YC38', 'YC39', 'YC40', 'YC41', 'YC42', 'YC43']
#bpm_y_list = ['BPM03', 'BPM04', 'BPM05', 'BPM06', 'BPM07', 'BPM08', 'BPM09', 'BPM10', 'BPM11', 'BPM12', 'BPM13', 'BPM14', 'BPM15', 'BPM16', 'BPM17', 'BPM18', 'BPM19', 'BPM20', 'BPM21', 'BPM22', 'BPM23', 'BPM24', 'BPM25', 'BPM26', 'BPM27', 'BPM28', 'BPM29', 'BPM30', 'BPM31', 'BPM32', 'BPM33', 'BPM34', 'BPM35', 'BPM36', 'BPM37', 'BPM38', 'BPM39', 'BPM40', 'BPM41', 'BPM42', 'BPM43']
class response:
    
    def __init__(self):
        self.pvl = []
        self.pv_val = []
        self.pvBPMx = []
        self.pvBPMy = []
        self.pvCORx = []
        self.pvCORy = []
        self.mij = []
        self.timer_interval = timer_interval

    def init_BPM_pv(self):
        pvBPMx = []
        pvBPMy = []
        for j in range(N_BPM):
            if j + 3 < 10:
                pv_BPMx = "HALF:IN:BPM:BPM0" + str(j + 3) + ":X:ao"
                pv_BPMy = "HALF:IN:BPM:BPM0" + str(j + 3) + ":Y:ao"
            else:
                pv_BPMx = "HALF:IN:BPM:BPM" + str(j + 3) + ":X:ao"
                pv_BPMy = "HALF:IN:BPM:BPM" + str(j + 3) + ":Y:ao"
            pvBPMx.append(pv_BPMx)
            pvBPMy.append(pv_BPMy)
        self.pvBPMx = pvBPMx
        self.pvBPMy = pvBPMy

    def init_COR_pv(self):
        pvCORx = []
        pvCORy = []
        for j in range(N_COR):
            if j + 3 < 10:
                pv_CORx = "HALF:IN:COR:XC0" + str(j + 3) + ":ao"
                pv_CORy = "HALF:IN:COR:YC0" + str(j + 3) + ":ao"
            else:
                pv_CORx = "HALF:IN:COR:XC" + str(j + 3) + ":ao"
                pv_CORy = "HALF:IN:COR:YC" + str(j + 3) + ":ao"
            pvCORx.append(pv_CORx)
            pvCORy.append(pv_CORy)
        self.pvCORx = pvCORx
        self.pvCORy = pvCORy

    def findresponse(self):
        n_averages = 2
        mij = np.empty((0,2*N_BPM))
        #先依次设校正铁值+dvalue，读bpm数n_averages次并平均
        #设校正铁值-dvalue，读bpm数n_averages次并平均，两者差值为响应矩阵元素
        for i in self.pvCORx:
            va = caget(i)
            caput(i, va + d_value)
            time.sleep(self.timer_interval)
            pvBPMx_val = caget_many(self.pvBPMx)
            pvBPMy_val = caget_many(self.pvBPMy)
            BPMx = pvBPMx_val
            BPMy = pvBPMy_val
            #print('BPMx1   ',BPMx)
            for j in range(n_averages-1):
                time.sleep(self.timer_interval)
                pvBPMx_val = caget_many(self.pvBPMx)
                pvBPMy_val = caget_many(self.pvBPMy)
                BPMx = np.vstack((BPMx,pvBPMx_val))
                BPMy = np.vstack((BPMy,pvBPMy_val))
            #print('BPMx   ',BPMx)
            mean_BPMx1 = np.mean(BPMx, axis=0)
            mean_BPMy1 = np.mean(BPMy, axis=0)
            #print(mean_BPMx1)

            caput(i, va - d_value)
            time.sleep(self.timer_interval)
            pvBPMx_val = caget_many(self.pvBPMx)
            pvBPMy_val = caget_many(self.pvBPMy)
            BPMx = pvBPMx_val
            BPMy = pvBPMy_val
            for j in range(n_averages-1):
                time.sleep(self.timer_interval)
                pvBPMx_val = caget_many(self.pvBPMx)
                pvBPMy_val = caget_many(self.pvBPMy)
                BPMx = np.vstack((BPMx,pvBPMx_val))
                BPMy = np.vstack((BPMy,pvBPMy_val))
            mean_BPMx2 = np.mean(BPMx, axis=0)
            mean_BPMy2 = np.mean(BPMy, axis=0)

            caput(i, va)
            time.sleep(self.timer_interval)
            mi11 = mean_BPMx1 - mean_BPMx2
            mi12 = mean_BPMy1 - mean_BPMy2
            #print("mi11  ",mi11)
            #print('mi12,  ',mi12)
            mij11 = np.append(mi11, mi12)
            #print('mij11   ',mij11)
            # if n == 0 :
            #     mij = mij11
            #     n = n+1
            # else:
            #     mij = np.vstack((mij, mij11))
            mij = np.vstack((mij, mij11))
            #print('mij    ',mij)
            #np.savetxt('mi11.txt', mij11)
            #np.savetxt('mij11.txt', mij11)
            #print('\n',n)
        for i in self.pvCORy:
            va = caget(i)
            caput(i, va + d_value)
            time.sleep(self.timer_interval)
            pvBPMx_val = caget_many(self.pvBPMx)
            pvBPMy_val = caget_many(self.pvBPMy)
            BPMx = pvBPMx_val
            BPMy = pvBPMy_val
            for j in range(n_averages-1):
                time.sleep(self.timer_interval)
                pvBPMx_val = caget_many(self.pvBPMx)
                pvBPMy_val = caget_many(self.pvBPMy)
                BPMx = np.vstack((BPMx,pvBPMx_val))
                BPMy = np.vstack((BPMy,pvBPMy_val))
            #print(BPMx)
            mean_BPMx1 = np.mean(BPMx, axis=0)
            mean_BPMy1 = np.mean(BPMy, axis=0)
            #print(mean_BPMx1)

            caput(i, va - d_value)
            time.sleep(self.timer_interval)
            pvBPMx_val = caget_many(self.pvBPMx)
            pvBPMy_val = caget_many(self.pvBPMy)
            BPMx = pvBPMx_val
            BPMy = pvBPMy_val
            for j in range(n_averages-1):
                time.sleep(self.timer_interval)
                pvBPMx_val = caget_many(self.pvBPMx)
                pvBPMy_val = caget_many(self.pvBPMy)
                BPMx = np.vstack((BPMx, pvBPMx_val))
                BPMy = np.vstack((BPMy, pvBPMy_val))
            mean_BPMx2 = np.mean(BPMx, axis=0)
            mean_BPMy2 = np.mean(BPMy, axis=0)
            
            caput(i, va)
            time.sleep(self.timer_interval)
            mi21 = mean_BPMx1 - mean_BPMx2
            mi22 = mean_BPMy1 - mean_BPMy2
            mij21 = np.append(mi21, mi22)
            mij = np.vstack((mij, mij21))
            #mij并不是真正的响应矩阵，实际上Mij=transpose（mij）/2d_value，所以使用时要先转置再求逆。
        mij = np.transpose(mij)/2/d_value
            #print(mij)
        np.savetxt('response.txt', mij)
            #with open('response.txt',"w") as f:
            #    f.write( json.dumps(lte, indent=4))

if __name__=='__main__':  
    f_res = response()
    f_res.init_BPM_pv()
    f_res.init_COR_pv()
    f_res.findresponse()
 