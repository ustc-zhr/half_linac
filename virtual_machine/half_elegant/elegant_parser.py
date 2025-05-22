#Author: Biaobin Li
#Date:   2024-01-16

import sys, os
import re
from math import *
import random
from collections import defaultdict
import sdds
from epics import caput, caget, caput_many
import json
import matplotlib.pyplot as plt
import numpy as np

from half_linac.virtual_machine.lattice_parser import lattice_parser
import half_linac.setup as st


nest_dict = lambda: defaultdict(nest_dict)

class ele_parser(lattice_parser):
    '''
    Parser for Elegant *.ele files
    '''
    
    def __init__(self, fileName):
        lattice_parser.__init__(self,fileName)
        
        self.fileName = fileName
        self.control  = self.get_control()
    
    def get_control(self):
        lines = self.get_brieflines()
        
        # delete all whitespaces
        j=0
        for line in lines:
            lines[j]=line.replace(' ','')
            j=j+1
        
        # get the parameters
        control = nest_dict()
        
        for line in lines:
            
            pattern1 = re.compile(r'&\w+',re.I)
            pattern2 = re.compile(r'&end',re.I)
            
            if re.match(pattern1,line):
                sec = re.match(pattern1,line).group()[1:]
                continue
            
            elif re.match(pattern2,line):
                continue
            
            tmp = re.split(';|,',line) # in case two assignments in one line    
            # remove white space
            while '' in tmp:
                tmp.remove('')
        
            for j in tmp:
                tmp2 = j.split('=')
        
                # name = tmp2[0].lower()
                name = tmp2[0]
                value = tmp2[1]
    
                control[sec][name] = value
            
        return control
    
    # def back2ele(self,lat_f1,lattice_usedline="lattice.lte"):
    def back2ele(self,ele_f,lat_f):
        '''
        back to one.ele file
        '''
        f =  open(ele_f,"w")
        
        # run_setup section
        line = "&run_setup\n"

        #change: lattice = .lte according input 'lat_f'
        # self.control["run_setup"]["lattice"] = "lattice.lte"
        self.control["run_setup"]["lattice"] = lat_f.split('/')[-1]
        
        
        for key in self.control["run_setup"]:
            line = line +"    " +key +" = " +self.control["run_setup"][key] +",\n"            
        line = line +"&end\n\n"
        
        # run_control section
        line = line +"&run_control\n"
        for key in self.control["run_control"]:
            line = line +"    " +key +" = " +self.control["run_control"][key] +",\n"            
        line = line +"&end\n\n"
        
        # twiss_output section
        line = line +"&twiss_output\n"
        for key in self.control["twiss_output"]:
            line = line +"    " +key +" = " +self.control["twiss_output"][key] +",\n"            
        line = line +"&end\n\n"

        # matrix_output section
        line = line +"&matrix_output\n"
        for key in self.control["matrix_output"]:
            line = line +"    " +key +" = " +self.control["matrix_output"][key] +",\n"            
        line = line +"&end\n\n"  

        # error_element section
        line = line +"&error_control\n"
        for key in self.control["error_control"]:
            line = line +"    " +key +" = " +self.control["error_control"][key] +",\n"            
        line = line +"&end\n\n" 

        line = line +"&error_element\n"
        for key in self.control["error_element"]:
            line = line +"    " +key +" = " +self.control["error_element"][key] +",\n"            
        line = line +"&end\n\n"     

        # beam section
        # sdds_beam section
        if 'sdds_beam' in self.control:
            line = line +"&sdds_beam\n"
            for key in self.control["sdds_beam"]:
                line = line +"    " +key +" = " +self.control["sdds_beam"][key] +",\n"            
            line = line +"&end\n\n"

        # bunched_beam section
        if 'bunched_beam' in self.control:
            line = line +"&bunched_beam\n"
            for key in self.control["bunched_beam"]:
                line = line +"    " +key +" = " +self.control["bunched_beam"][key] +",\n"            
            line = line +"&end\n\n"
        
        # track end
        line = line+"&track &end"
        
        f.write(line)
        f.close()
    

