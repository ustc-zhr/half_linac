clear all;close all;clc


%% 发射度变化过程
%input
filename = 'ENXY.DAT';
a = 3;b = 531-5;% a 是表头行数 b是每组数据行数
group = 1;
% main
data = readENXYdata(filename,a,b);
figure;
subplot(2,1,1)
for i=1:group
    plot(data((i-1)*b+1:i*b,1),data((i-1)*b+1:i*b,2)*1e6);
    hold on
end
set(gca,'fontname','arial','fontsize',14,'linewidth',1.5);xlabel('s (m)');ylabel('enx (\mumrad)');
% set(gcf,'unit','centimeters','position',[10 10 7.6*col/2 8]);

subplot(2,1,2)
for i=1:group
    plot(data((i-1)*b+1:i*b,1),data((i-1)*b+1:i*b,3)*1e6);
    hold on
end
set(gca,'fontname','arial','fontsize',14,'linewidth',1.5);xlabel('s (m)');ylabel('eny (\mumrad)');
% latticeplot('one.mag',-0.5,0.1)
% set(gcf,'unit','centimeters','position',[10 10 38 8]);

%% 波荡器入口发射度统计
% figure;
% subplot(2,1,1);
% histogram(data([0:1:200]*b+65,2)*1e6,30);
% set(gca,'fontname','arial','fontsize',14,'linewidth',1.5);xlabel('enx (\mumrad)');ylabel('counts');title('undulator@entrance')
% subplot(2,1,2);
% histogram(data([0:1:200]*b+65,3)*1e6,30);
% set(gca,'fontname','arial','fontsize',14,'linewidth',1.5);xlabel('eny (\mumrad)');ylabel('counts')


%% 出口发射度统计
% data_enx = importdata('enx.DAT');
% data_eny = importdata('enY.DAT');
% figure;
% subplot(2,1,1);
% histogram(data_enx.data,30);
% set(gca,'fontname','arial','fontsize',14,'linewidth',1.5);xlabel('enx (μmrad)');ylabel('counts')
% subplot(2,1,2);
% histogram(data_eny.data,30);
% set(gca,'fontname','arial','fontsize',14,'linewidth',1.5);xlabel('eny (μmrad)');ylabel('counts')








%% some functions
function data=readENXYdata(filename,a,b)
 i = 0;
 j = 0;
 began = 0;
 data = [];
 fileID = fopen(filename,'r'); 
 while ~feof(fileID)
        tline=fgetl(fileID);                              %逐行读取原始文件
        i=i+1;

        if contains(tline,'enx')
            began = i+a;%该位置第一个数
            eend = i+a-1+b;%该位置最后一个数
        end
        
        if (began>0)&&(eend>=i)&&(i>=began) 
            j = j+1;
            data(j,:)=str2num(tline);%       x           xp           y           yp          phi          w    particle #
        end
 end
 fclose(fileID); 
 fclose all;
end
