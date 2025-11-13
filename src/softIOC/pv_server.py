import epics 
import json
import time
import os
import numpy as np

import half_linac.setup as st


# -------------
# PAY ATTENTION
# -------------
# PV rule is: HALF:LN:QUAD:QL01:K1, element name should place at 4th 
class pv_server:
    def __init__(self, jsonpath, iocpath):
        self.pvl = []
        self.pv_val = []
        
        self.jsonpath = jsonpath    #st.rootpath+"/virtual_machine/half_elegant/halflinac.json"
        self.iocpath = iocpath     
        self.substitutions_name = jsonpath.split("/")[-1].split(".")[0] # keep the same name with .json file
    
    def gen_substitution_file(self):
        '''
        generate db/half.substitutions file
        '''
        # read lattice from json
        with open(self.jsonpath,"r") as f:
            lte = json.load(f)

        lattice = lte["lattice"]
        
        with open(self.iocpath + "/db/" + self.substitutions_name + '.substitutions', 'w') as f:
            # for QUAD
            f.write("file db/quad.template {\n")
            f.write("  pattern {QUAD}\n")
            for key in lattice:
                if lattice[key]["TYPE"] == "QUAD":   
                    f.write("  { \"" +lattice[key]["NAME"] +"\" }\n")
            f.write("}\n")
                
            # for BEND
            f.write("file db/bend.template {\n")
            f.write("  pattern {BEND}\n")
            for key in lattice:
                if lattice[key]["TYPE"] in ["BEND", "CSRCSBEND", "SBEND", "SBEN"]:   
                    f.write("  { \"" +lattice[key]["NAME"] +"\" }\n")
            f.write("}\n")
            
            # for BPM
            f.write("file db/bpm.template {\n")
            f.write("  pattern {BPM}\n")
            for key in lattice:
                if lattice[key]["TYPE"] == "MONI":   
                    f.write("  { \"" +lattice[key]["NAME"] +"\" }\n")
            f.write("}\n")
            
            # for FLAG
            f.write("file db/flag.template {\n")
            f.write("  pattern {FLAG}\n")
            for key in lattice:
                if lattice[key]["TYPE"] == "WATCH":   
                    f.write("  { \"" +lattice[key]["NAME"] +"\" }\n")
            f.write("}\n")
    
            # for COR
            f.write("file db/corr.template {\n")
            f.write("  pattern {COR}\n")
            for key in lattice:
                if lattice[key]["TYPE"] in ["HKICK", "VKICK"] :   
                    f.write("  { \"" +lattice[key]["NAME"] +"\" }\n")
            f.write("}\n")
        
                    
        # and so on 
        # ...
    
    def init_lattice_pv(self):
        '''
        initialize all PVs' value based on lattice.json
        '''
        with open(self.jsonpath,"r") as f:
            lte = json.load(f)
        
        lattice = lte["lattice"]
        print(lattice["SM"])
 
        ##1. add channel to json file
        ##---------------------------
        #for key in lattice:
        #    if lattice[key]["TYPE"] == "QUAD":
        #        # add AP attibute
        #        lattice[key]["AP"] = "HALF:IN:QUAD:"+key+":K1"

        #    elif lattice[key]["TYPE"] in ["CSRCSBEND", "CSBEND", "BEND", "SBEN", "SBEND"]:
        #        lattice[key]["AP"] = "HALF:IN:BEND:"+key+":ANGLE"

        #    # RFCW, COR,... and so on
        #    # to be finished yet
        #
        ##write back to json file
        #with open(self.jsonpath,"w") as f:
        #    f.write(json.dumps(lte, indent=4))

        #2. broadcast element setting values to epics
        #----------------------------     
        pvl = []
        pv_val = []
        # init all quads
        for key in lattice:
            if lattice[key]["TYPE"] == "QUAD":        
                pvl.append(lattice[key]["AP"])
                pv_val.append(lattice[key]["K1"])

            elif lattice[key]["TYPE"] in ["BEND", "CSRCSBEND", "SBEND", "SBEN"]:  
                pvl.append(lattice[key]["AP"])
                pv_val.append(lattice[key]["ANGLE"])

            # KICK pv value isn't set in *_ini.ele  AND pv is n't set in initial json file
            # elif lattice[key]["TYPE"] in ["HKICK","VKICK"]:    
            #     lattice[key]["AP"] = "HALF:IN:COR:"+key+":ao" 
            #     lattice[key]["KICK"] = "0" 
            #     pvl.append(lattice[key]["AP"])
            #     pv_val.append(lattice[key]["KICK"])


            # to be finished 
            # RFCW, and so on
       

        self.pvl = pvl
        self.pv_val = pv_val
        epics.caput_many(pvl, pv_val)
        #print(pvl)

    def monitor_json(self):
        '''
        In case caput a new value to a PV, the lattice.json should also be updated
        '''
        # pvl_value = epics.caget_many(self.pvl)

        def onChanges(pvname=None, value=None, char_value=None,**kw):
            print('PV Changed:', pvname,", new value=", value, ", time:",time.ctime())
            print("lattice.json will be updated.\n")
            
            with open(self.jsonpath,"r") as f:
                lte = json.load(f)
            
            lattice = lte["lattice"]

            # get the element name from PV 
            tmp = pvname.split(":")
            if tmp[2] == "QUAD":
                # HALF:LN:QUAD:QL01S:K1 => QL01S, so rule should be REALLY important 
                elem_name = tmp[3]
                lattice[elem_name]["K1"] = str(value)
            
            elif tmp[2] == "BEND":
                # kicker
                # IRFEL:PS:HC01:current:ao
                elem_name = tmp[3]
                lattice[elem_name]["ANGLE"] = str(value)

            elif tmp[2] == "COR":
                # kicker
                # IRFEL:PS:HC01:current:ao
                elem_name = tmp[3]
                lattice[elem_name]["KICK"] = str(value)

            # to be finished, RFCW, BEND, ...
            # ...
            
            with open(self.jsonpath,"w") as f:
                f.write( json.dumps(lte, indent=4))
        
        for pv in self.pvl:
            mypv = epics.PV(pv)
            mypv.add_callback(onChanges)

