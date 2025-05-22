# Author: Shancai Zhang
# Date: 2024-08-25

import time

import numpy as np
from threading import Timer, activeCount
import sys
from epics import caget, caget_many,caput,caput_many,PV
import os
import half_linac.setup as st


RESPM_FILE = st.rootpath+'/apps/orbit_correct/response.dat'
filepath   = st.rootpath+"/apps/orbit_correct/clicked.txt"
N_BPM = 41 #41
N_COR = 41 #41
d_value = 0.02*0.001
max_value = 0.001
timer_interval = st.runtime_machine
cor_x_list_all = ['XC03', 'XC04', 'XC05', 'XC06', 'XC07', 'XC08', 'XC09', 'XC10', 'XC11', 'XC12', 'XC13', 'XC14', 'XC15', 'XC16', 'XC17', 'XC18', 'XC19', 'XC20', 'XC21', 'XC22', 'XC23', 'XC24', 'XC25', 'XC26', 'XC27', 'XC28', 'XC29', 'XC30', 'XC31', 'XC32', 'XC33', 'XC34', 'XC35', 'XC36', 'XC37', 'XC38', 'XC39', 'XC40', 'XC41', 'XC42', 'XC43']
cor_y_list_all = ['YC03', 'YC04', 'YC05', 'YC06', 'YC07', 'YC08', 'YC09', 'YC10', 'YC11', 'YC12', 'YC13', 'YC14', 'YC15', 'YC16', 'YC17', 'YC18', 'YC19', 'YC20', 'YC21', 'YC22', 'YC23', 'YC24', 'YC25', 'YC26', 'YC27', 'YC28', 'YC29', 'YC30', 'YC31', 'YC32', 'YC33', 'YC34', 'YC35', 'YC36', 'YC37', 'YC38', 'YC39', 'YC40', 'YC41', 'YC42', 'YC43']
bpm_list_all = ['BPM03', 'BPM04', 'BPM05', 'BPM06', 'BPM07', 'BPM08', 'BPM09', 'BPM10', 'BPM11', 'BPM12', 'BPM13', 'BPM14', 'BPM15', 'BPM16', 'BPM17', 'BPM18', 'BPM19', 'BPM20', 'BPM21', 'BPM22', 'BPM23', 'BPM24', 'BPM25', 'BPM26', 'BPM27', 'BPM28', 'BPM29', 'BPM30', 'BPM31', 'BPM32', 'BPM33', 'BPM34', 'BPM35', 'BPM36', 'BPM37', 'BPM38', 'BPM39', 'BPM40', 'BPM41', 'BPM42', 'BPM43']

