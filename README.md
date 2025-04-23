Date: 2024-04-23



# Quick start

### 1. Install some packages in Linux system

epics

python3

pyqt



### 2. Git management for the 1st time

#### a. install git

```zsh
sudo apt-get install git
```

#### b. configuration Git 

```zsh
git config --global user.name "zhanghaoran"
git config --global user.email "zhrzhm@ustc.edu.cn"
```

#### c. create a local Git Repository

```zsh
mkdir PATH-TO-HERE/half_linac
cd half_linac
git init
```

#### d. clone from Remote Repository



















1. run the elegant virtual machine

  go to `virtual_machine` run `python main.py`. The elegant simulation results of all the BPM Cx and Cy are broadcasted into EPICS PV. The BPM PV name ranges from BPM01 to BPM43:

  ```
  camonitor LN:TEST:BPM01:X:ai
  camonitor LN:TEST:BPM01:Y:ai
  ```




# others

## install python sdds

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

	

# 开发进度

## 2024-03-11 周一

今天完成了：

- 以 lattice_ini.lte => lattice.json 作为输入文件，自动生成 quad.template, bpm.template 等 IOC 文件。后面如果需要修改元件名称和PV命名规则，直接修改`lattice_ini.lte` 文件即可。见`gen_substitution_file()`

	

- 以 `lattice.json`文件作为中间媒介：

	- 当epics修改了 quad 的K1值时，IOC监测到PV值发生了改变，将自动更新 `lattice.json` 文件。当前只添加了 QUAD。
	- Elegant 每次循环运行时，都会重新读取 `lattice.json` 文件，生成 `lattice.lte`，然后运行。注意，lattice_ini.lte 文件没有改变。



下一步计划：

- [ ] 完成发射度测量程序。





# 程序结构

- 虚拟加速器
	- 第一次运行，根据lattice.lte 生成 json 文件，后续循环，每次读入为 json 文件；epics caput命令更新的也是json文件。
	- 生成json文件时，也会自动添加 PV 通道名，注意和db文件中保持一致。



- softIOC

	- 第一次运行，将生成 db 文件夹下的 substitutions 文件，template 文件需手动vim输入生成，注意PV命名规则和 elegant_parser.py & impactz_parser.py 两个文件中添加通道名时的规则保持一致。

	- 第一次运行，也将根据 json 文件初始化 epics 中所有四极铁等元件的初始K值。

	- 利用onChange函数监听磁铁元件的K值改变，一旦改变，更新 json 文件。

		

- apps
	- 开发各种上层物理调束程序
	- launcher 显示主界面，各按钮调用其他调束软件，单独开一个线程进行。









