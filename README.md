# 开发进度

2024-3-11-Libiaobin

- 以 `lattice_ini.lte` =>` lattice.json` 作为输入文件，自动生成 quad.template, bpm.template 等 IOC 文件。后面如果需要修改元件名称和PV命名规则，直接修改`lattice_ini.lte` 文件即可。见`gen_substitution_file()`
- 以 `lattice.json`文件作为中间媒介：
  - 当epics修改了 quad 的K1值时，IOC监测到PV值发生了改变，将自动更新 `lattice.json` 文件。当前只添加了 QUAD。
  - Elegant 每次循环运行时，都会重新读取 `lattice.json` 文件，生成 `lattice.lte`，然后运行。注意，lattice_ini.lte 文件没有改变。

2025-4-17-Zhanghaoran

- 发射度测量界面增加了`simply VM`按钮，可根据所选取的Q铁和FLAG简化lattice，加速虚拟加速器运行速度，而`full VM`按钮可将lattice恢复到原始状态

2025-4-24-Zhanghaoran

- 鉴于BPM数量多，`orbit_display`界面增加了选择显示一定范围BPMs的选项，并添加按钮可查看所有BPM的实时读数。

2025-4-29-Zhanghaoran

- 重新调整了launcher的gui布局，并将与VM相关的功能（start VM;start IOC; add error）单独放在一个用户界面，且静态误差可自定义。



# Quick start

## 1. Install some packages in Linux system

### 1）Linux environment

#### windows下如何实现linux环境

在Windows操作系统下创建和运行Linux环境通常有以下几种方法，推荐使用WSL：

##### 1. 使用Windows子系统（Windows Subsystem for Linux, WSL）

WSL 是微软提供的一个功能，允许你在Windows 10和Windows 11上直接运行Linux发行版，如Ubuntu、Debian等。

##### 2. 使用虚拟机软件（如VirtualBox或VMware）

通过虚拟机软件，你可以在Windows上运行一个完整的虚拟机，里面可以安装Linux操作系统。


### 2）epics

### 3）python3

### 4）pyqt

### 5）install python sdds

For Ubuntu20.04, the link of the module is: https://ops.aps.anl.gov/downloads/SDDSPython3-5.2.1-1.ubuntu.20.04.x86_64.rpm

1. install

   `alien -iv SDDSPython3-5.2.1-1.ubuntu.20.04.x86_64.rpm`

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

### b. run it

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





# 程序主要功能

- ## 虚拟加速器
	
	- 第一次运行，根据lattice_ini.lte和one_ini.ele 生成 json 文件，之后根据 json 文件生成lattice.lte和one.ele
	- 运行elegant one.ele 并向IOC发布相关pv value
	- 进入监视状态，一旦json文件发生变化，运行elegant one.ele 并向IOC发布相关pv值



- ## softIOC

	- 第一次运行，将生成 db 文件夹下的 substitutions 文件

	- 建立起IOC，将根据 json 文件初始化 epics 中所有四极铁等元件的初始pv值

	- 利用onChange函数监听元件的pv值，一旦改变，更新 json 文件

		

- ## apps
	
	- launcher 显示主界面，各按钮调用其他调束功能，单独开一个线程进行。
	- 各种上层物理调束程序
	  - bba
	  - beam_monitor
	  - emit_measure
	  - orbit_correct
	  - orbit_display

​					  。。。。。。


