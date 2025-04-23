from pathlib import Path

#class myconst:
#    def __init__(self):
#        self.rootpath = str(Path(__file__).resolve().parent)
#
#
#if __name__ == '__main__':
#    tmp = myconst()
#    print(tmp.rootpath)

rootpath = str(Path(__file__).resolve().parent)

# pixel information for VM and real facility
flag_pixel_vm = [360,270]   #[360,270]如果与实际一样的话，caget速度太慢，约为1.几秒的样子。
flag_pixel_machine = [1440,1080]
flag_pixel_width = 0.02 #[mm]

# consts
c_light = 2.99792458e8
electron_mass = 0.51099895000e6  #eV
proton_mass = 938.27208816e6     #eV
