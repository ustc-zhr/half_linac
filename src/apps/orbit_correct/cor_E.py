# Author: Shancai Zhang
# Date: 2024-09-05
# 利用校正铁来估算能量是不是达到理想值
import sys
#sys.path.append('e:\half_linac')
from epics import caget, caget_many,caput,caput_many,PV
import os
import re
import half_linac.setup as st

d_value = 0.01*0.001
d_value1 
RESPM_FILE = st.rootpath+'/apps/orbit_correct/response.dat'
RM = np.loadtxt(RESPM_FILE)
ORM = np.reshape(RM,(82,82),order='C')
ORM_x = RM[0:N_BPM,0:N_COR]
ORMx_n = np.linalg.inv(ORM_x)

def COR_E(COR):
    cor_n = str(re.findall(r'\d+', COR)[0])
    pv_CORx = PV("HALF:IN:COR:XC" + cor_n + ":ao")
    #pv_CORy = PV("HALF:IN:COR:YC" + cor_n + ":ao")
    pv_BPMx = PV("HALF:IN:BPM:BPM" + cor_n + ":X:ao")  
    #pv_BPMy = PV("HALF:IN:BPM:BPM" + cor_n + ":Y:ao") 
    #print(pv_CORx)
    xbpmVal = 0
    #ybpmVal = 0
    xbpmVald = 0
    #ybpmVald = 0
    hcorrVal = 0
    #vcorrVal = 0
    for i in range(n_averages):
        time.sleep(2)
        xbpmVal += self.pvBPMx[j].get()
        #ybpmVal += self.pvBPMy[j].get()
    hcorrVal += self.pvCORx[j].get()
    #vcorrVal += self.pvCORy[j].get() 
    xbpmVal = xbpmVal/n_averages
    #ybpmVal = ybpmVal/n_averages
    self.pvCORx[j].put(hcorrVal+d_value)
    #self.pvCORy[j].put(vcorrVal+d_value)
    for i in range(n_averages):
        time.sleep(2)
        xbpmVald += self.pvBPMx[j].get()
        #ybpmVald += self.pvBPMy[j].get()
    xbpmVald = xbpmVald/n_averages
    #ybpmVald = ybpmVald/n_averages
    delt_coh = xbpmVal /(xbpmVald-xbpmVal)*d_value
    delt_coh1 = xbpmVal * ORMx_n[j,j]*d_value*2
    #delt_cov = ybpmVal /(ybpmVald-ybpmVal)*d_value
    #hcorrVal = hcorrVal-delt_coh
    #vcorrVal = vcorrVal-delt_cov    
    if delt_coh/delt_coh1 > 1.01 :
        print("enery > theory",delt_coh/delt_coh1)
    elif delt_coh/delt_coh1 < 0.99:
        print("enery < theory",delt_coh/delt_coh1)
    else:
        print("enery = theory")


if __name__=='__main__':
    COR_E('XC07')
