#Biaobin, 2024-03-26
#broadcast the simulation results to epics channel

import json
import numpy as np
import matplotlib as mpl
import matplotlib.pyplot as plt
import epics 
import sdds
from half_linac import setup as st

class vm_results:
    def __init__(self):
        pass
    
    
    def broadcast_flag(self):
        # get watch channel
        with open("./halflinac.json","r") as f:
            lte = json.load(f)
        lattice = lte["lattice"]
        
        for key in lattice:
            if lattice[key]["TYPE"] in ["WATCH"] and lattice[key]["COORD_INFO"]=="1":
                channel = lattice[key]["AP"]
                file_id =  lattice[key]["FILENAME_ID"]
                
                tmp = self._get_watch_image(file_id)
                
                print("broadcasting flag image data ...")
                epics.caput(channel, tmp)
                print("broadcasting flag image data finished.")
    
    def broadcast_bpm(self):
        moni = np.loadtxt("./impz/fort.33",skiprows=1)
       
        moniid = moni[:,0]   
        cx     = moni[:,2] *1e3 #[mm]
        cy     = moni[:,3] *1e3 #[mm]

        k = 0
        pvl=[]
        val=[]
        for j in moniid:
            if j < 10:
                pvx = "IRFEL:BD:BPM0"+str(int(j))+":X"
                pvy = "IRFEL:BD:BPM0"+str(int(j))+":Y"
            else:
                pvx = "IRFEL:BD:BPM"+str(int(j))+":X"
                pvy = "IRFEL:BD:BPM"+str(int(j))+":Y"
            
            pvl.append(pvx)
            pvl.append(pvy)
            val.append(cx[k])
            val.append(cy[k])
            k=k+1    
        
        # broadcast BPM X, Y
        print("broadcasting BPM data ...")
        epics.caput_many(pvl,val)
        print("broadcasting BPM data finished.")
        
    # sub-funcs
    def _get_watch_image(self, file_id):
        #pha = np.loadtxt("./impz/fort."+file_id, skiprows=1)
        tmp = sdds.SDDS(0)
        
        tmp.load('./elegant/'+file_id+".out")
        colname = tmp.columnName
        data    = tmp.columnData

        out = {}
        cnt=0
        for j in colname:
            out[j]=data[cnt][0]
            cnt = cnt+1

        # the real size of flag
        x1=-0.5*st.flag_pixel_machine[0]*st.flag_pixel_width*1e-3 #[m]
        x2= 0.5*st.flag_pixel_machine[0]*st.flag_pixel_width*1e-3 #[m]
        y1=-0.5*st.flag_pixel_machine[1]*st.flag_pixel_width*1e-3 #[m]
        y2= 0.5*st.flag_pixel_machine[1]*st.flag_pixel_width*1e-3 #[m]

        pixel_vm = st.flag_pixel_vm
        h = plt.hist2d(pha[:,0],pha[:,2], bins=[pixel_vm[0],pixel_vm[1]],cmap=plt.cm.jet,range=[[x1,x2],[y1,y2]])
        tmp = np.reshape(h[0].transpose(),(np.size(h[0]),))  #change to 1d array
        
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
        
    
    
if __name__=="__main__":
    
    vmrt = vm_results()
    
    # vmrt.broadcast_flag()
    vmrt.broadcast_bpm()
    
        