class correct:
    def __init__(self, timer_interval_set=None, cor_accuracy=None, samples_perstep=None, target_BPMlist=None):
        self.a = 0
        self.pvl = []
        self.pv_val = []
        self.pvBPMx = []
        self.pvBPMy = []
        self.pvCORx = []
        self.pvCORy = []

        self.ORMx_n,self.ORMy_n = self.load_response_matrix()
        
        if timer_interval_set == None or timer_interval_set < timer_interval:
            self.timer_interval = timer_interval
        else:
            self.timer_interval = timer_interval_set
        self.cor_accuracy = cor_accuracy
        self.samples_perstep = samples_perstep

        index = self.find_positions(bpm_list_all, target_BPMlist)
        self.bpm_list_target = [bpm_list_all[i] for i in index]
        self.cor_x_list_target  = [cor_x_list_all[i] for i in index]
        self.cor_y_list_target  = [cor_y_list_all[i] for i in index]
        print(self.bpm_list_target)
        print(self.cor_x_list_target)
        print(self.cor_y_list_target)

    def find_positions(self, main_list, sub_list):
        return [index for index, element in enumerate(main_list) if element in sub_list]
    
    def load_response_matrix(self):  
        RM = np.loadtxt(RESPM_FILE)
        # ORM = np.reshape(RM,(82,82),order='C')
        ORM_x = RM[0:N_BPM,0:N_COR]
        ORM_y = RM[N_BPM:82,N_COR:82]
        ORMx_n = np.linalg.inv(ORM_x)
        ORMy_n = np.linalg.inv(ORM_y)
        return(ORMx_n,ORMy_n)

    def init_BPM_pv(self):  
        for j in self.bpm_list_target:  
            pv_BPMx = PV("HALF:IN:BPM:" + j + ":X:ao")  
            pv_BPMy = PV("HALF:IN:BPM:" + j + ":Y:ao")  
            self.pvBPMx.append(pv_BPMx)  
            self.pvBPMy.append(pv_BPMy) 
  
    def init_COR_pv(self):  
        for j in self.cor_x_list_target:  
            pv_CORx = PV("HALF:IN:COR:" + j + ":ao")  
            self.pvCORx.append(pv_CORx)  
        for j in self.cor_y_list_target:  
            pv_CORy = PV("HALF:IN:COR:" + j + ":ao")  
            self.pvCORy.append(pv_CORy)  

    def reset_cor(self):
        for j in self.cor_x_list_target:  
            pv_CORx = "HALF:IN:COR:" + j + ":ao"  
            self.pvCORx.append(pv_CORx)  
        for j in self.cor_y_list_target:  
            pv_CORy = "HALF:IN:COR:" + j + ":ao" 
            self.pvCORy.append(pv_CORy)
        values = [0] * len(self.pvCORx)
        caput_many(self.pvCORx,values)
        caput_many(self.pvCORy,values)

