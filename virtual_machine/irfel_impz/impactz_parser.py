#author: Biaobin Li
#email: biaobin@ustc.edu.cn

import sys
import re
from collections import defaultdict
from copy import deepcopy
from math import sqrt, pi
import math
from half_linac.virtual_machine.irfel_impz import const
from half_linac.virtual_machine.lattice_parser import lattice_parser
import json

nest_dict = lambda: defaultdict(nest_dict) # to define a['key1']['key2'] = value
                 
# class: impactz_parser
#======================
class impactz_parser(lattice_parser):
    '''
    transform lte.impz => ImpactZ.in.
    
    '''
    def __init__(self, fileName=None, lineName=None):
        lattice_parser.__init__(self,fileName, lineName)

        if fileName==None:
            pass
        else:
            # get brief lte.impz lines
            self.lines = self.get_brieflines()
            
            self.control = {}
            self.beam = {}
            self.lattice = nest_dict()
            
            # initiate with default values
            self.__default_control()
            self.__default_beam()
            self.__default_lattice()
            
            # update with read in *.impz file
            self.control = self.update_control()
            self.beam    = self.update_beam()
            self.trackline, self.usedline = self.update_trackline()

            # some parameters of the beam
            self.Scxl = None
            self.gam0 = None
            self.gambet0 = None
            self.bet0 = None
            self.mass = None
            self.update_paras()
       
    def dump2json(self):
        jsonfile = nest_dict()
        jsonfile["control"] = self.control
        jsonfile["beam"] = self.beam

        # all elements in dict, no duplicates
        namelist = []
        s = 0
        for elem in self.trackline:
            if elem["NAME"] in namelist:
                print("ERROR, duplicated element name:", elem["NAME"])
                print("Please update lattice file !!! Program stops!")
                sys.exit(0)

            # add s for every element
            # ========================
            if "L" in elem.keys():
                s = s+float(elem["L"])
                # position at entrance
                elem["S"] = str( s-float(elem["L"]) )
            else:
                s = s + 0
                elem["S"] = str( s )
                
            # add channel information
            # ========================
            if elem["TYPE"] == "QUAD":
                elem["AP"] = "IRFEL:AP:QUAD:"+elem["NAME"]+":K1:ao"

            elif elem["TYPE"] in ["CSRCSBEND", "CSBEND", "BEND", "SBEN", "SBEND"]:
                elem["AP"] = "IRFEL:AP:BEND:"+elem["NAME"]+":ANGLE"

            elif elem["TYPE"] in ["WATCH"]:
                if elem["COORD_INFO"] == "1":
                    elem["AP"] = "IRFEL:BD:"+elem["NAME"]+":image1:ArrayData:vm"

            elif elem["TYPE"] in ["MONI"]:
                elem["AP"]["X"] = "IRFEL:BD:"+elem["NAME"]+":X"
                elem["AP"]["Y"] = "IRFEL:BD:"+elem["NAME"]+":Y"

            elif elem["TYPE"] in ["HKICK","VKICK"]:
                elem["AP"] = "IRFEL:PS:"+elem["NAME"]+":current:ao"

            # RFCW,... and so on
            
            # end channel
            # ======================== 
            namelist.append(elem["NAME"])
            jsonfile["lattice"][elem["NAME"]] = elem

        jsonfile["usedline"] = self.usedline
        
        with open("irfel.json","w") as f:
            f.write(json.dumps(jsonfile, indent=4))

    def json2impzin(self):
        f = open("irfel.json","r")
        lte = json.load(f)
        f.close()

        #update self.control and self.beam with json file
        self.beam = {}
        self.control = {}
        self.beam = lte["beam"]
        self.control = lte["control"]

        # update self.trackline with json file
        self.trackline = []
        for elem in lte["usedline"]:
            self.trackline.append( lte["lattice"][elem])

        control_file = self.impactzin_control()
        lattice_file = self.impactzin_lattice()
        
        with open('./impz/ImpactZ.in','w') as f:
            f.write(control_file)
            f.write(lattice_file)

        f.close()     

    def write_impactzin(self):
        '''
        generate ImpactZ.in file.
        '''
        control_file = self.impactzin_control()
        lattice_file = self.impactzin_lattice()
        
        with open('ImpactZ.in','w') as f:
            f.write(control_file)
            f.write(lattice_file)

        f.close()     
 
    # default control, beam, lattice dicts
    def __default_control(self):    
        self.control['CORE_NUM_T'] = 1  #transverse direction computor core number      
        self.control['CORE_NUM_L'] = 1  #longitudinal direction 
        self.control['INTEGRATOR'] = 1  # 1 for linear map, 2 for lorentz integrator
        self.control['MESHX'] = 64
        self.control['MESHY'] = 64
        self.control['MESHZ'] = 64
        self.control['KINETIC_ENERGY'] = 0;  # kinetic energy W, E=W+E0
        self.control['FREQ_RF_SCALE'] = 2.856e9
        self.control['DEFAULT_ORDER'] = 1
        self.control['STEPS'] = 1 #1kick/m
        self.control['MAPS']  = 1 #1map/half_step
        self.control['TSC'] = 0
        self.control['LSC'] = 0
        self.control['CSR'] = 0
        self.control['ZWAKE'] = 0
        self.control['TRWAKE'] = 0
        self.control['PIPE_RADIUS'] = 0.014 #pipe radius[m], by default, 0.014m
        self.control['TURN'] = 1
        self.control['N_TURN_OUT'] = 1
        self.control['RINGSIMU']  = 0 
        self.control['SAMPLE_OUT'] = 1e5   #WATCH element, output how many particles
        self.control['SLICE_BIN'] = 128    #WATCH element, slice num
        self.control['ERROR'] = 0
        self.control['FLAGLSC'] = '3D'

        # turn all para values to str data type
        for key in self.control:
            self.control[key] = str( self.control[key] )

    def __default_beam(self):
        self.beam['MASS'] = const.electron_mass
        self.beam['CHARGE'] = -1.0
        self.beam['NP']   = int(1e3)
        self.beam['TOTAL_CHARGE'] = 1.0e-12 #[C]
        
        self.beam['SIGX'] = 0.0     #[m]
        self.beam['SIGY'] = 0.0     #[m]      
        self.beam['SIGZ'] = 0.0     #[m]
        
        self.beam['SIGXP'] = 0.0    #sig_gambetx/gambet0 [rad]
        self.beam['SIGYP'] = 0.0    #sig_gambety/gambet0 [rad]
        self.beam['SIGE']  = 0.0   # (E-E0)  [eV] 
        
        # for twiss parameters settings
        self.beam['EMIT_X'] = 0.0
        self.beam['EMIT_NX'] = 0.0   
        self.beam['BETA_X'] = 1.0
        self.beam['ALPHA_X'] = 0.0

        self.beam['EMIT_Y'] = 0.0
        self.beam['EMIT_NY'] = 0.0   
        self.beam['BETA_Y'] = 1.0
        self.beam['ALPHA_Y'] = 0.0

        self.beam['EMIT_Z'] = 0.0   # deg MeV
        self.beam['BETA_Z'] = 1.0   # deg/MeV
        self.beam['ALPHA_Z'] = 0.0  # 1
        
        # in future, may use elegant's style to define every phase space 
        # distribution respectively
        self.beam['DISTRIBUTION_TYPE'] = 2

        # for distribution 46 with sin modulation
        self.beam['ETA']=0.0
        self.beam['LAMBDA']=0.0
        
        # turn all para values to str data type
        for key in self.beam:
            self.beam[key] = str(self.beam[key])

    def __default_lattice(self):
        # drift
        #-------------
        self.lattice['DRIFT']['L'] = 0.0
        self.lattice['DRIFT']['STEPS'] = 0
        self.lattice['DRIFT']['MAPS'] = 0
        self.lattice['DRIFT']['ORDER'] = 0
        self.lattice['DRIFT']['PIPE_RADIUS'] = 0.0

        # quad
        #-------------
        self.lattice['QUAD']['L'] = 0.0
        self.lattice['QUAD']['STEPS'] = 0
        self.lattice['QUAD']['MAPS'] = 0
        self.lattice['QUAD']['ORDER'] = 0
        self.lattice['QUAD']['K1'] = 0.0 
        self.lattice['QUAD']['GRAD'] = 0.0 
        self.lattice['QUAD']['PIPE_RADIUS'] = 0.0
        self.lattice['QUAD']['DX'] = 0.0
        self.lattice['QUAD']['DY'] = 0.0
        self.lattice['QUAD']['ROTATE_X'] = 0.0 #[rad]
        self.lattice['QUAD']['ROTATE_Y'] = 0.0
        self.lattice['QUAD']['ROTATE_Z'] = 0.0

        # BEND
        #-------------
        self.lattice['BEND']['L'] = 0.0
        self.lattice['BEND']['STEPS'] = 0
        self.lattice['BEND']['MAPS'] = 0
        self.lattice['BEND']['ORDER'] = 0
        self.lattice['BEND']['ANGLE'] = 0.0
        self.lattice['BEND']['E1'] = 0.0
        self.lattice['BEND']['E2'] = 0.0
        self.lattice['BEND']['K1'] = 0.0
        self.lattice['BEND']['HGAP'] = 0.0 #half gap for y-direction
        self.lattice['BEND']['XGAP'] = 25e-3 #half gap for x-direction
        self.lattice['BEND']['H1'] = 0.0
        self.lattice['BEND']['H2'] = 0.0
        self.lattice['BEND']['FINT'] = 0.5
        self.lattice['BEND']['DX'] = 0.0
        self.lattice['BEND']['DY'] = 0.0
        self.lattice['BEND']['ROTATE_X'] = 0.0
        self.lattice['BEND']['ROTATE_Y'] = 0.0
        self.lattice['BEND']['ROTATE_Z'] = 0.0
        self.lattice['BEND']['CSR'] = 0
        self.lattice['BEND']['CSROUT'] = 0
        self.lattice['BEND']['CSRFILE'] = 1 

        # CSR-KICK
        #-------------
        self.lattice['CSRKICK']['L'] = 0.0
        self.lattice['CSRKICK']['STEPS'] = 0
        self.lattice['CSRKICK']['MAPS'] = 0
        self.lattice['CSRKICK']['ORDER'] = 0
        self.lattice['CSRKICK']['ANGLE'] = 0.0
        self.lattice['CSRKICK']['PIPE_RADIUS'] = 0.0 #half gap
        self.lattice['CSRKICK']['CSR'] = 0
        self.lattice['CSRKICK']['CSROUT'] = 0
        self.lattice['CSRKICK']['CSRFILE'] = 1 

        # RFCW 
        #-------------
        self.lattice['RFCW']['L'] = 0.0
        self.lattice['RFCW']['STEPS'] = 0
        self.lattice['RFCW']['MAPS'] = 0
        self.lattice['RFCW']['ORDER'] = 0
        self.lattice['RFCW']['VOLT'] = 0.0
        self.lattice['RFCW']['GRADIENT'] = 0.0
        self.lattice['RFCW']['PHASE'] = 0.0
        self.lattice['RFCW']['FREQ'] = 2.856e9
        self.lattice['RFCW']['END_FOCUS'] = 1
        self.lattice['RFCW']['PIPE_RADIUS'] = 0.0
        self.lattice['RFCW']['DX'] = 0.0
        self.lattice['RFCW']['DY'] = 0.0
        self.lattice['RFCW']['ROTATE_X'] = 0.0
        self.lattice['RFCW']['ROTATE_Y'] = 0.0
        self.lattice['RFCW']['ROTATE_Z'] = 0.0
        self.lattice['RFCW']['DVV'] = 0.0
        self.lattice['RFCW']['DPHI'] = 0.0

        self.lattice['RFCW']['ZWAKE'] = 0
        self.lattice['RFCW']['TRWAKE'] = 0
        self.lattice['RFCW']['WAKEFILE_ID'] = None
        self.lattice['RFCW']['A'] = 6.6e-3
        self.lattice['RFCW']['G'] = 14.995e-3
        self.lattice['RFCW']['CELL_LEN'] = 17.495e-3
        self.lattice['RFCW']['AC_MODE'] = 0
        self.lattice['RFCW']['WAKEOUT'] = 0
        self.lattice['RFCW']['WAKEFILE'] = 0
        self.lattice['RFCW']['WAKETYPE'] = 'SLAC32PI'
        self.lattice['RFCW']['FACTOR'] = 1.0

        # WAKEON
        # -----------
        self.lattice['WAKEON']['WAKEFILE_ID'] = None
        self.lattice['WAKEON']['ZWAKE'] = 0
        self.lattice['WAKEON']['TRWAKE'] = 0
        self.lattice['WAKEON']['A'] = 6.6e-3
        self.lattice['WAKEON']['G'] = 14.995e-3
        self.lattice['WAKEON']['CELL_LEN'] = 17.495e-3
        self.lattice['WAKEON']['WAKEOUT'] = 0
        self.lattice['WAKEON']['WAKEFILE'] = 0
        self.lattice['WAKEON']['WAKETYPE'] = 'SLAC32PI'
        self.lattice['WAKEON']['FACTOR'] = 1.0

        # WAKEOFF
        # -----------
        self.lattice['WAKEOFF']

        # EMATRIX
        #-------------
        self.lattice['EMATRIX']['PIPE_RADIUS'] = 0.0
        self.lattice['EMATRIX']['R11'] = 1.0
        self.lattice['EMATRIX']['R33'] = 1.0
        self.lattice['EMATRIX']['R55'] = 1.0
        self.lattice['EMATRIX']['R56'] = 0.0
        self.lattice['EMATRIX']['R65'] = 0.0
        self.lattice['EMATRIX']['R66'] = 1.0
        self.lattice['EMATRIX']['T566'] = 0.0
        self.lattice['EMATRIX']['T655'] = 0.0
        self.lattice['EMATRIX']['U5666'] = 0.0
        self.lattice['EMATRIX']['U6555'] = 0.0

        self.lattice['EMATRIX']['R15'] = 0.0
        self.lattice['EMATRIX']['R25'] = 0.0
        self.lattice['EMATRIX']['T155'] = 0.0
        self.lattice['EMATRIX']['T255'] = 0.0

        # watch
        #-------------
        self.lattice['WATCH']['FILENAME_ID'] = 9999
        self.lattice['WATCH']['SAMPLE_FREQ'] = 0
        self.lattice['WATCH']['COORD_CONV'] = 'NORMAL' 
        self.lattice['WATCH']['SLICE_INFO'] = 1  # by default add -8 element simultaneously
        self.lattice['WATCH']['COORD_INFO'] = 1  # by default add -8 element simultaneously
        self.lattice['WATCH']['SLICE_BIN'] = 0

        # moni, -12 element
        #-------------
        self.lattice['MONI']['FILENAME_ID'] = 1
        self.lattice['MONI']['DX'] = 0.0
        self.lattice['MONI']['DY'] = 0.0

        # RingRF BPM element
        self.lattice['RINGRF']['VOLT']  = 0.0     #eV
        self.lattice['RINGRF']['PHASE'] = 0.0     #deg in sin func  
        self.lattice['RINGRF']['HARM']  = 1
        self.lattice['RINGRF']['PIPE_RADIUS'] = 0.0
        self.lattice['RINGRF']['AC_MODE'] = 0
         
        # GAP BPM element for low beam energy
        self.lattice['GAP']['VOLT']  = 0.0     #eV
        self.lattice['GAP']['FREQ']  = 324e6
        self.lattice['GAP']['PHASE'] = 0.0     #deg in sin func  
        self.lattice['GAP']['PIPE_RADIUS'] = 0.0

        # 101 DTL element 
        self.lattice['DTL']['L'] = 0.0
        self.lattice['DTL']['STEPS'] = 0
        self.lattice['DTL']['MAPS'] = 0
        self.lattice['DTL']['SCALE'] = 1.0
        self.lattice['DTL']['FREQ']  = 324e6
        self.lattice['DTL']['PHASE'] = 0.0     #driven phase, deg in sin  
        self.lattice['DTL']['ID']  = 100
        self.lattice['DTL']['PIPE_RADIUS'] = 0.0        
        self.lattice['DTL']['LQ1'] = 0.0        
        self.lattice['DTL']['GRAD1'] = 0.0        
        self.lattice['DTL']['LQ2'] = 0.0        
        self.lattice['DTL']['GRAD2'] = 0.0        
        self.lattice['DTL']['DX_Q'] = 0.0
        self.lattice['DTL']['DY_Q'] = 0.0
        self.lattice['DTL']['ROTATE_X_Q'] = 0.0
        self.lattice['DTL']['ROTATE_Y_Q'] = 0.0
        self.lattice['DTL']['ROTATE_Z_Q'] = 0.0
        self.lattice['DTL']['DX_RF'] = 0.0
        self.lattice['DTL']['DY_RF'] = 0.0
        self.lattice['DTL']['ROTATE_X_RF'] = 0.0
        self.lattice['DTL']['ROTATE_Y_RF'] = 0.0
        self.lattice['DTL']['ROTATE_Z_RF'] = 0.0

        # ideal DTL element, will be replaced with (q1,d1,gap,d2,q2) 
        self.lattice['DTLCEL']['L'] = 0.0
        self.lattice['DTLCEL']['STEPS'] = 0
        self.lattice['DTLCEL']['MAPS'] = 0
        self.lattice['DTLCEL']['VOLT'] = 0.0
        self.lattice['DTLCEL']['FREQ']  = 324e6
        self.lattice['DTLCEL']['PHASE'] = 0.0     #driven phase, deg in sin  
        self.lattice['DTLCEL']['PIPE_RADIUS'] = 0.0        
        self.lattice['DTLCEL']['LQ1'] = 0.0        
        self.lattice['DTLCEL']['GRAD1'] = 0.0        
        self.lattice['DTLCEL']['LQ2'] = 0.0        
        self.lattice['DTLCEL']['GRAD2'] = 0.0        
        self.lattice['DTLCEL']['GC'] = 0.0        
        self.lattice['DTLCEL']['DX_Q'] = 0.0
        self.lattice['DTLCEL']['DY_Q'] = 0.0
        self.lattice['DTLCEL']['ROTATE_X_Q'] = 0.0
        self.lattice['DTLCEL']['ROTATE_Y_Q'] = 0.0
        self.lattice['DTLCEL']['ROTATE_Z_Q'] = 0.0

        # 104 SC element 
        self.lattice['SC']['L'] = 0.0
        self.lattice['SC']['STEPS'] = 0
        self.lattice['SC']['MAPS'] = 0
        self.lattice['SC']['SCALE'] = 1.0
        self.lattice['SC']['FREQ']  = 324e6
        self.lattice['SC']['PHASE'] = 0.0     #driven phase, deg in sin  
        self.lattice['SC']['ID']  = 100
        self.lattice['SC']['PIPE_RADIUS'] = 0.0        
        self.lattice['SC']['DX'] = 0.0
        self.lattice['SC']['DY'] = 0.0
        self.lattice['SC']['ROTATE_X'] = 0.0
        self.lattice['SC']['ROTATE_Y'] = 0.0
        self.lattice['SC']['ROTATE_Z'] = 0.0

        # 110 for user defined element: FIELDMAP
        self.lattice['FIELDMAP']['L'] = 0.0
        self.lattice['FIELDMAP']['STEPS'] = 0
        self.lattice['FIELDMAP']['MAPS'] = 0
        self.lattice['FIELDMAP']['SCALE'] = 1.0
        self.lattice['FIELDMAP']['FREQ']  = 324e6
        self.lattice['FIELDMAP']['PHASE'] = 0.0     #driven phase, deg in sin  
        self.lattice['FIELDMAP']['ID']  = 100
        self.lattice['FIELDMAP']['XRADIUS'] = 0.0        
        self.lattice['FIELDMAP']['YRADIUS'] = 0.0        
        self.lattice['FIELDMAP']['DX'] = 0.0
        self.lattice['FIELDMAP']['DY'] = 0.0
        self.lattice['FIELDMAP']['ROTATE_X'] = 0.0
        self.lattice['FIELDMAP']['ROTATE_Y'] = 0.0
        self.lattice['FIELDMAP']['ROTATE_Z'] = 0.0
        self.lattice['FIELDMAP']['DATATYPE'] = 1
        self.lattice['FIELDMAP']['COORDINATE'] = 2
        
        # -1 or -19, shift the centroid of beam to the axis origin point
        #----------------------------------------------------
        self.lattice['SHIFTCENTER']['OPTION'] = "ZDE"

        # -21 element
        #----------------------------------------------------
        self.lattice['OFFSET']['DX'] = 0.0
        self.lattice['OFFSET']['DY'] = 0.0
        self.lattice['OFFSET']['DZ'] = 0.0
        self.lattice['OFFSET']['DPX'] = 0.0
        self.lattice['OFFSET']['DPY'] = 0.0
        self.lattice['OFFSET']['DE']  = 0.0

        # -13, rectangular aperture 
        #--------------------------
        self.lattice['RCOL']['X_MAX'] =  0.04
        self.lattice['RCOL']['Y_MAX'] =  0.04
        self.lattice['RCOL']['X_MIN'] =  'None'
        self.lattice['RCOL']['Y_MIN'] =  'None'

        # -14, elliptical aperture 
        #--------------------------
        self.lattice['ECOL']['X_MAX'] = 0.04
        self.lattice['ECOL']['Y_MAX'] = 0.04
        
        # -15, LongiCol
        #--------------------------
        self.lattice['LONGICOL']['Z_MAX']     = 0.0
        self.lattice['LONGICOL']['DELTA_MAX'] = 0.0
        self.lattice['LONGICOL']['Z_MIN']     = 'None'
        self.lattice['LONGICOL']['DELTA_MIN'] = 'None'

        # space charge output
        #--------------------
        self.lattice['SCOUT']['SCOUT']=0
        self.lattice['SCOUT']['SCFILE']=0

        # scatter element
        #-----------------
        self.lattice['SCATTER']['DE']=0.0

        # -18, ROTATE element
        #---------------
        self.lattice['ROTATE']['ANGLE']=0.0

        # -55 thinlens element
        #----------------------------------------------------
        self.lattice['THINLENS']['K0'] = 0.0
        self.lattice['THINLENS']['K1'] = 0.0
        self.lattice['THINLENS']['K2'] = 0.0
        self.lattice['THINLENS']['K3'] = 0.0
        self.lattice['THINLENS']['K4'] = 0.0
        self.lattice['THINLENS']['K5'] = 0.0

        # HKICK & VKICK
        #---------------------
        self.lattice['HKICK']['KICK'] = 0.0
        self.lattice['VKICK']['KICK'] = 0.0

        #turn all lattice elem values to string data type
        for elem in self.lattice.keys():
            for key in self.lattice[elem].keys():
                self.lattice[elem][key] = str(self.lattice[elem][key])

    # update the default control, beam, lattice with read-in lte.impz
    #==============================================================================    
    def update_control(self):
        '''
        update control:dict with read in para values.
        '''
        control_sec   = self.get_control_section()
        
        control = deepcopy( self.control )
        for key in control_sec.keys():
            if key in self.control.keys():               
                # update with read in values
                control[key] = control_sec[key]
            else:
                print('Unknown control item:',key,'=',control_sec[key])    
                sys.exit()
        return control
               
    def update_beam(self):
        '''
        update beam:dict with read in para values.
        '''
        beam_sec   = self.get_beam_section()
        
        beam = deepcopy( self.beam )
        for key in beam_sec.keys():
            if key in self.beam.keys():               
                # update with read in values
                beam[key] = beam_sec[key]
            else:
                print('Unknow beam item:',key,'=',beam_sec[key])    
                sys.exit()
        return beam

    def update_trackline(self):
        '''
        update the default track_line lattice para values with lte.impz
        '''
        trackline, usedline = self.get_lattice_section() # get the tracking line
        
        # MAP ELEGANT LATTICE TO lte.impz
        trackline = self.elegant2impz_lattice(trackline)
        
        j = 0
        for elem in trackline:
            # check if the element type is in self.lattice.keys, i.e. whether in
            # dict_keys(['DRIFT', 'QUAD', 'BEND', 'RFCW', 'WATCH'])          
                         
            # update the not-yet-setting lattice element parameters with the default 
            # values.
            if elem['TYPE'] in self.lattice.keys():
                tmp = deepcopy(self.lattice[elem['TYPE']])
                
                #NAME and TYPR for element in lte.impz are not in the self.lattice[elem['TYPE']].keys()
                table =  list(self.lattice[elem['TYPE']].keys())
                table.append('NAME')
                table.append('TYPE')
                for elem_para in elem.keys():
                    if elem_para not in table:
                        print("PAY ATTENTION: unknown element parameter",elem_para,"for",elem['NAME'],":",elem['TYPE'])
                        print("PROGRAM CONTINUE!")

                    tmp[elem_para] = elem[elem_para] 
                # replace trackline
                trackline[j] = tmp
                j += 1        
            else:
                print("Unknown element type in lattice section:",elem['TYPE'])
                sys.exit()       
        # turn all elem value to string data type    
        return trackline, usedline

    def update_paras(self):  
        Scxl = const.c_light/(2*math.pi*float(self.control['FREQ_RF_SCALE'])) 
        gam0 = (float(self.control['KINETIC_ENERGY'])+float(self.beam['MASS']))/float(self.beam['MASS'])
        gambet0 = sqrt(gam0**2-1.0)
        bet0 = gambet0/gam0

        #update self
        self.Scxl = Scxl
        self.gam0 = gam0
        self.gambet0 = gambet0
        self.bet0 = bet0
        self.mass = float(self.beam['MASS'])
     
    #write in ImpactZ.in    
    #==================================================================================================== 
    def impactzin_control(self):
        '''
        ImpactZ.in's control section
        '''
        # control section
        #----------------
        control_lines = [' !=================== \n']
        control_lines.append('! control section \n')
        control_lines.append('!=================== \n')
        
        # line-1 
        control_lines.append(self.control['CORE_NUM_T'])
        control_lines.append(self.control['CORE_NUM_L'])
        control_lines.append( '\n' )
        
        # line-2
        control_lines.append( '6' )
        
        Np = int(float(self.beam['NP']))
        control_lines.append( str(Np) )
        control_lines.append( self.control['INTEGRATOR'] )
        control_lines.append( self.control['ERROR'] )
        control_lines.append( '1 \n' )
        
        # line-3
        control_lines.append( self.control['MESHX'] )
        control_lines.append( self.control['MESHY'] )
        control_lines.append( self.control['MESHZ'] )
        control_lines.append('1')
        control_lines.append(self.control['PIPE_RADIUS'])
        control_lines.append(self.control['PIPE_RADIUS'])
        control_lines.append('0.10 \n')
        
        # line-4
        control_lines.append( self.beam['DISTRIBUTION_TYPE'] )
        control_lines.append( '0 0 1 \n' )
        
        # line-5
        control_lines.append( str(Np) )
        control_lines.append( '\n' )
        
        # line-6
        # current averaged over FREQ_RF_SCALE
        current = float(self.beam['TOTAL_CHARGE'])*float(self.control['FREQ_RF_SCALE'])
        current = abs(current)  # current should be positive value even for electron beams,
                                # otherwise, negative current will turn off space charge.
        control_lines.append( str(current) )
        control_lines.append('\n')
        
        # line-7
        charge = float(self.beam['CHARGE'])
        q_m = 1/float(self.beam['MASS'])*charge  # q/m 
        control_lines.append(str(q_m))
        control_lines.append('\n')
        
        # line-8 to line-10
        # beam distribution  
        Scxl = const.c_light/(2*math.pi*float(self.control['FREQ_RF_SCALE'])) 
        gam0 = (float(self.control['KINETIC_ENERGY'])+float(self.beam['MASS']))/float(self.beam['MASS'])
        gambet0 = sqrt(gam0**2-1.0)
        bet0 = gambet0/gam0
       
        # set sigxxp, sigyyp, sigzdE by default 0
        sigxxp=0.0
        sigyyp=0.0
        sigzdE=0.0

        # twiss2sig
        # NOTE that (sigi,sigj,sigij) is different from what we know about RMS values,
        # see notes 2021-02-22
        # ---------
        # X-PX
        emitx = float(self.beam['EMIT_X'])
        emit_nx = float(self.beam['EMIT_NX']) 
        if emit_nx != 0.0:
            emitx = emit_nx/gambet0
            
        if emitx != 0.0:
            betax = float(self.beam['BETA_X'])
            alphax = float(self.beam['ALPHA_X'])
            
            # attention: this is not RMS "sigx"
            sigx  = sqrt(emitx*betax/(1+alphax**2))
            sigxp = sqrt(emitx/betax)
            sigxxp= alphax/sqrt(1+alphax**2)
            
            # replace the default values
            self.beam['SIGX'] = str(sigx)
            self.beam['SIGXP'] = str(sigxp)
        
        # Y-PY
        emity = float(self.beam['EMIT_Y'])
        emit_ny = float(self.beam['EMIT_NY']) 
        if emit_ny != 0.0:
            emity = emit_ny/gambet0
            
        if emity != 0.0:           
            betay = float(self.beam['BETA_Y'])
            alphay = float(self.beam['ALPHA_Y'])
            
            sigy   = sqrt(emity*betay/(1+alphay**2))
            sigyp  = sqrt(emity/betay)
            sigyyp = alphay/sqrt(1+alphay**2)
             
            self.beam['SIGY']  = str(sigy)
            self.beam['SIGYP'] = str(sigyp)

        # Z-dE
        # deg-MeV
        emitz = float(self.beam['EMIT_Z'])

        if emitz != 0.0:           
            betaz = float(self.beam['BETA_Z'])
            alphaz = float(self.beam['ALPHA_Z'])
            
            sigz   = sqrt(emitz*betaz/(1+alphaz**2))
            sigdE  = sqrt(emitz/betaz)
            sigzdE = alphaz/sqrt(1+alphaz**2)
            
            # From (deg, MeV) change to (m, eV)
            # phi=wt,dW=Ei-E0 [MeV] =>
            # z=-bet0 ct, dE=Ei-E0 [eV]
            sigz   = sigz/180*pi *Scxl*bet0
            sigdE  = sigdE*1e6
            sigzdE = -sigzdE  #PAY ATTENTION to the minus sign

            self.beam['SIGZ'] = str(sigz)
            self.beam['SIGE'] = str(sigdE)
 
        # change to IMPACT-Z coordinates definition
        sigX    = float(self.beam['SIGX'])/Scxl  
        sigPx   = float(self.beam['SIGXP'])*gambet0
        sigY    = float(self.beam['SIGY'])/Scxl  
        sigPy   = float(self.beam['SIGYP'])*gambet0        
        sigT    = float(self.beam['SIGZ'])/Scxl/bet0   # m2rad
        sigPt = float(self.beam['SIGE'])/float(self.beam['MASS']) #eV 2 1 
        # sigxxp, sigyyp, sigzdE, no unit para 
        sigTPt = sigzdE
        
        control_lines.append( str(sigX) )
        control_lines.append( str(sigPx) )
        control_lines.append( str(sigxxp) )
        control_lines.append( '1.0 1.0 0.0 0.0 \n' )

        control_lines.append( str(sigY) )
        control_lines.append( str(sigPy) )
        control_lines.append( str(sigyyp) )
        control_lines.append( '1.0 1.0 0.0 0.0 \n' )
        
        if self.beam['DISTRIBUTION_TYPE'] == '46':
            eta=self.beam['ETA']        #[1]
            lambda2=self.beam['LAMBDA'] #[m]
        else:
            eta='0.0'
            lambda2='0.0'
            
        control_lines.append( str(sigT)    )
        control_lines.append( str(sigPt) ) 
        control_lines.append( str(sigTPt)  ) 
        #control_lines.append( '1.0 1.0 0.0 0.0 \n' )
        control_lines.append( '1.0 1.0' )
        control_lines.append(eta)
        control_lines.append(lambda2)
        control_lines.append('\n')

        
        # line-11
        control_lines.append(str(current))
        control_lines.append( self.control['KINETIC_ENERGY'] )
        control_lines.append( self.beam['MASS'] )
        control_lines.append( self.beam['CHARGE'] )
        control_lines.append( self.control['FREQ_RF_SCALE'] )
        control_lines.append( '0.0 \n' )

        # line-12, Biaobin Li added in 03/12/2021
        if self.control['RINGSIMU'] == '1':
            simutype = 2
        else:
            simutype = 1

        Flagsc = self._get_sc_flag()
        control_lines.append( str(Flagsc) )
        control_lines.append( self.control['TURN'] )
        control_lines.append( self.control['N_TURN_OUT'] )
        control_lines.append( str(simutype) )
        control_lines.append( '\n' )

        control_lines = ' '.join(control_lines)
        return control_lines

    def impactzin_lattice(self):
        '''
        lattice section in ImpactZ.in file.        
        '''
        lte_lines = [' !=================== \n']
        lte_lines.append('! lattice lines \n')
        lte_lines.append('!=================== \n')        
               
        for elem in self.trackline:
            if elem['TYPE'] == 'DRIFT':
                if float(elem['L']) == 0:
                    # ignore 0.0 length drift
                    pass
                else:
                    map_flag = self._get_driftmap_flag(elem)
                    self._set_steps_maps_radius(elem)

                    lte_lines.append( elem['L'] )
                    lte_lines.append( elem['STEPS'] )
                    lte_lines.append( elem['MAPS'] )
                    lte_lines.append('0')
                    lte_lines.append(elem['PIPE_RADIUS'])
                    lte_lines.append(map_flag)
                    lte_lines.append('0 0 / \n')

            elif elem['TYPE'] == 'QUAD':
                map_flag = self._get_quadmap_flag(elem)
                self._set_steps_maps_radius(elem)

                if float(elem['GRAD']) == 0.0:
                    value = elem['K1']
                else:
                    value = elem['GRAD']

                lte_lines.append(elem['L'])
                lte_lines.append(elem['STEPS'])
                lte_lines.append(elem['MAPS'])
                lte_lines.append('1')
                lte_lines.append( value )
                lte_lines.append( map_flag )
                lte_lines.append(elem['PIPE_RADIUS'])
                lte_lines.append(elem['DX'])
                lte_lines.append(elem['DY'])
                lte_lines.append(elem['ROTATE_X'])
                lte_lines.append(elem['ROTATE_Y'])
                lte_lines.append(elem['ROTATE_Z'])
                lte_lines.append('/ \n')

            elif elem['TYPE'] == 'BEND':
                map_flag = self._get_bendmap_flag(elem)
                self._set_steps_maps_radius(elem)

                lte_lines.append(elem['L'])
                lte_lines.append(elem['STEPS'])
                lte_lines.append(elem['MAPS'])
                lte_lines.append('4')
                lte_lines.append(elem['ANGLE'])
                lte_lines.append(elem['K1']) 
                lte_lines.append( map_flag )
                lte_lines.append(elem['HGAP'])
                lte_lines.append(elem['E1']) 
                lte_lines.append(elem['E2']) 
                lte_lines.append(elem['H1'])
                lte_lines.append(elem['H2'])
                lte_lines.append(elem['FINT'])
                lte_lines.append(elem['DX'])
                lte_lines.append(elem['DY'])
                lte_lines.append(elem['ROTATE_X'])
                lte_lines.append(elem['ROTATE_Y'])
                lte_lines.append(elem['ROTATE_Z'])
                lte_lines.append(elem['CSROUT'])
                lte_lines.append(elem['CSRFILE'])
                lte_lines.append(elem['XGAP'])
                lte_lines.append('/ \n')
                
            elif elem['TYPE'] == 'CSRKICK':
                map_flag = self._get_bendmap_flag(elem)
                self._set_steps_maps_radius(elem)

                lte_lines.append(elem['L'])
                lte_lines.append(elem['STEPS'])
                lte_lines.append(elem['MAPS'])
                lte_lines.append('5')
                lte_lines.append(elem['ANGLE'])
                lte_lines.append('0') 
                lte_lines.append( map_flag )
                lte_lines.append(elem['PIPE_RADIUS'])
                lte_lines.append('0 0 0 0 0 0 0 0 0 0') 
                lte_lines.append(elem['CSROUT'])
                lte_lines.append(elem['CSRFILE'])
                lte_lines.append('/ \n')

            elif elem['TYPE'] == 'EMATRIX':
                #pipe_radius currently not used
                if elem['PIPE_RADIUS'] == '0.0' :
                    elem['PIPE_RADIUS'] = self.control['PIPE_RADIUS']

                lte_lines.append('0 0 0 -22')
                lte_lines.append(elem['PIPE_RADIUS'])
                lte_lines.append(elem['R11'])
                lte_lines.append(elem['R33'])
                lte_lines.append(elem['R55'])
                lte_lines.append(elem['R56'])
                lte_lines.append(elem['R65'])
                lte_lines.append(elem['R66'])
                lte_lines.append(elem['T566'])
                lte_lines.append(elem['T655'])
                lte_lines.append(elem['U5666'])
                lte_lines.append(elem['U6555'])

                lte_lines.append(elem['R15'])
                lte_lines.append(elem['R25'])
                lte_lines.append(elem['T155'])
                lte_lines.append(elem['T255'])

                lte_lines.append('/ \n')

            elif elem['TYPE'] == 'SHIFTCENTER':
                if elem['OPTION']=='ZDE':
                    lte_lines.append('0 0 0 -19')
                    lte_lines.append('/ \n')
                elif elem['OPTION']=="XY":
                    lte_lines.append('0 0 0 -1')
                    lte_lines.append('/ \n')
                else:
                    print("ERROR: Not available option for SHIFTCENTER is given:",elem['OPTION'])
                    sys.exit()
                   
            elif elem['TYPE'] == 'OFFSET':
                #dz => dphi
                dz=float(elem['DZ'])
                f0=float(self.control['FREQ_RF_SCALE'])
                dphi=2*f0*dz/const.c_light*180
                # offset refers to beam center
                dphi=-dphi
                dE=-float(elem['DE'])

                lte_lines.append('0 0 0 -21 1.01')
                lte_lines.append(elem['DX'])
                lte_lines.append(elem['DPX'])
                lte_lines.append(elem['DY'])
                lte_lines.append(elem['DPY'])
                lte_lines.append(str(dphi))
                lte_lines.append(str(dE))
                lte_lines.append('/ \n')

            elif elem['TYPE'] == 'WAKEON':
                wake_flag = self._get_wakefield_flag(elem)
                # add -41 wakefield element
                # ------------------------
                if wake_flag=='-1':
                    pass
                else:
                    if elem['WAKEFILE_ID']=='None':
                        # analytical wake 
                        if elem['WAKETYPE']=='SLAC32PI':
                            lte_lines.append('0 0 1 -41 1.0 -1')
                        elif elem['WAKETYPE']=='TESLA13G':
                            lte_lines.append('0 0 1 -41 1.0 -2')
                        elif elem['WAKETYPE']=='TESLA39G':
                            lte_lines.append('0 0 1 -41 1.0 -3')
                        else:
                            print("ERROR: wrong waketype is given:",elem['WAKETYPE'])
                            sys.exit()

                        lte_lines.append(wake_flag)
                        lte_lines.append(elem['A'])
                        lte_lines.append(elem['G'])
                        lte_lines.append(elem['CELL_LEN'])  
                        lte_lines.append(elem['WAKEOUT'])
                        lte_lines.append(elem['WAKEFILE'])
                        lte_lines.append(elem['FACTOR'])
                        lte_lines.append('/ \n')
                   
                    elif self._is_number(elem['WAKEFILE_ID']):
                        # user defined wakefile
                        lte_lines.append('0 0 1 -41 1.0')
                        lte_lines.append(elem['WAKEFILE_ID'])
                        lte_lines.append(wake_flag)
                        lte_lines.append(elem['A'])
                        lte_lines.append(elem['G'])
                        lte_lines.append(elem['CELL_LEN'])  
                        lte_lines.append(elem['WAKEOUT'])
                        lte_lines.append(elem['WAKEFILE'])
                        lte_lines.append(elem['FACTOR'])
                        lte_lines.append('/ \n')
                    else:
                        print('ERROR: WAKEFILE_ID should be a int number, refer to wakefield file, like WAKEFILE_ID=41,' \
                              'refers to rfdata41.in file.')
                        sys.exit()

            elif elem['TYPE'] == 'WAKEOFF':
                # wakefield stops at this element
                # -------------------------------
                lte_lines.append('0 0 1 -41 1.0 -1 -1 0 0 0 0 0 / \n')
 
            elif elem['TYPE'] == 'RFCW':
                map_flag = self._get_rfcwmap_flag(elem)
                wake_flag = self._get_wakefield_flag(elem)
                if float(elem['VOLT'])==0:
                    gradient = 0.0
                else:
                    gradient = float(elem['VOLT'])/float(elem['L']) #V/m
                phase = float(elem['PHASE']) - 90 #cos for impactz, elegant sin function
                
                # add -41 wakefield element
                # ------------------------
                if wake_flag=='-1':
                    # wake OFF
                    pass
                else:
                    if elem['WAKEFILE_ID']=='None':
                        # analytical wake model
                        if elem['WAKETYPE']=='SLAC32PI':
                            lte_lines.append('0 0 1 -41 1.0 -1')
                        elif elem['WAKETYPE']=='TESLA13G':
                            lte_lines.append('0 0 1 -41 1.0 -2')
                        elif elem['WAKETYPE']=='TESLA39G':
                            lte_lines.append('0 0 1 -41 1.0 -3')
                        else:
                            print("ERROR: wrong waketype is given:",elem['WAKETYPE'])
                            sys.exit()

                        lte_lines.append(wake_flag)
                        lte_lines.append(elem['A'])
                        lte_lines.append(elem['G'])
                        lte_lines.append(elem['CELL_LEN'])  
                        lte_lines.append(elem['WAKEOUT'])
                        lte_lines.append(elem['WAKEFILE'])
                        lte_lines.append(elem['FACTOR'])
                        lte_lines.append('/ \n')                                          
                    elif self._is_number(elem['WAKEFILE_ID']):
                        # read-in wakefile
                        # print('Wakefield for cavity',elem['NAME'],'is added.')  
                        # print('rfdata',elem['WAKEFILE_ID'],'.in should be given in current path.')                   
                        lte_lines.append('0 0 1 -41 1.0')
                        lte_lines.append(elem['WAKEFILE_ID'])
                        lte_lines.append(wake_flag)
                        lte_lines.append(elem['A'])
                        lte_lines.append(elem['G'])
                        lte_lines.append(elem['CELL_LEN'])  
                        lte_lines.append(elem['WAKEOUT'])
                        lte_lines.append(elem['WAKEFILE'])
                        lte_lines.append(elem['FACTOR'])
                        lte_lines.append('/ \n')
                    else:
                        print('ERROR: WAKEFILE_ID should be a int number, refer to wakefield file, like WAKEFILE_ID=41,' \
                              'refers to rfdata41.in file.')
                        sys.exit()

                self._set_steps_maps_radius(elem)
                # cavity lines
                # ------------------------
                lte_lines.append(elem['L'])
                lte_lines.append(elem['STEPS'])
                lte_lines.append(elem['MAPS'])
                lte_lines.append('103')
                lte_lines.append(str(gradient))
                lte_lines.append(elem['FREQ'])
                lte_lines.append( str(phase) )
                lte_lines.append( map_flag )
                lte_lines.append(elem['PIPE_RADIUS'] )
                lte_lines.append(elem['DX'])
                lte_lines.append(elem['DY'])
                lte_lines.append(elem['ROTATE_X'])
                lte_lines.append(elem['ROTATE_Y'])
                lte_lines.append(elem['ROTATE_Z'])
                lte_lines.append(elem['DVV'])
                lte_lines.append(elem['DPHI'])
                lte_lines.append('/ \n')
                       
                # wakefield stops at the exit of cavity
                # ------------------------
                if wake_flag=='-1':
                    pass
                else:
                    # OFF wake after RFCW element
                    lte_lines.append('0 0 1 -41 1.0 -1 -1 0 0 0 0 0 / \n')           
            
            elif elem['TYPE'] == 'WATCH':
                if elem['COORD_CONV'].upper() == 'IMPACT-Z':
                    phaseopt = 0
                elif elem['COORD_CONV'].upper() == 'IMPACT-T':
                    phaseopt = 1                    
                elif elem['COORD_CONV'].upper() == 'NORMAL':
                    phaseopt = 2                    
                elif elem['COORD_CONV'].upper() == 'ASTRA':
                    phaseopt = 3                    
                else:
                    print('Unknown coord_conv for -2 element:',elem['COORD_CONV'])
                    sys.exit()

                # if SAMPLE_FREQ not set in the lattice line, replace with the value in control section
                if elem['SAMPLE_FREQ']=='0':
                    Np = float(self.beam['NP'])
                    sample_out = float(self.control['SAMPLE_OUT'])
                    if Np <= sample_out:
                        elem['SAMPLE_FREQ'] = '1'
                    elif Np > sample_out:
                        freq = math.ceil(Np/sample_out)
                        elem['SAMPLE_FREQ'] = str(freq)
                
                if elem['COORD_INFO']=='1':
                    lte_lines.append('0 0')
                    lte_lines.append(elem['FILENAME_ID'])
                    lte_lines.append('-2')
                    lte_lines.append(str(phaseopt))
                    lte_lines.append(elem['SAMPLE_FREQ'])
                    lte_lines.append('/ \n')
                
                # whether add -8 element
                if elem['SLICE_BIN']=='0':
                    elem['SLICE_BIN']=self.control['SLICE_BIN']

                if elem['SLICE_INFO'] == '0':
                    pass
                
                elif elem['SLICE_INFO'] == '1':
                    lte_lines.append('0 0')
                    lte_lines.append( str(int(elem['FILENAME_ID']) +10000) )
                    lte_lines.append('-8')
                    lte_lines.append(elem['SLICE_BIN'])
                    lte_lines.append('/ \n')    
                else:
                    print('Unknown flag for SLICE_INFO, it should be 0 or 1.')
                    sys.exit()

            elif elem["TYPE"] == "MONI":
                lte_lines.append('0 0')
                lte_lines.append(elem['FILENAME_ID'])
                lte_lines.append('-12')
                lte_lines.append(elem["DX"])
                lte_lines.append(elem["DY"])
                lte_lines.append('/ \n')

            elif elem['TYPE'] == 'RINGRF':
                if elem['PIPE_RADIUS'] == '0.0' :
                    elem['PIPE_RADIUS'] = self.control['PIPE_RADIUS'] 

                if elem['AC_MODE'] == '1':
                    mode = 2
                else:
                    mode = 1
                 
                phase = float(elem['PHASE']) - 90

                lte_lines.append('0 0 0 -42')
                lte_lines.append(elem['PIPE_RADIUS'])
                lte_lines.append(elem['VOLT'])
                lte_lines.append(elem['HARM'])
                lte_lines.append(str(phase))
                lte_lines.append(str(mode))
                lte_lines.append('/ \n')

            elif elem['TYPE'] == 'GAP':
                if elem['PIPE_RADIUS'] == '0.0' :
                    elem['PIPE_RADIUS'] = self.control['PIPE_RADIUS'] 

                phase = float(elem['PHASE']) - 90

                lte_lines.append('0 0 0 -43')
                lte_lines.append(elem['PIPE_RADIUS'])
                lte_lines.append(elem['VOLT'])
                lte_lines.append(elem['FREQ'])
                lte_lines.append(str(phase))
                lte_lines.append('/ \n')

            elif elem['TYPE'] == 'SC':
                self._set_steps_maps_radius(elem)
                phase = float(elem['PHASE']) - 90

                lte_lines.append(elem['L'])
                lte_lines.append(elem['STEPS'])
                lte_lines.append(elem['MAPS'])
                lte_lines.append('104')
                lte_lines.append(elem['SCALE'])
                lte_lines.append(elem['FREQ'])
                lte_lines.append( str(phase) )
                lte_lines.append( elem['ID'] )
                lte_lines.append(elem['PIPE_RADIUS'] )
                lte_lines.append(elem['DX'])
                lte_lines.append(elem['DY'])
                lte_lines.append(elem['ROTATE_X'])
                lte_lines.append(elem['ROTATE_Y'])
                lte_lines.append(elem['ROTATE_Z'])
                lte_lines.append('/ \n')

            elif elem['TYPE'] == 'DTL':
                self._set_steps_maps_radius(elem)
                phase = float(elem['PHASE']) - 90

                lte_lines.append(elem['L'])
                lte_lines.append(elem['STEPS'])
                lte_lines.append(elem['MAPS'])
                lte_lines.append('101')
                lte_lines.append(elem['SCALE'])
                lte_lines.append(elem['FREQ'])
                lte_lines.append( str(phase) )
                lte_lines.append( elem['ID'] )
                lte_lines.append(elem['PIPE_RADIUS'] )
              
                lte_lines.append( elem['LQ1'] )
                lte_lines.append( elem['GRAD1'] )
                lte_lines.append( elem['LQ2'] )
                lte_lines.append( elem['GRAD2'] )

                lte_lines.append(elem['DX_Q'])
                lte_lines.append(elem['DY_Q'])
                lte_lines.append(elem['ROTATE_X_Q'])
                lte_lines.append(elem['ROTATE_Y_Q'])
                lte_lines.append(elem['ROTATE_Z_Q'])
                lte_lines.append(elem['DX_RF'])
                lte_lines.append(elem['DY_RF'])
                lte_lines.append(elem['ROTATE_X_RF'])
                lte_lines.append(elem['ROTATE_Y_RF'])
                lte_lines.append(elem['ROTATE_Z_RF'])
                lte_lines.append('/ \n')

            elif elem['TYPE'] == 'DTLCEL':
                self._set_steps_maps_radius(elem)
                phase = float(elem['PHASE']) - 90

                L=float(elem['L'])
                Lq1=float(elem['LQ1'])
                Lq2=float(elem['LQ2'])
                gc =float(elem['GC'])

                d1 = 0.5*L-gc-Lq1
                d2 = 0.5*L+gc-Lq2
                
                # q1
                #----------- 
                steps = math.ceil(Lq1/L*float(elem['STEPS']))
                maps  = math.ceil(Lq1/L*float(elem['MAPS']))
                
                lte_lines.append(elem['LQ1'])
                lte_lines.append(str(steps))
                lte_lines.append(str(maps))
                lte_lines.append('1')
                lte_lines.append(elem['GRAD1'])
                lte_lines.append('-16' )  # using grad, and is nonlinear map
                lte_lines.append(elem['PIPE_RADIUS'])
                lte_lines.append(elem['DX_Q'])
                lte_lines.append(elem['DY_Q'])
                lte_lines.append(elem['ROTATE_X_Q'])
                lte_lines.append(elem['ROTATE_Y_Q'])
                lte_lines.append(elem['ROTATE_Z_Q'])
                lte_lines.append('/ \n')

                # d1, using nonlinear drift
                #--------------------------
                steps = math.ceil(d1/L*float(elem['STEPS']))
                maps  = math.ceil(d1/L*float(elem['MAPS']))

                lte_lines.append( str(d1) )
                lte_lines.append( str(steps) )
                lte_lines.append( str(maps) )
                lte_lines.append('0')
                lte_lines.append(elem['PIPE_RADIUS'])
                lte_lines.append('/ \n')

                # gap
                #-----------
                lte_lines.append('0 0 0 -43')
                lte_lines.append(elem['PIPE_RADIUS'])
                lte_lines.append(elem['VOLT'])
                lte_lines.append(elem['FREQ'])
                lte_lines.append(str(phase))
                lte_lines.append('/ \n') 

                # d2, using nonlinear drift
                #--------------------------
                steps = math.ceil(d2/L*float(elem['STEPS']))
                maps  = math.ceil(d2/L*float(elem['MAPS']))

                lte_lines.append( str(d2) )
                lte_lines.append( str(steps) )
                lte_lines.append( str(maps) )
                lte_lines.append('0')
                lte_lines.append(elem['PIPE_RADIUS'])
                lte_lines.append('/ \n')

                # q2
                #----------- 
                steps = math.ceil(Lq2/L*float(elem['STEPS']))
                maps  = math.ceil(Lq2/L*float(elem['MAPS']))
                
                lte_lines.append(elem['LQ2'])
                lte_lines.append(str(steps))
                lte_lines.append(str(maps))
                lte_lines.append('1')
                lte_lines.append(elem['GRAD2'])
                lte_lines.append('-16' )  # using grad, and is nonlinear map
                lte_lines.append(elem['PIPE_RADIUS'])
                lte_lines.append(elem['DX_Q'])
                lte_lines.append(elem['DY_Q'])
                lte_lines.append(elem['ROTATE_X_Q'])
                lte_lines.append(elem['ROTATE_Y_Q'])
                lte_lines.append(elem['ROTATE_Z_Q'])
                lte_lines.append('/ \n')

            elif elem['TYPE'] == 'FIELDMAP':
                self._set_steps_maps_radius(elem)
                phase = float(elem['PHASE']) - 90

                lte_lines.append(elem['L'])
                lte_lines.append(elem['STEPS'])
                lte_lines.append(elem['MAPS'])
                lte_lines.append('110')
                lte_lines.append(elem['SCALE'])
                lte_lines.append(elem['FREQ'])
                lte_lines.append( str(phase) )
                lte_lines.append( elem['ID'] )
                lte_lines.append(elem['XRADIUS'] )
                lte_lines.append(elem['YRADIUS'] )
                lte_lines.append(elem['DX'])
                lte_lines.append(elem['DY'])
                lte_lines.append(elem['ROTATE_X'])
                lte_lines.append(elem['ROTATE_Y'])
                lte_lines.append(elem['ROTATE_Z'])
                lte_lines.append(elem['DATATYPE'])
                lte_lines.append(elem['COORDINATE'])
                lte_lines.append('/ \n')

            elif elem['TYPE'] == 'SCOUT':
                lte_lines.append('1.0d-6 1 1 0 100 -1')
                lte_lines.append(elem['SCOUT'])
                lte_lines.append(elem['SCFILE'])
                lte_lines.append('/ \n')

            elif elem['TYPE'] == 'SCATTER':
                lte_lines.append('0 0 0 -20 0.014')
                lte_lines.append(elem['DE'])
                lte_lines.append('/ \n')

            elif elem['TYPE']=='ROTATE':
                lte_lines.append('0 0 0 -18 0.014')
                lte_lines.append(elem['ANGLE'])
                lte_lines.append('/ \n')

            elif elem['TYPE']=='RCOL':
                if elem['X_MIN']=='None':
                    elem['X_MIN']=''.join(['-',elem['X_MAX']])
                if elem['Y_MIN']=='None':
                    elem['Y_MIN']=''.join(['-',elem['Y_MAX']])
                lte_lines.append('0 0 0 -13 0.014')
                lte_lines.append(elem['X_MIN'])
                lte_lines.append(elem['X_MAX'])
                lte_lines.append(elem['Y_MIN'])
                lte_lines.append(elem['Y_MAX'])
                lte_lines.append('/ \n')

            elif elem['TYPE']=='ECOL':
                lte_lines.append('0 0 0 -14 0.014')
                lte_lines.append(elem['X_MAX'])
                lte_lines.append(elem['Y_MAX'])
                lte_lines.append('/ \n')

            elif elem['TYPE']=='LONGICOL':
                if float(elem['Z_MAX'])==0:
                    elem['Z_MAX']='1e5'
                if float(elem['DELTA_MAX'])==0:
                    elem['DELTA_MAX']='1e5'

                if elem['Z_MIN']=='None':
                    elem['Z_MIN']=''.join(['-',elem['Z_MAX']])
                if elem['DELTA_MIN']=='None':
                    elem['DELTA_MIN']=''.join(['-',elem['DELTA_MAX']])
                lte_lines.append('0 0 0 -15 0.014')
                lte_lines.append(elem['Z_MIN'])
                lte_lines.append(elem['Z_MAX'])
                lte_lines.append(elem['DELTA_MIN'])
                lte_lines.append(elem['DELTA_MAX'])
                lte_lines.append('/ \n')

            elif elem['TYPE']=='THINLENS':
                lte_lines.append('0 0 0 -55 0.014')
                lte_lines.append(elem['K0'])
                lte_lines.append(elem['K1'])
                lte_lines.append(elem['K2'])
                lte_lines.append(elem['K3'])
                lte_lines.append(elem['K4'])
                lte_lines.append(elem['K5'])
                lte_lines.append('/ \n')

            elif elem["TYPE"] == "HKICK":
                lte_lines.append('0 0 0 -55 0.014')
                lte_lines.append(elem['KICK'])
                lte_lines.append('0 0 0 0 0 / \n')

            elif elem["TYPE"] == "VKICK":
                lte_lines.append('0 0 0 -18 0.014')
                lte_lines.append(str(pi/2))
                lte_lines.append('/ \n')

                lte_lines.append('0 0 0 -55 0.014')
                lte_lines.append(elem['KICK'])
                lte_lines.append('0 0 0 0 0 / \n')

                lte_lines.append('0 0 0 -18 0.014')
                lte_lines.append(str(-pi/2))
                lte_lines.append('/ \n')
              
            else:
                print("NOT AVAILABLE ELEMENT TYPE:",elem['TYPE'])
                sys.exit()
       
        lte_lines = ' '.join(lte_lines)
        return lte_lines    
   
    # sub-funcs 
    #===============================================================================
   
    def get_control_section(self):
        '''
        get control section.
        '''
        lines = deepcopy(self.lines)
        
        j=0
        for line in lines:
           lines[j]=line.replace(' ','') 
           j=j+1
           
        # get control section
        pattern1 = re.compile(r'^&control$',re.I)  #not case sensitive
        pattern2 = re.compile(r'^&end$',re.I)
        
        j1,j2 = self._get_index(pattern1, pattern2, lines)
        
        control_lines = lines[j1+1:j2]  # ignored 1st and last element, i.e. &control and &end
        
        control = {}
        for line in control_lines:
            tmp = re.split(';|,',line) # , and ; are both supported
        
            # remove white space
            while '' in tmp:
                tmp.remove('')
            
            for j in tmp:
                tmp2 = j.split('=')
                
                name = tmp2[0].upper()
                value = tmp2[1]
                
                # in case math expression, such as:
                #     FREQ_RF_SCALE = 2.998e8/2/pi
                try:
                    eval(value)
                except:
                    pass
                else:
                    value = eval(value.lower())
                    value = str(value)  #back to string
                
                control[name] = value                
                
        return control         
        
    def get_beam_section(self):
        '''
        get beam section.
        '''
        #use deepcopy to keep self.lines unchanged, as white space should be kept in
        #rpn expressions for lattice section  
        lines = deepcopy(self.lines)
        j=0
        for line in lines:
            lines[j] = line.replace(' ','')
            j=j+1
            
        # get lattice section
        pattern1 = re.compile(r'^&beam$',re.I)  #not case sensitive
        pattern2 = re.compile(r'^&end$',re.I)
        
        j1,j2 = self._get_index(pattern1, pattern2, lines)
        
        beam_lines = lines[j1+1:j2]  # ignored 1st and last element, i.e. &beam and &end

        beam = {}
        for line in beam_lines:
            tmp = re.split(';|,',line) # , and ; are both supported
        
            # remove white space
            while '' in tmp:
                tmp.remove('')
            
            for j in tmp:
                tmp2 = j.split('=')
                
                name = tmp2[0].upper()
                value = tmp2[1]
                
                # in case math expression, such as:
                #    total_charge=20*5e-3/2.998e8
                try:
                    eval(value)
                except:
                    pass
                else:
                    value = eval(value.lower())
                    value = str(value)  #back to string
                
                beam[name] = value
                                         
        return beam  

    def get_lattice_section(self):
        '''
        get lattice section. 

        '''   
        lines = self.lines
        
        # get lattice section
        pattern1 = re.compile(r'^&lattice$',re.I)  #not case sensitive
        pattern2 = re.compile(r'^&end$',re.I)
        
        j1,j2 = self._get_index(pattern1, pattern2, lines)
        
        lattice = lines[j1+1:j2]  # ignored 1st and last element, i.e. &lattice and &end
        
        # get the tracked line
        trackline, usedline = self.get_trackline(lattice)

        return trackline, usedline
               
    def _get_index(self, pattern1, pattern2, lines):
        '''
        get start and finished index of (&control,...,&end)    
        '''       
        j1 = int(1e6)
        j2 = int(1e6)
        cnt = 0
        for line in lines:
            if re.match(pattern1, line):  
                j1 = cnt
                break
            cnt += 1
                    
        cnt = j1
        for j in range(j1, len(lines)):
            if re.match(pattern2,lines[j]):
                j2 = cnt  
                break                  
            cnt += 1    
            
        if j1 == int(1e6):
            print(pattern1,"not found. Input file is wrong.")
            sys.exit()
        elif j2==int(1e6):
            print(pattern2,"not found. Input file is wrong.")
            sys.exit()
                      
        return j1,j2
    
    def _get_driftmap_flag(self, elem :dict):
        if elem['ORDER'] == '0':
            # set by DEFAULT_ORDER
            elem['ORDER'] = self.control['DEFAULT_ORDER']

        if elem['ORDER'] == '1':     #linear map
            flag = '-1'
        elif elem['ORDER'] == '2':    #real nonlinear map
            flag = '1'      
        else:
            print('Element',elem['NAME'],':unknown order is given.')
            sys.exit()

        return flag
    
    def _get_quadmap_flag(self, elem :dict):
        if elem['ORDER'] == '0':
            # set by DEFAULT_ORDER
            elem['ORDER'] = self.control['DEFAULT_ORDER']
        if float(elem['GRAD']) == 0.0:
            # value is K1
            if elem['ORDER'] == '1':     #linear map
                flag = '-5'
            elif elem['ORDER'] == '2':    #nonlinear map
                flag = '-15'
            elif elem['ORDER'] == '5':    #map for undulator
                flag = '-25'
            else:
                print('Element',elem['NAME'],':unknown ORDER for QUAD is given.')
                sys.exit()
        else:
            # value is gradient
            if elem['ORDER'] == '1':     #linear map
                flag = '-6'
            elif elem['ORDER'] == '2':    #nonlinear map
                flag = '-16'
            elif elem['ORDER'] == '5':    #map for undulator
                flag = '-26'
            else:
                print('Element',elem['NAME'],':unknown ORDER for QUAD is given.')
                sys.exit()
        return flag
    
    def _get_bendmap_flag(self, elem :dict):
        if elem['ORDER'] == '0':
            # set by DEFAULT_ORDER
            elem['ORDER'] = self.control['DEFAULT_ORDER']    
        if elem['CSR'] == '0':
            elem['CSR'] = self.control['CSR']

        if   elem['ORDER']=='1' and elem['CSR']=='0':              
            flag = '25'
        elif elem['ORDER']=='1' and elem['CSR']=='1':     
            flag = '75'
        elif elem['ORDER']=='2' and elem['CSR']=='0':     
            flag = '150'
        elif elem['ORDER']=='2' and elem['CSR']=='1':     
            flag = '250'
        else:
            print('Element',elem['NAME'],':unknown ORDER or CSR flag are given, ORDER=1/2, CSR=0/1.')
            sys.exit()
        return flag
    
    def _get_rfcwmap_flag(self, elem :dict):
        if elem['ORDER'] == '0':
            # set by DEFAULT_ORDER
            elem['ORDER'] = self.control['DEFAULT_ORDER']    

        if elem['ORDER'] == '1':     #linear map
            if elem['END_FOCUS']=='1':
                flag='-0.5'
            elif elem['END_FOCUS']=='0':
                flag='-0.25'
            else:
                print('Element',elem['NAME'],':unknown END_FOCUS flag is given.')
                sys.exit()
 
        elif elem['ORDER'] == '2':    #nonlinear map
            if elem['END_FOCUS']=='1':
                flag='-1.0'
            elif elem['END_FOCUS']=='0':
                flag='-0.75'
            else:
                print('Element',elem['NAME'],':unknown END_FOCUS flag is given.')
                sys.exit()
        else:
            print('Element',elem['NAME'],':unknown ORDER is given.')
            sys.exit()
       
        #ac_mode has higher priority
        if elem['AC_MODE']=='1':
            if elem['END_FOCUS']=='1':    #nonlinear map
                flag='-0.55'
            elif elem['END_FOCUS']=='0':   #nonlinear map
                flag='-0.65'
            else:
                print('Element',elem['NAME'],':unknown END_FOCUS flag is given.')
                sys.exit()
            
        return flag   

    def _get_sc_flag(self):
        '''
        Set Flagsc value based on TSC and LSC values in lte.impz
        '''
        if self.control['FLAGLSC'].upper()=='3D':
            if   self.control['TSC']=='0' and self.control['LSC']=='0':
                if self.control['ZWAKE']=='1' or self.control['TRWAKE']=='1' or self.control['CSR']=='1':
                    #SC OFF, however, wake or csr ON case
                    Flagsc=4
                else:
                    Flagsc=0
            elif self.control['TSC']=='0' and self.control['LSC']=='1':
                Flagsc=1
            elif self.control['TSC']=='1' and self.control['LSC']=='0':
                Flagsc=2
            elif self.control['TSC']=='1' and self.control['LSC']=='1':
                Flagsc=3
            else:
                print("Error: TSC and LSC value should be integer 0 or 1.")
                sys.exit()
        elif self.control['FLAGLSC'].upper()=='1D':
            if self.control['LSC']=='0':
                if self.control['ZWAKE']=='1' or self.control['TRWAKE']=='1' or self.control['CSR']=='1':
                    #SC OFF, however, wake or csr ON case
                    Flagsc=5
                else:
                    Flagsc=0
            elif self.control['LSC']=='1':
                Flagsc=6
        

        return Flagsc

    def _get_wakefield_flag(self, elem :dict):
        '''
        get -41 wakefield element flag.
        '''
        # global settings from control section
        if elem['ZWAKE']=='0':
            elem['ZWAKE'] = self.control['ZWAKE']
        if elem['TRWAKE']=='0':
            elem['TRWAKE'] = self.control['TRWAKE']

        # get flag
        if   elem['ZWAKE']=='0' and elem['TRWAKE']=='0':
            # turn-off wakefield
            flag = '-1'
        elif elem['ZWAKE']=='1' and elem['TRWAKE']=='0':
            flag = '5'
        elif elem['ZWAKE']=='0' and elem['TRWAKE']=='1':
            flag = '15'
        elif elem['ZWAKE']=='1' and elem['TRWAKE']=='1':
            flag = '25'
        else:
            print('Unknown flag for ZWAKE or TRWAKE, value should be 0 or 1.')
            sys.exit()
        return flag
    
    def _set_steps_maps_radius(self, elem :dict):
        '''
        set default maps and steps based on control settings
        control steps is nseg/m
        element steps is nseg
        element steps and maps has higher priority.
        '''
        steps = float(self.control['STEPS'])
        maps  = float(self.control['MAPS'])
        length = float(elem['L'])
        
        if elem['STEPS'] == '0':
            elem['STEPS'] = str(math.ceil(steps*length))
        else:
            steps = float(elem['STEPS'])

        if elem['MAPS']=='0':
            elem['MAPS'] = str(math.ceil(maps*length))
        else:
            maps = elem['MAPS']
       
        # in case element length is 0.0, steps cannot be 0
        # if control.steps=0, then elem['STEPS']='1'
        if length==0.0 or steps==0.0:
            elem['STEPS'] = '1'

        if length==0.0 or maps==0.0:
            elem['MAPS'] = '1'
        
        if elem['TYPE'] == 'BEND':
            if elem['HGAP'] == '0.0':
                elem['HGAP'] = self.control['PIPE_RADIUS']
        else:  
            if elem['PIPE_RADIUS'] == '0.0':
                elem['PIPE_RADIUS'] = self.control['PIPE_RADIUS']
    
    def elegant2impz_lattice(self,trackline):
        '''
        For elegant lattice, there are different definition or notations for
        accelerator elements, map these elements to lte.impz CONVENTION

        Returns
        -------
        trackline

        '''
        # map ELEGANT elements
        j=0  
        for elem in trackline:
            # map sbend to bend
            if elem['TYPE'] in ['CSRCSBEN', 'CSRCSBEND','SBEN','CSBEND']:
                elem['TYPE'] = 'BEND'

            # map quadrupole to quad 
            elif elem['TYPE'] in ['QUADRUPOLE']:
                elem['TYPE'] = 'QUAD'

            # map monitor, hkicker, vkicker, sextupole to drift, temporary 
            # length is kept
            elif elem['TYPE'] in ['DRIF', 'CSRDRIFT', \
                                'MARK', \
                                'MALIGN','CLEAN', \
                                'SEXTUPOLE', \
                                'CHARGE']:
                elem['TYPE'] = 'DRIFT'
            
            trackline[j]['TYPE']=elem['TYPE']
            j=j+1
        return trackline
        
if __name__=='__main__':
        
    # usage examples
    # =====================
    file_name = './impz/lte.impz'   
    line_name = 'flag4line'
    
    lte = impactz_parser(file_name,line_name)

    # lte.write_impactzin()   
    lte.dump2json()
    lte.json2impzin()     
    
