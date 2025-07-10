# half_linac 上层物理应用软件



# 每周开发进度

##### 2025-7-10-Zhanghaoran

- 开展在线优化功能开发(half_linac\src\optimization)

  实现了非梯度依赖的基于方向集的优化算法，robust conjugate direction search (RCDS)  ，并通过Rosenbrock函数进行了测试

##### 2025-6-26-Zhanghaoran

- `orbit corrrect`

  添加svd方法求解逆矩阵

##### 2025-6-19-Zhanghaoran

- `orbit corrrect`

  完善global correction功能

##### 2025-6-12-Zhanghaoran

- `orbit corrrect`

  添加任意定义目标轨道功能

##### 2025-5-29-Zhanghaoran

- `beam monitor`

  优化束斑分布拟合，提升拟合准确度
  
- `Virtual Machine`

  添加关闭功能

##### 2025-5-22-Zhanghaoran

- `Virtual Machine`

  添加Q铁的强度jitter功能

###### 2025-5-15-Zhanghaoran

- `BBA`

  BBA2经debug已可正确运行

- `orbit correct`

  添加了任意自选需校正的BPM功能，并测试了其在one-by-one校正方法下的正确性

###### 2025-5-8-Zhanghaoran

- 给orbit corrrect添加独立gui，可自定义相关参数（如采样间隔，校正精度）；并增加校正停止和归零功能按钮。

###### 2025-4-29-Zhanghaoran

- 重新调整了launcher的gui布局，并将与VM相关的功能（start VM;start IOC; add error）单独放在一个用户界面，且静态误差可自定义。

###### 2025-4-24-Zhanghaoran

- 鉴于BPM数量多，`orbit_display`界面增加了选择显示一定范围BPMs的选项，并添加按钮可查看所有BPM的实时读数。

###### 2025-4-17-Zhanghaoran

- 发射度测量界面增加了`simply VM`按钮，可根据所选取的Q铁和FLAG简化lattice，加速虚拟加速器运行速度，而`full VM`按钮可将lattice恢复到原始状态

###### 2024-3~2025-4 Zhangshancai

- 内容较多，待补充。。。

###### 2024-3-11-Libiaobin

- 以 `lattice_ini.lte` =>` lattice.json` 作为输入文件，自动生成 quad.template, bpm.template 等 IOC 文件。后面如果需要修改元件名称和PV命名规则，直接修改`lattice_ini.lte` 文件即可。见`gen_substitution_file()`
- 以 `lattice.json`文件作为中间媒介：
  - 当epics修改了 quad 的K1值时，IOC监测到PV值发生了改变，将自动更新 `lattice.json` 文件。当前只添加了 QUAD。
  - Elegant 每次循环运行时，都会重新读取 `lattice.json` 文件，生成 `lattice.lte`，然后运行。注意，lattice_ini.lte 文件没有改变。



# 程序功能总览

- ## virtual machine

  - 第一次运行，根据lattice_ini.lte和one_ini.ele 生成 json 文件，之后根据 json 文件生成lattice.lte和one.ele
  - 运行elegant one.ele 并向IOC发布相关pv value
  - 进入监视状态，一旦json文件发生变化，运行elegant one.ele 并向IOC发布相关pv值



- ## softIOC

  - 第一次运行，将生成 db 文件夹下的 substitutions 文件

  - 建立起IOC，将根据 json 文件初始化 epics 中所有四极铁等元件的初始pv值

  - 利用onChange函数监听元件的pv值，一旦改变，更新 json 文件

    

- ## apps

  - launcher 显示主界面，各按钮调用其他调束功能，单独开一个线程进行。
  - 各种上层物理调束程序，打开后分别会单独开一个线程进行。
    - bba
    - beam_monitor
    - emit_measure
    - orbit_correct
    - orbit_display

​					  。。。。。。



# Quick start

## 1. Install some packages in Linux system

### a. Linux environment

#### windows下如何实现linux环境

在Windows操作系统下创建和运行Linux环境通常有以下几种方法，推荐使用WSL：

##### 1. 使用Windows子系统（Windows Subsystem for Linux, WSL）

WSL 是微软提供的一个功能，允许你在Windows 10和Windows 11上直接运行Linux发行版，如Ubuntu、Debian等。

- 在管理员权限下进入Powershell

  ```powershell
  wsl --install
  ```

- wsl成功启用后安装ubantu

  ```powershell
  wsl.exe --install Ubantu
  ```