#    timer_interval = 2
#       用于虚拟机的轨道校正
#响应矩阵方法进行轨道校正
    def Corrector_h(self):
        print(self.a)
        t1 = time.time()
        self.a += 1
        xbpmVal = []
        for idex, btem in enumerate(self.pvBPMx):
            xbpmVal.append(btem.get())
        delt_orbitx = np.array(xbpmVal)

        hcorrVal = []
        for idex, ctem in enumerate(self.pvCORx):
            hcorrVal.append(ctem.get())

        delt_corrh = np.dot(self.ORMx_n,delt_orbitx)
        hcorrVal = hcorrVal-delt_corrh*d_value*2

        for idex, ctem in enumerate(self.pvCORx):
            ctem.put(hcorrVal[idex])

        t2 = time.time()
        print(t2-t1)
        print('当前线程数为{}'.format(activeCount()))
        #time.sleep(1)
        if self.a > 5:
            self.T_h.cancel()   
            print("定时器已取消")
        else:
            time.sleep(2)
            self.T_h = Timer(self.timer_interval, self.Corrector_h)
            self.T_h.start()

    def Corrector_h_p(self):
        #print(self.a)
        #t1 = time.time()
        self.a += 1
        xbpmVal = []
        for idex, btem in enumerate(self.pvBPMx):
            xbpmVal.append(btem.get())
        delt_orbitx = np.array(xbpmVal)

        hcorrVal = []
        for idex, ctem in enumerate(self.pvCORx):
            hcorrVal.append(ctem.get())

        delt_corrh = np.dot(self.ORMx_n,delt_orbitx)*d_value*2
        norm = np.linalg.norm(delt_corrh)
        
        # if norm < 1e-8 :
        #     print("满足精度要求，停止校正",norm)
        #     return

        hcorrVal = hcorrVal-delt_corrh

        for idex, ctem in enumerate(self.pvCORx):
            if hcorrVal[idex] > max_value:
                hcorrVal[idex] = max_value
            ctem.put(hcorrVal[idex])

        time.sleep(2)
        self.T_h = Timer(self.timer_interval, self.Corrector_h_p)
        current_modified = os.path.getmtime(filepath)
        if current_modified != last_modified:
            return
        else:
            self.T_h.start()

    def Corrector_v_p(self):
        #print(self.a)
        #t1 = time.time()
        self.a += 1
        ybpmVal = []
        for idex, btem in enumerate(self.pvBPMy):
            ybpmVal.append(btem.get())
        delt_orbity = np.array(ybpmVal)

        vcorrVal = []
        for idex, ctem in enumerate(self.pvCORy):
            vcorrVal.append(ctem.get())

        delt_corrv = np.dot(self.ORMy_n,delt_orbity)*d_value*2
        normv = np.linalg.norm(delt_corrv)
        
        # if normv < 1e-8 :
        #     print("满足精度要求，停止校正",normv)
        #     return
        
        vcorrVal = vcorrVal-delt_corrv

        for idex, ctem in enumerate(self.pvCORy):
            if vcorrVal[idex] > max_value:
                vcorrVal[idex] = max_value
            ctem.put(vcorrVal[idex])

        time.sleep(2)
        self.T_v = Timer(self.timer_interval, self.Corrector_v_p)
        current_modified = os.path.getmtime(filepath)
        if current_modified != last_modified:
            return
        else:
            self.T_v.start()
        # last_modified = os.path.getmtime(filepath)
        # while True:
        #     current_modified = os.path.getmtime(filepath)
        #     #time.sleep(2)
        #     self.T_h = Timer(self.timer_interval, self.Corrector_h_p)
        #     self.T_h.start()
        #     if current_modified != last_modified:
        #        return
        #t2 = time.time()
        #print(t2-t1)
        #print('当前线程数为{}'.format(activeCount()))
        #time.sleep(1)
        # if False:  #self.a > 5:
        #     self.T_h.cancel()
        #     print("定时器已取消")
        # else:
        #     time.sleep(2)
        #     self.T_h = Timer(self.timer_interval, self.Corrector_h_p)
        #     self.T_h.start()

    def Corrector_p(self):
        #print(self.a)
        #t1 = time.time()
        self.a += 1
        xbpmVal = []

        for idex, btem in enumerate(self.pvBPMx):
            xbpmVal.append(btem.get())
        delt_orbitx = np.array(xbpmVal)

        hcorrVal = []
        for idex, ctem in enumerate(self.pvCORx):
            hcorrVal.append(ctem.get())

        delt_corrh = np.dot(self.ORMx_n,delt_orbitx)*d_value*2
        norm = np.linalg.norm(delt_corrh)
        
        # if norm < 1e-8 :
        #     print("满足精度要求，停止校正",norm)
        #     return

        hcorrVal = hcorrVal-delt_corrh

        for idex, ctem in enumerate(self.pvCORx):
            if hcorrVal[idex] > max_value:
                hcorrVal[idex] = max_value
            ctem.put(hcorrVal[idex])

        ybpmVal = []
        for idex, btem in enumerate(self.pvBPMy):
            ybpmVal.append(btem.get())
        delt_orbity = np.array(ybpmVal)

        vcorrVal = []
        for idex, ctem in enumerate(self.pvCORy):
            vcorrVal.append(ctem.get())

        delt_corrv = np.dot(self.ORMy_n,delt_orbity)*d_value*2
        normv = np.linalg.norm(delt_corrv)
        
        # if normv < 1e-8 :
        #     print("满足精度要求，停止校正",normv)
        #     return
        
        vcorrVal = vcorrVal-delt_corrv

        # for idex, ctem in enumerate(self.pvCORy):
        #     if vcorrVal[idex] > max_value:
        #         vcorrVal[idex] = max_value
        #     ctem.put(vcorrVal[idex])

        # time.sleep(2)
        # self.Tcor = Timer(self.timer_interval, self.Corrector_p)
        # current_modified = os.path.getmtime(filepath)
        # if current_modified != last_modified:
        #     return
        # else:
        #     self.Tcor.start()        

    def correct_one_to_one(self):
        #先测量校正铁的小量变化引入的相邻的下游bpm变化，再反算校正铁应该改变的数值
        #校正强度可以加一个修正系数，目前都没有加
        n_averages=self.samples_perstep # 每次采样个数
        for j in range(len(self.bpm_list_target)):
            print('begin correct:',self.bpm_list_target[j],'\n')

            xbpmVal = 0.0
            ybpmVal = 0.0
            xbpmVald = 0.0
            ybpmVald = 0.0
            hcorrVal = 0.0
            vcorrVal = 0.0

            # 对校正子和BPM进行多次采样平均
            for i in range(n_averages):
                time.sleep(self.timer_interval)
                # pvBPMx_valt = self.pvBPMx[j].get()
                # pvBPMy_valt = self.pvBPMy[j].get()
                # hcorrValt = self.pvCORx[j].get()
                # vcorrValt = self.pvCORy[j].get()
                # xbpmVal = caget('HALF:IN:BPM:BPM03:X:ao')
                # ybpmVal = caget('HALF:IN:BPM:BPM03:Y:ao')
                # hcorrVal = caget(self.pvCORx[j])
                # vcorrVal = caget(self.pvCORy[j]) #校正铁要不要进行平均
                xbpmVal += self.pvBPMx[j].get()
                ybpmVal += self.pvBPMy[j].get()
                hcorrVal += self.pvCORx[j].get()
                vcorrVal += self.pvCORy[j].get() #校正铁要不要进行平均
            xbpmVal = xbpmVal/n_averages
            ybpmVal = ybpmVal/n_averages
            hcorrVal = hcorrVal/n_averages
            vcorrVal = vcorrVal/n_averages

            # 微调校正子后对BPM进行多次采样平均
            self.pvCORx[j].put(hcorrVal+d_value)
            self.pvCORy[j].put(vcorrVal+d_value)
            for i in range(n_averages):
                time.sleep(self.timer_interval)
                xbpmVald += self.pvBPMx[j].get()
                ybpmVald += self.pvBPMy[j].get()
            xbpmVald = xbpmVald/n_averages
            ybpmVald = ybpmVald/n_averages

            # 获得响应函数
            Rx = (xbpmVald-xbpmVal)/d_value
            Ry = (ybpmVald-ybpmVal)/d_value
            print('Rx=',Rx,' Ry=',Ry)

            #根据响应函数调整上游校正子
            hcorrVal = hcorrVal-xbpmVal / Rx
            vcorrVal = vcorrVal-ybpmVal / Ry
            self.pvCORx[j].put(hcorrVal)
            self.pvCORy[j].put(vcorrVal)
            time.sleep(self.timer_interval)

            # 根据BPM精度进一步判断调整校正子
            xbpmVald = self.pvBPMx[j].get()
            ybpmVald = self.pvBPMy[j].get()
            loop = 0
            while abs(xbpmVald)>self.cor_accuracy or abs(ybpmVald)>self.cor_accuracy:    
                loop += 1
                print('cor loop ',loop,'for',self.bpm_list_target[j],'\n')
                hcorrVal = hcorrVal-xbpmVald /Rx
                vcorrVal = vcorrVal-ybpmVald /Ry
                self.pvCORx[j].put(hcorrVal)
                self.pvCORy[j].put(vcorrVal)
                time.sleep(self.timer_interval)
                xbpmVald = self.pvBPMx[j].get()
                ybpmVald = self.pvBPMy[j].get()
            
            print('finish correct:',self.bpm_list_target[j])
            print(self.cor_x_list_target[j],'/',self.cor_y_list_target[j],': (',hcorrVal,',',vcorrVal,')')
            print(self.bpm_list_target[j],':(',xbpmVald,',',ybpmVald,')\n')
            
        #响应矩阵方法的
        # for j in range(N_BPM):
        #     xbpmVal = self.pvBPMx[j].get()
        #     ybpmVal = self.pvBPMy[j].get()
        #     hcorrVal = self.pvCORx[j].get()
        #     vcorrVal = self.pvCORy[j].get()
        #     delt_coh = xbpmVal * self.ORMx_n[j,j]*d_value*2
        #     delt_cov = ybpmVal * self.ORMy_n[j,j]*d_value*2
        #     hcorrVal = hcorrVal-delt_coh
        #     vcorrVal = vcorrVal-delt_cov
        #     self.pvCORx[j].put(hcorrVal)
        #     self.pvCORy[j].put(vcorrVal)
        #     time.sleep(2)
        # self.Tcor = Timer(self.timer_interval, self.Corrector_p)
        # current_modified = os.path.getmtime(filepath)
        # if current_modified != last_modified:
        #     return
        # else:
        #     self.Tcor.start()     
        #    
    def correct_global(self):
        #print(self.a)
        #t1 = time.time()
        # self.a += 1
        xbpmVal = []

        for idex, btem in enumerate(self.pvBPMx):
            xbpmVal.append(btem.get())
        delt_orbitx = np.array(xbpmVal)

        hcorrVal = []
        for idex, ctem in enumerate(self.pvCORx):
            hcorrVal.append(ctem.get())

        delt_corrh = np.dot(self.ORMx_n,delt_orbitx)
        # norm = np.linalg.norm(delt_corrh)
        
        # if norm < 1e-8 :
        #     print("满足精度要求，停止校正",norm)
        #     return

        hcorrVal = hcorrVal-delt_corrh

        for idex, ctem in enumerate(self.pvCORx):
            if hcorrVal[idex] > max_value:
                hcorrVal[idex] = max_value
            if hcorrVal[idex] < -max_value:
                hcorrVal[idex] = -max_value
            ctem.put(hcorrVal[idex])
        time.sleep(2)

        ybpmVal = []
        for idex, btem in enumerate(self.pvBPMy):
            ybpmVal.append(btem.get())
        delt_orbity = np.array(ybpmVal)

        vcorrVal = []
        for idex, ctem in enumerate(self.pvCORy):
            vcorrVal.append(ctem.get())

        delt_corrv = np.dot(self.ORMy_n,delt_orbity)
        # normv = np.linalg.norm(delt_corrv)
        
        # if normv < 1e-8 :
        #     print("满足精度要求，停止校正",normv)
        #     return
        
        vcorrVal = vcorrVal-delt_corrv

        for idex, ctem in enumerate(self.pvCORy):
            if vcorrVal[idex] > max_value:
                vcorrVal[idex] = max_value
            if vcorrVal[idex] < -max_value:
                vcorrVal[idex] = -max_value
            ctem.put(vcorrVal[idex])

        time.sleep(2)
        # self.Tcor = Timer(self.timer_interval, self.Corrector_p)
        # current_modified = os.path.getmtime(filepath)
        # if current_modified != last_modified:
        #     return
        # else:
        #     self.Tcor.start()        

    def start_timer_h(self):
        self.T_h = Timer(self.timer_interval, self.Corrector_h_p)
        self.T_h.start()
 
    def start_timer_v(self):
        self.T_v = Timer(self.timer_interval, self.Corrector_v_p)
        self.T_v.start()
 
    def start_timer(self):
        self.Tcor = Timer(self.timer_interval, self.Corrector_p)
        self.Tcor.start()