#     def gen_random_Q_err(self):
#         seed = 123456789
#         # 设定高斯分布的参数
#         mu = 0  # 均值
#         sigma = 0.5e-4  # 标准差

#         # 生成高斯分布的随机数
# #        datax = np.random.normal(mu, sigma, 1)[0]  # 假设生成10000个数据点
# #        datay = np.random.normal(mu, sigma, 1)[0]
#         # 3σ截断
#         lower_bound = mu - 3 * sigma
#         upper_bound = mu + 3 * sigma

#         f = open(self.jsonpath, "r")
#         lte = json.load(f)
#         lattice = lte["lattice"]
#         f.close()
#         for key in lattice:
#             if lattice[key]["TYPE"] == "QUAD":
#                 datax = np.random.normal(mu, sigma, 1)[0]
#                 datay = np.random.normal(mu, sigma, 1)[0]
#                 while not (lower_bound <= datax <= upper_bound):
#                     datax = np.random.normal(mu, sigma, 1)[0]
#                 lattice[key]["DX"] = str(datax)
#                 while not (lower_bound <= datay <= upper_bound):
#                     datay = np.random.normal(mu, sigma, 1)[0]
#                 lattice[key]["DY"] = str(datay)
#         f = open(self.jsonpath, "w")
#         f.write(json.dumps(lte, indent=4))
#         f.close()

#     def err_off(self):

#         f = open(self.jsonpath, "r")
#         lte = json.load(f)
#         lattice = lte["lattice"]
#         f.close()
#         for key in lattice:
#             if lattice[key]["TYPE"] == "QUAD":
#                 lattice[key]["DX"] = "0"
#                 lattice[key]["DY"] = "0"
#         f = open(self.jsonpath, "w")
#         f.write(json.dumps(lte, indent=4))
#         f.close()        



from subprocess import Popen
if __name__=='__main__':

    jsonpath  = st.rootpath+"src/virtual_machine/half_elegant/halflinac.json" 
    iocpath   = st.rootpath+"src/softIOC/halflinac"

    myserver = pv_server(jsonpath, iocpath)
    
    #1. lattice.lte => ./softIOC/db/half.substitutions 
    myserver.gen_substitution_file()
 
    #2. start softIOC
    Popen("./runMe", cwd=iocpath, shell=True)

    #3. initialize epics PV with the values from lattice.lte
    myserver.init_lattice_pv()

    #4. Now, we can monitor the lattice pv, in case they have changes
    myserver.monitor_json()
    # #---------------------------------
    print('Now wait for changes')
    
    # 让该程序长时间运行下去 且添加时间间隔避免过分占资源
    t0 = time.time()
    while time.time() - t0 < 100000.0:
    
        time.sleep(1.e-3)
    print('Done.')
    