##### 2. 使用虚拟机软件（如VirtualBox或VMware）

通过虚拟机软件，你可以在Windows上运行一个完整的虚拟机，里面可以安装Linux操作系统。



#### Tips: 推荐使用shell命令行工具on-my-zsh！

- 安装zsh

  ```bash
  sudo apt-get install zsh
  sudo chsh -s $(which zsh)
  sudo reboot
  ```

- 安装oh-my-zsh

  ```bash
  sh -c "$(curl -fsSL https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/master/tools/install.sh)"
  ```

  

### b. epics

参考官方网站：https://docs.epics-controls.org/en/latest/getting-started/installation-linux.html

#### install

The recommended way to start working with EPICS is to download one of the release packages. The released versions of EPICS have been fully tested to work as documented. Choose the release that you want and download:

```zsh
mkdir $HOME/EPICS
cd $HOME/EPICS
wget https://epics-controls.org/download/base/base-7.0.8.1.tar.gz
tar -xvf base-7.0.8.1.tar.gz
cd base-7.0.8.1
make
```

After compiling you should put the path into `$HOME/.profile` or into `$HOME/.bashrc` or into  `$HOME/.zshrc` by adding the following to either one of those files:

```zsh
export EPICS_BASE=${HOME}/EPICS/epics-base
export EPICS_HOST_ARCH=$(${EPICS_BASE}/startup/EpicsHostArch)
export PATH=${EPICS_BASE}/bin/${EPICS_HOST_ARCH}:${PATH}
```

EpicsHostArch is a program provided by EPICS that returns the architecture of your system. Thus the code above should be fine for every architecture.

#### test

Now log out and log in again, so that your new path is set correctly. Alternatively, you can execute the three lines above beginning with export directly from the terminal.

Run `softIoc` and, if everything is ok, you should see an EPICS prompt.

```zsh
softIoc
epics>
```

You can exit with ctrl-c or by typing exit.



### c. python3 and something

##### 1. anaconda+vscode（recommend）

##### 2. pyqt5+QTdesigner

​	for gui

##### 3. pyepics

##### 4. matplotlib、scipy、numpy

```zsh
conda install matplotlib scipy numpy
```

##### 5. python sdds

​	For Ubuntu20.04, the link of the module is: https://ops.aps.anl.gov/downloads/SDDSPython3-5.2.1-1.ubuntu.20.04.x86_64.rpm

1. install

   ```bash
   wget https://ops.aps.anl.gov/downloads/SDDSPython3-5.2.1-1.ubuntu.20.04.x86_64.rpm
   alien -iv SDDSPython3-5.2.1-1.ubuntu.20.04.x86_64.rpm
   ```

   The `-v` parameter will show you where you are going to install the `sdds` module. 

2. for my case, update the `PYTHONPATH` env variable in my `.zshrc` file:

   ```bash
   # sdds python module
   export PYTHONPATH=/usr/lib/python3/dist-packages:$PYTHONPATH
   ```

3. go to any path, have a try, run ipython:

   ```
   import sdds
   ```

   to check whether you have set the right env variable.

##### tips: virtual environment is recommended!





## 2. use Git for the 1st time

### a. install git

```zsh
sudo apt-get install git
```

### b. configuration Git 

```zsh
git config --global user.name "example:zhanghaoran"
git config --global user.email "example:zhrzhm@ustc.edu.cn"
```

### c. clone from Remote Repository to Local Repository

```zsh
mkdir example:gitproj
cd gitproj
git clone https://git.ustc.edu.cn/zhanghaoran/half_linac.git
```



## 3. run this software

### a. add env variable

for my case, add the  env variable in my `.zshrc` file:

```zsh
export PYTHONPATH=$PYTHONPATH:~/gitproj
```

### b. modify softIOC config

文件在.\half_linac\softIOC\halflinac\iocBoot\ioctarget\envPaths   根据个人情况修改

可通过下面命令测试是否可正常运行

```zsh
./st.cmd
```

### c. run it

```zsh
python /home/user/gitproj/half_linac/apps/launcher/main.py
```

then you can try it~



# 使用Git日常管理代码

为方便多人同步开发，这里采用git管理代码。远程仓库使用科大的GItLab。

- 从远程仓库更新合并

```zsh
git pull origin main
```

- 向远程仓库提交修改后的文件

```zsh
git add .
git commit -m 'you can add some description about this commit'
git push origin main
```