class elegant_parser:
    '''
    1. elegant => python => elegant
    2. extract elegant simulation results

    '''
    def __init__(self,lat_file, ele_file, line_name):
        self.lattice_file = lat_file  #'./elegant/lattice_ini.lte'
        self.line = line_name         #'ALL'
        self.ele_file = ele_file      #'./elegant/one_ini.ele'
        
        # get lattice
        lte = lattice_parser(self.lattice_file,self.line)
        self.lattice, self.trackline_names_list = lte.get_lattice_tracklinenameslist()

        # get control
        self.ele = ele_parser(self.ele_file)
        self.control = self.ele.get_control()

        # dump lattice with channel added to halflinac.json 
        #self.dump2json()

    def dump2json(self,j_file="halflinac.json"):
        jsonfile = nest_dict()
        
        # add epics channel/PV
        self._add_channel()

        # control in dict
        jsonfile["control"] = self.control
        jsonfile["lattice"] = self.lattice
        jsonfile["usedline"] = self.trackline_names_list
        
        with open(j_file,"w") as f:
           f.write(json.dumps(jsonfile, indent=4))

    def json2lte_ele(self,lat_f = "./elegant/lattice.lte", ele_f = "./elegant/one.ele",j_file="halflinac.json"):
        
        # update lattice.lte with halflinac.json 
        #============================
        f = open(j_file,"r")
        lte = json.load(f)
        f.close()
    
        #halflinac.json => lattice.lte 
        f = open(lat_f,"w") 
        
        lattice = lte["lattice"]
        tmpnamelist = []
        for elem_name in lte["usedline"]: 
            tmpnamelist.append(lattice[elem_name]["NAME"])
            
            if tmpnamelist.count(lattice[elem_name]["NAME"]) >1:
                continue    
            
            tmp = lattice[elem_name]["NAME"]+": "+lattice[elem_name]["TYPE"]
            for key in lattice[elem_name].keys():
                # AP/channel should not appear in lattice.lte
                if key not in ["NAME","TYPE","AP"]:
                    tmp = tmp +"," +key +"=\"" +lattice[elem_name][key] + "\""

            tmp = tmp+"\n"                  
            f.write(tmp)        
                
        # line2 = "\nALL: LINE = (" +','.join(lte["usedline"]) +")"
        line2 = "\nUSEDLINE: LINE = (" +','.join(lte["usedline"]) +")"
        f.write(line2)
        f.close()        
      
        # update one.ele with json file
        #==============================
        self.ele.control = lte["control"]
        self.ele.back2ele(ele_f,lat_f)
    
    # sub-funcs for simulation results
    #----------------------------------------------
    def get_bpmdata(self):
        tmp = sdds.SDDS(0)
        
        tmp.load('./elegant/one.bpmcen')
        colname = tmp.columnName
        data    = tmp.columnData

        out = {}
        cnt=0
        for j in colname:
            out[j]=data[cnt][0]
            cnt = cnt+1
        
        bpm = nest_dict()
        cnt = 0
        for elem in out['ElementName']:
            bpm[elem.upper()]['Cx'] = out['Cx'][cnt]
            bpm[elem.upper()]['Cy'] = out['Cy'][cnt]
            cnt = cnt+1
        
        return bpm
    
    def broadcast_bpm(self):
        '''
        broadcast the BPM values to epics PV
        '''
        bpm = self.get_bpmdata()
        
        pvlx = []
        pvly = []
        pvlvalx = []
        pvlvaly = []
        for key in bpm :
            pvlx_temp = "HALF:IN:BPM:"+key+":X:ao"
            pvlvalx_temp  = bpm[key]['Cx']

            pvly_temp = "HALF:IN:BPM:"+key+":Y:ao"
            pvlvaly_temp  = bpm[key]['Cy']

            pvlx.append(pvlx_temp)
            pvly.append(pvly_temp)
            pvlvalx.append(pvlvalx_temp)
            pvlvaly.append(pvlvaly_temp)
        
        # put the values
        caput_many(pvlx,pvlvalx) 
        caput_many(pvly,pvlvaly) 


    def broadcast_flag(self):
        # get watch channel
        #with open("./halflinac.json","r") as f:
        #    lte = json.load(f)
        #lattice = lte["lattice"]
        #print("ffshflkshglhlshgl")
        for key in self.lattice:
            #print(key)
            if (self.lattice[key]["TYPE"] == "WATCH") and (self.lattice[key]["MODE"].lower() == "coord")  and (self.lattice[key]["DISABLE"] == '0') and ("PRF" in self.lattice[key]["NAME"]):
                self.lattice[key]["AP"] = "HALF:IN:FLAG:"+key+":image1:ArrayData:vm"
                channel = self.lattice[key]["AP"]
                #file_id =  lattice[key]["NAME"]

                tmp = self._get_watch_image(key)
                
                # print("broadcasting flag image data ...")
                caput(channel, tmp)
                # print("broadcasting flag image data finished.")

    def _get_watch_image(self, file_id):
        #pha = np.loadtxt("./impz/fort."+file_id, skiprows=1)
        tmp = sdds.SDDS(0)
        # print(file_id)
        tmp.load('./elegant/'+file_id+".out")
        tmpx = tmp.columnData[0][0]
        tmpy = tmp.columnData[2][0]
        

        # the real size of flag
        # x1=-0.5*st.flag_pixel_machine[0]*st.flag_pixel_width*1e-3 #[m]
        # x2= 0.5*st.flag_pixel_machine[0]*st.flag_pixel_width*1e-3 #[m]
        # y1=-0.5*st.flag_pixel_machine[1]*st.flag_pixel_width*1e-3 #[m]
        # y2= 0.5*st.flag_pixel_machine[1]*st.flag_pixel_width*1e-3 #[m]        
        x1=-0.5*st.flag_pixel_vm[0]*st.flag_pixel_width*1e-3 #[m]
        x2= 0.5*st.flag_pixel_vm[0]*st.flag_pixel_width*1e-3 #[m]
        y1=-0.5*st.flag_pixel_vm[1]*st.flag_pixel_width*1e-3 #[m]
        y2= 0.5*st.flag_pixel_vm[1]*st.flag_pixel_width*1e-3 #[m]
        
        plt.figure()
        pixel_vm = st.flag_pixel_vm
        h = plt.hist2d(tmpx ,tmpy, bins=[pixel_vm[0],pixel_vm[1]],cmap=plt.cm.jet,range=[[x1,x2],[y1,y2]])
        # plt.show(block=False)
        # plt.pause(2)
        tmp = np.reshape(h[0].transpose(),(np.size(h[0]),))  #change to 1d array
        plt.close()  # 关闭图表，释放资源
        #plt.figure()
        #plt.subplot(1,2,1)
        #h = plt.hist2d(pha[:,1],pha[:,3], bins=[pixel_vm[0],pixel_vm[1]],cmap=plt.cm.jet)
        #plt.colorbar()
        #plt.show()
        #
        ## extract the pixel data from the plot
        #tmp = np.reshape(h[0].transpose(),(np.size(h[0]),))  #change to 1d array
        #
        ## reprode the pixel image
        #imag = np.reshape(tmp,(pixel_vm[1],pixel_vm[0]))
        #data_max = np.max(imag)
        #vnorm = mpl.colors.Normalize(vmin=0, vmax=data_max)        
        #plt.subplot(1,2,2)
        #plt.imshow(imag, norm=vnorm, cmap="jet", origin="lower")
        #plt.colorbar()
        #plt.show()
        
        return tmp
    def _add_channel(self):
        for key in self.lattice:
            if self.lattice[key]["TYPE"] == "QUAD":
                # add PV attibute
                self.lattice[key]["AP"] = "HALF:IN:QUAD:"+key+":K1"
                
            elif self.lattice[key]["TYPE"] in ["CSRCSBEND", "CSBEND", "BEND", "SBEN", "SBEND"]:
                self.lattice[key]["AP"] = "HALF:IN:BEND:"+key+":ANGLE"
            
            # RFCW, COR,... and so on


if __name__=='__main__':

    lattice_file = './elegant/lattice_ini.lte'
    line_name    = 'ALL'
    ele_file     = './elegant/one_ini.ele'  

    lte = elegant_parser(lattice_file, ele_file, line_name)
    # lte.dump2json()

    # lte.json2lte_ele()   

    lte.broadcast_flag()     
    
    # # run elegant
    # elegant_path = "./elegant"
    # top_path = os.getcwd()
    
    # os.chdir(elegant_path)
    # os.system("./one")
    # os.chdir(top_path)
    
    # # update bpm data
    # ele.broadcast_bpm()


    
    
    
