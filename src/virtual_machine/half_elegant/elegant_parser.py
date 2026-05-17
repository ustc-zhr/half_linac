#Author: Biaobin Li
#Date:   2024-01-16

import re
from math import *
from collections import defaultdict
import sdds
import epics.ca
from epics import caput, caget, caput_many
import json
import matplotlib.pyplot as plt
import numpy as np
from pathlib import Path

from half_linac.src.virtual_machine.lattice_parser import lattice_parser
from half_linac.src.virtual_machine.half_elegant.runtime_state import (
    read_runtime_state,
    write_runtime_state,
)
import half_linac.setup as st


EPICS_CONNECTION_TIMEOUT_S = 0.5
EPICS_PUT_TIMEOUT_S = 5.0


nest_dict = lambda: defaultdict(nest_dict)

class ele_parser(lattice_parser):
    '''
    Parser for Elegant *.ele files
    '''
    
    def __init__(self, fileName):
        super().__init__(self,fileName) # 继承lattice_parser是为了调用相关的一些子函数 如get_brieflines 这里不需要输入lineName 因为这里不调用相关函数
        
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
        with open(ele_f,"w") as f:
        
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
    

class elegant_parser:
    '''
    1. elegant => python => elegant
    2. extract elegant simulation results

    '''
    def __init__(self,lat_file, ele_file, line_name):
        self.lattice_file = lat_file  #'./elegant/lattice_ini.lte'
        self.line = line_name         #'ALL'
        self.ele_file = ele_file      #'./elegant/one_ini.ele'
        self.vm_dir = Path(st.rootpath) / "src/virtual_machine/half_elegant"
        self.elegant_dir = self.vm_dir / "elegant"
        self.runtime_json_path = self.vm_dir / "halflinac.json"
        
        # get lattice
        lte = lattice_parser(self.lattice_file,self.line)
        self.lattice, self.trackline_names_list = lte.get_lattice_tracklinenameslist()

        # get control
        self.ele = ele_parser(self.ele_file)
        self.control = self.ele.get_control()

        # dump lattice with channel added to halflinac.json 
        #self.dump2json()

    def _resolve_runtime_json_path(self, j_file):
        if j_file == "halflinac.json":
            return self.runtime_json_path
        return Path(j_file)

    def _resolve_elegant_path(self, pathlike, default_name):
        if pathlike == default_name:
            return self.elegant_dir / default_name
        return Path(pathlike)

    def build_runtime_state(self):
        jsonfile = nest_dict()

        # add epics channel/PV
        self._add_channel()

        jsonfile["control"] = self.control
        jsonfile["lattice"] = self.lattice
        jsonfile["usedline"] = self.trackline_names_list

        return jsonfile

    def dump2json(self,j_file="halflinac.json"):
        write_runtime_state(self._resolve_runtime_json_path(j_file), self.build_runtime_state())

    def _add_channel(self):
        for key in self.lattice:
            if self.lattice[key]["TYPE"] == "QUAD":
                # add PV attibute
                self.lattice[key]["AP"] = st.pv_prefix_quad + key + st.pv_suffix_quad
                
            elif self.lattice[key]["TYPE"] in ["CSRCSBEND", "CSBEND", "BEND", "SBEN", "SBEND"]:
                self.lattice[key]["AP"] = st.pv_prefix_bend + key + st.pv_suffix_bend

            elif self.lattice[key]["TYPE"] in ["HKICK","VKICK"]:
                self.lattice[key]["AP"] = st.pv_prefix_cor + key + st.pv_suffix_cor
            
            # RFCW, COR,... and so on

    
    # json2lte_ele broadcast_bpm broadcast_flag cycle
    # ===============================================
    def json2lte_ele(self,lat_f = "./elegant/lattice.lte", ele_f = "./elegant/one.ele",j_file="halflinac.json"):
        json_path = self._resolve_runtime_json_path(j_file)
        lattice_path = self._resolve_elegant_path(lat_f, "lattice.lte")
        ele_path = self._resolve_elegant_path(ele_f, "one.ele")

        # update lattice.lte with halflinac.json (according to "usedline")
        #============================
        with json_path.open("r", encoding="utf-8") as f:
            lte  = json.load(f)
    
        #halflinac.json => lattice.lte 
        with lattice_path.open("w", encoding="utf-8") as f:
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
                    
            line2 = "\nUSEDLINE: LINE = (" +','.join(lte["usedline"]) +")"
            f.write(line2)
    
    
        # update one.ele with json file
        #==============================
        self.ele.control = lte["control"]
        self.ele.back2ele(str(ele_path), str(lattice_path))
    
    # sub-funcs for simulation results
    #----------------------------------------------
    def _publish_many_best_effort(self, label, pv_names, values):
        if not pv_names:
            return True

        try:
            results = caput_many(
                pv_names,
                values,
                wait=False,
                connection_timeout=EPICS_CONNECTION_TIMEOUT_S,
                put_timeout=EPICS_PUT_TIMEOUT_S,
            )
        except epics.ca.ChannelAccessException as exc:
            print(f"{label} publish skipped: {exc}")
            return False
        except Exception as exc:
            print(f"{label} publish skipped: {exc}")
            return False

        failures = 0
        if results is not None:
            failures = sum(1 for result in results if result in (None, False))

        if failures:
            print(f"{label} publish incomplete: {failures}/{len(pv_names)} PV writes were not confirmed.")
            return False

        return True

    def _publish_flags_best_effort(self, flag_updates):
        if not flag_updates:
            return True

        try:
            failures = 0
            for channel, value in flag_updates:
                result = caput(
                    channel,
                    value,
                    wait=False,
                    connection_timeout=EPICS_CONNECTION_TIMEOUT_S,
                    timeout=EPICS_PUT_TIMEOUT_S,
                )
                if result in (None, False):
                    failures += 1
        except epics.ca.ChannelAccessException as exc:
            print(f"flag publish skipped: {exc}")
            return False
        except Exception as exc:
            print(f"flag publish skipped: {exc}")
            return False

        if failures:
            print(f"flag publish incomplete: {failures}/{len(flag_updates)} PV writes were not confirmed.")
            return False

        return True

    def broadcast_bpm(self):
        '''
        broadcast the BPM values to epics PV
        '''
        bpm = self._get_bpmdata()
        
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
        x_ok = self._publish_many_best_effort("BPM X", pvlx, pvlvalx)
        y_ok = self._publish_many_best_effort("BPM Y", pvly, pvlvaly)
        return x_ok and y_ok

    def broadcast_flag(self):
        # get watch channel
        lte = read_runtime_state(self.runtime_json_path)
        usedline = lte["usedline"] # 保证只有usedline里面的PRF才会被发布到ioc
        flag_updates = []

        for key in self.lattice:
            #print(key)
            if (self.lattice[key]["TYPE"] == "WATCH") and (self.lattice[key]["MODE"].lower() == "coord")  and (self.lattice[key]["DISABLE"] == '0') \
                and ("PRF" in self.lattice[key]["NAME"]) and (key in usedline):

                self.lattice[key]["AP"] = "HALF:IN:FLAG:"+key+":image1:ArrayData:vm"
                channel = self.lattice[key]["AP"]
                #file_id =  lattice[key]["NAME"]
                
                if "ESA" in key:
                    tmp = self._get_watch_image(key,"ESA")
                else:
                    tmp = self._get_watch_image(key)

                flag_updates.append((channel, tmp))

        return self._publish_flags_best_effort(flag_updates)


    def _get_bpmdata(self):
        tmp = sdds.SDDS(0)
        
        tmp.load(str(self.elegant_dir / "one.bpmcen"))
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

    def _get_watch_image(self, file_id, index="normal"):
        # the size of different flag 
        if index == "normal": 
            flag_pixel_vm = st.flag_pixel_vm
            flag_pixel_width = st.flag_pixel_width
        elif index == "ESA":
            flag_pixel_vm = st.ESAflag_pixel_vm
            flag_pixel_width = st.ESAflag_pixel_width
        x1=-0.5*flag_pixel_vm[0]*flag_pixel_width*1e-3 #[m]
        x2= 0.5*flag_pixel_vm[0]*flag_pixel_width*1e-3 #[m]
        y1=-0.5*flag_pixel_vm[1]*flag_pixel_width*1e-3 #[m]
        y2= 0.5*flag_pixel_vm[1]*flag_pixel_width*1e-3 #[m]                 
        
        # get the data
        #pha = np.loadtxt("./impz/fort."+file_id, skiprows=1)
        tmp = sdds.SDDS(0)
        # print(file_id)
        tmp.load(str(self.elegant_dir / f"{file_id}.out"))
        tmpx = tmp.columnData[0][0]
        tmpy = tmp.columnData[2][0]
        

        # 直接计算2D直方图
        hist, xedges, yedges = np.histogram2d(
            tmpx, tmpy, 
            bins=[flag_pixel_vm[0], flag_pixel_vm[1]], 
            range=[[x1, x2], [y1, y2]]
        )
        # 将结果转为一维数组
        tmp = np.reshape(hist.transpose(), (np.size(hist),))
        
        # plt.figure()
        
        # h = plt.hist2d(tmpx ,tmpy, bins=[flag_pixel_vm[0],flag_pixel_vm[1]],cmap=plt.cm.jet,range=[[x1,x2],[y1,y2]])
        # # plt.show(block=False)
        # # plt.pause(2)
        # tmp = np.reshape(h[0].transpose(),(np.size(h[0]),))  #change to 1d array
        # plt.close()  # 关闭图表，释放资源
        # #plt.figure()
        # #plt.subplot(1,2,1)
        # #h = plt.hist2d(pha[:,1],pha[:,3], bins=[pixel_vm[0],pixel_vm[1]],cmap=plt.cm.jet)
        # #plt.colorbar()
        # #plt.show()
        # #
        # ## extract the pixel data from the plot
        # #tmp = np.reshape(h[0].transpose(),(np.size(h[0]),))  #change to 1d array
        # #
        # ## reprode the pixel image
        # #imag = np.reshape(tmp,(pixel_vm[1],pixel_vm[0]))
        # #data_max = np.max(imag)
        # #vnorm = mpl.colors.Normalize(vmin=0, vmax=data_max)        
        # #plt.subplot(1,2,2)
        # #plt.imshow(imag, norm=vnorm, cmap="jet", origin="lower")
        # #plt.colorbar()
        # #plt.show()
        
        return tmp



if __name__=='__main__':
    # print("test elegant parser")
    # tmp = sdds.SDDS(0)
        
    # tmp.load('./elegant/one.bpmcen')
    # colname = tmp.columnName
    # data    = tmp.columnData
    
    # lattice_file = './elegant/lattice_ini.lte'
    # line_name    = 'ALL'
    # ele_file     = './elegant/one_ini.ele'  
    lattice_file = st.rootpath+"/src/virtual_machine/half_elegant/elegant/lattice_ini.lte"
    ele_file     = st.rootpath+"/src/virtual_machine/half_elegant/elegant/one_ini.ele"
    line_name    = 'ALL'

    lte = elegant_parser(lattice_file, ele_file, line_name)
    lte.dump2json()

    # lte.json2lte_ele()   

    # lte.broadcast_flag()     

    
    # # run elegant
    # elegant_path = "./elegant"
    # top_path = os.getcwd()
    
    # os.chdir(elegant_path)
    # os.system("./one")
    # os.chdir(top_path)
    
    # # update bpm data
    # ele.broadcast_bpm()


    
    
    