if __name__=='__main__':

    # Half-linac
    #    jsonpath  = st.rootpath+"/virtual_machine/half_elegant/halflinac.json" 
    #    iocpath   = st.rootpath+"/softIOC/halflinac"



    # f_res.start_timer_h()
    # f_res.start_timer_v()
    # same to f_res.start_timer()
    # print("zzz")
    # try:
        print("input para.:",sys.argv)
        if sys.argv[1] == "start_cor":
            method = sys.argv[2]
            samp_interval = float(sys.argv[3])     #s
            cor_accuracy = float(sys.argv[4])*1e-6 #m
            samples_perstep = int(sys.argv[5])     #
            target_BPMlist = sys.argv[6].split(',') #

            f_res = correct(samp_interval,cor_accuracy,samples_perstep,target_BPMlist) 
            f_res.init_BPM_pv()
            f_res.init_COR_pv()

            last_modified = os.path.getmtime(filepath)
            current_modified =last_modified

            if  method == "one-to-one":
                f_res.correct_one_to_one()
            
            if  method == "global":
                f_res.correct_global()

        elif sys.argv[1] == "cor_off":
            target_BPMlist = sys.argv[2].split(',') #
            f_res = correct(None ,None ,None , target_BPMlist)
            f_res.reset_cor()

        
    # except:
    #     print('correct error!')
    #     pass
