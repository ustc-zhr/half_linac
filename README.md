# Date: 2024-04-23



# Quick start

## 1. Install some packages in Linux system

### 1）epics

### 2）python3

### 3）pyqt

### 4）install python sdds

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

向远程仓库提交修改后的文件

```zsh
git add .
git commit -m 'you can add some description about this commit'
git push -uf origin main
```

从远程仓库更行合并

```zsh
git pull origin main
```







# 程序主要功能

- 虚拟加速器
	- 第一次运行，根据lattice_ini.lte和one_ini.ele 生成 json 文件，之后根据 json 文件生成lattice.lte和one.ele
	- 运行elegant one.ele 并向IOC发布相关pv value
	- 进入监视状态，一旦json文件发生变化，运行elegant one.ele 并向IOC发布相关pv值



- softIOC

	- 第一次运行，将生成 db 文件夹下的 substitutions 文件

	- 第一次运行，也将根据 json 文件初始化 epics 中所有四极铁等元件的初始pv值

	- 利用onChange函数监听元件的pv值，一旦改变，更新 json 文件

		

- apps
	
	- launcher 显示主界面，各按钮调用其他调束软件，单独开一个线程进行。
	- 开发各种上层物理调束程序

。。。。。。





# 开发进度

2025-4-17：发射度测量界面增加了simply VM按钮，可根据所选取的Q铁和FLAG简化lattice，加速虚拟加速器运行速度，而full VM按钮可将lattice恢复到原始状态

