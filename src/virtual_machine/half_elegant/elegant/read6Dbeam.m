clear all;close all;clc



%% beam6D
%linac前后
%        x              xp             y              yp             t              p       
%        m                             m                             s           m$be$nc  

data1 = importdata('beam6D_start.DAT');
% sigmx1 = mean(data1.data(:,1));
% sigmxp1 = mean(data1.data(:,2));
% number1 = length(data1.data(:,1));
data2 = importdata('beam6D_middle.DAT');
% sigmp2 = mean(data2.data(:,1));
% sigmx2 = mean(data2.data(:,2));
% number2 = length(data2.data(:,1));
% data3 = importdata('beam6D_final.DAT');
% data3(find(data3(:,1)>6e-3),:)=[];
%% plot
%before
emittancex = sqrt(mean(data1(:,1).^2)*mean(data1(:,2).^2)-(mean(data1(:,1).*data1(:,2)))^2);
emittancey = sqrt(mean(data1(:,3).^2)*mean(data1(:,4).^2)-(mean(data1(:,3).*data1(:,4)))^2);
emittancex_nor = emittancex*(mean(data1(:,6)))*sqrt(1-1/(mean(data1(:,6)))^2);
emittancey_nor = emittancey*(mean(data1(:,6)))*sqrt(1-1/(mean(data1(:,6)))^2); 
figure;
subplot(2,2,1)
binscater_projection(data1(:,1)*1000,data1(:,2)*1000,250,250,'\sigma_x','\sigma_{x^{\prime}}')
colorbar off
set(gca,'fontname','arial','fontsize',14,'linewidth',2)
xlabel('x (mm)');ylabel('x^{\prime} (mrad)');
text(min(data1(:,1)*1000)+0.05*(max(data1(:,1)*1000)-min(data1(:,1)*1000)),max(data1(:,2)*1000)-0.05*(max(data1(:,2)*1000)-min(data1(:,2)*1000)),['\epsilon_{xn}=',num2str(emittancex_nor*1e6)],'fontname','arial','fontsize',12)
subplot(2,2,2)
binscater_projection(data1(:,3)*1000,data1(:,4)*1000,250,250,'\sigma_y','\sigma_{y^{\prime}}')
colorbar off
set(gca,'fontname','arial','fontsize',14,'linewidth',2)
xlabel('y (mm)');ylabel('y^{\prime} (mrad)');
text(min(data1(:,3)*1000)+0.05*(max(data1(:,3)*1000)-min(data1(:,3)*1000)),max(data1(:,4)*1000)-0.05*(max(data1(:,4)*1000)-min(data1(:,4)*1000)),['\epsilon_{yn}=',num2str(emittancey_nor*1e6)],'fontname','arial','fontsize',12)
subplot(2,2,3)
binscater_projection(data1(:,1)*1000,data1(:,3)*1000,250,250,'\sigma_x','\sigma_y')
colorbar off
set(gca,'fontname','arial','fontsize',14,'linewidth',2)
xlabel('x (mm)');ylabel('y (mm)');
subplot(2,2,4);
binscater_projection(data1(:,5)*1e12,data1(:,6),150,150,'\sigma_t','\sigma_{\beta\gamma}')
colorbar off
set(gca,'fontname','arial','fontsize',14,'linewidth',2)
xlabel('t (ps)');ylabel('\beta\gamma');
set(gcf,'unit','centimeters','position',[10 2 28 20]);


%middle
emittancex = sqrt(mean(data2(:,1).^2)*mean(data2(:,2).^2)-(mean(data2(:,1).*data2(:,2)))^2);
emittancey = sqrt(mean(data2(:,3).^2)*mean(data2(:,4).^2)-(mean(data2(:,3).*data2(:,4)))^2);
emittancex_nor = emittancex*(mean(data2(:,6)))*sqrt(1-1/(mean(data2(:,6)))^2);
emittancey_nor = emittancey*(mean(data2(:,6)))*sqrt(1-1/(mean(data2(:,6)))^2); 
figure;
subplot(2,2,1)
binscater_projection(data2(:,1)*1000,data2(:,2)*1000,250,250,'\sigma_x','\sigma_{x^{\prime}}')
colorbar off
set(gca,'fontname','arial','fontsize',14,'linewidth',2)
xlabel('x (mm)');ylabel('x^{\prime} (mrad)');
text(min(data2(:,1)*1000)+0.05*(max(data2(:,1)*1000)-min(data2(:,1)*1000)),max(data2(:,2)*1000)-0.05*(max(data2(:,2)*1000)-min(data2(:,2)*1000)),['\epsilon_{xn}=',num2str(emittancex_nor*1e6)],'fontname','arial','fontsize',12)
subplot(2,2,2)
binscater_projection(data2(:,3)*1000,data2(:,4)*1000,250,250,'\sigma_y','\sigma_{y^{\prime}}')
colorbar off
set(gca,'fontname','arial','fontsize',14,'linewidth',2)
xlabel('y (mm)');ylabel('y^{\prime} (mrad)');
text(min(data2(:,3)*1000)+0.05*(max(data2(:,3)*1000)-min(data2(:,3)*1000)),max(data2(:,4)*1000)-0.05*(max(data2(:,4)*1000)-min(data2(:,4)*1000)),['\epsilon_{yn}=',num2str(emittancey_nor*1e6)],'fontname','arial','fontsize',12)
subplot(2,2,3)
binscater_projection(data2(:,1)*1000,data2(:,3)*1000,250,250,'\sigma_x','\sigma_y')
colorbar off
set(gca,'fontname','arial','fontsize',14,'linewidth',2)
xlabel('x (mm)');ylabel('y (mm)');
subplot(2,2,4);
binscater_projection(data2(:,5)*1e12,data2(:,6),150,150,'\sigma_t','\sigma_{\beta\gamma}')
colorbar off
set(gca,'fontname','arial','fontsize',14,'linewidth',2)
xlabel('t (ps)');ylabel('\beta\gamma');
set(gcf,'unit','centimeters','position',[10 2 28 20]);


% x-gamma 以便考虑能量狭缝
% figure
% subplot(2,1,1)
% binscater_projection(data2(:,1)*1000,data2(:,6),150,150,'\sigma_x','\sigma_{\beta\gamma}')
% colorbar off
% set(gca,'fontname','arial','fontsize',14,'linewidth',2)
% xlabel('x (mm)');ylabel('\beta\gamma');
% % 假设加一个能量狭缝后的纵向相空间
% subplot(2,1,2)
% index = find(abs(data2(:,1)+4e-3)<6e-3);
% binscater_projection(data2(index,5)*1e12,data2(index,6),150,150,'\sigma_t','\sigma_{\beta\gamma}')
% colorbar off
% set(gca,'fontname','arial','fontsize',14,'linewidth',2)
% xlabel('t (ps)');ylabel('\beta\gamma');
% set(gcf,'unit','centimeters','position',[10 2 16 20]);
return
%after
emittancex = sqrt(mean(data3(:,1).^2)*mean(data3(:,2).^2)-(mean(data3(:,1).*data3(:,2)))^2);
emittancey = sqrt(mean(data3(:,3).^2)*mean(data3(:,4).^2)-(mean(data3(:,3).*data3(:,4)))^2);
emittancex_nor = emittancex*(mean(data3(:,6)))*sqrt(1-1/(mean(data3(:,6)))^2);
emittancey_nor = emittancey*(mean(data3(:,6)))*sqrt(1-1/(mean(data3(:,6)))^2); 
figure;
subplot(2,2,1)
binscater_projection(data3(:,1)*1000,data3(:,2)*1000,250,250,'\sigma_x','\sigma_{x^{\prime}}')
colorbar off
set(gca,'fontname','arial','fontsize',14,'linewidth',2)
xlabel('x (mm)');ylabel('x^{\prime} (mrad)');
text(min(data3(:,1)*1000)+0.05*(max(data3(:,1)*1000)-min(data3(:,1)*1000)),max(data3(:,2)*1000)-0.05*(max(data3(:,2)*1000)-min(data3(:,2)*1000)),['\epsilon_{xn}=',num2str(emittancex_nor*1e6)],'fontname','arial','fontsize',12)
subplot(2,2,2)
binscater_projection(data3(:,3)*1000,data3(:,4)*1000,250,250,'\sigma_y','\sigma_{y^{\prime}}')
colorbar off
set(gca,'fontname','arial','fontsize',14,'linewidth',2)
xlabel('y (mm)');ylabel('y^{\prime} (mrad)');
text(min(data3(:,3)*1000)+0.05*(max(data3(:,3)*1000)-min(data3(:,3)*1000)),max(data3(:,4)*1000)-0.05*(max(data3(:,4)*1000)-min(data3(:,4)*1000)),['\epsilon_{yn}=',num2str(emittancey_nor*1e6)],'fontname','arial','fontsize',12)
subplot(2,2,3)
binscater_projection(data3(:,1)*1000,data3(:,3)*1000,250,250,'\sigma_x','\sigma_y')
colorbar off
set(gca,'fontname','arial','fontsize',14,'linewidth',2)
xlabel('x (mm)');ylabel('y (mm)');
subplot(2,2,4);
binscater_projection(data3(:,5)*1e12-28354.8-1337.7+1.8,data3(:,6),150,150,'\sigma_t','\sigma_{\beta\gamma}')
colorbar off
set(gca,'fontname','arial','fontsize',14,'linewidth',2)
xlabel('t (ps)');ylabel('\beta\gamma');
set(gcf,'unit','centimeters','position',[10 2 28 20]);

% current
% Q = 5*size(data3,1)/size(data1,1);%nC
% [counts1,centers1] = hist(data3(:,5)*1e12-28354.8,150);
% sum = trapz(centers1,counts1);
% figure
% plot(centers1,Q*1e3*counts1/sum,'linewidth',2)
% xlabel('t (ps)');ylabel('I (A)');
% set(gca,'fontname','arial','fontsize',14,'linewidth',2)


%% 相关参数计算
%eta 色散量
%exit
input  = data3;
x = input(:,1);
xp = input(:,2);
y = input(:,3);
yp = input(:,4);
delta = (input(:,6)-mean(input(:,6)))/mean(input(:,6));
sigma16  = mean((x-mean(x)).*(delta-mean(delta)));
sigma26  = mean((xp-mean(xp)).*(delta-mean(delta)));
sigma36  = mean((y-mean(y)).*(delta-mean(delta)));
sigma46  = mean((yp-mean(yp)).*(delta-mean(delta)));
sigma66  = mean((delta-mean(delta)).*(delta-mean(delta)));
etax = sigma16/sigma66
etaxp  = sigma26/sigma66
etay = sigma36/sigma66
etayp  = sigma46/sigma66


%%
function []=binscater_projection(x,y,a,b,name1,name2)

[counts1,centers1] = hist(x,b);
[counts2,centers2] = hist(y,b);
binscatter(x,y,a);colormap('jet')
xlimin = min(x)-0.10*(max(x)-min(x));xlimax =  max(x)+0.10*(max(x)-min(x));
ylimin = min(y)-0.10*(max(y)-min(y));ylimax = max(y)+0.10*(max(y)-min(y));
xlim([xlimin xlimax]);ylim([ylimin ylimax])
hold on
p1 = plot(centers1,counts1/max(counts1)*(ylimax-ylimin)*0.3+ylimin,'-m','linewidth',1.5);
hold on
p2 = plot(counts2/max(counts2)*(xlimax-xlimin)*0.3+xlimin,centers2,'-r','linewidth',1.5);
legend([p1 p2],{[name1,'=',num2str(std(x))],[name2,'=',num2str(std(y))]},'box','off')
text(xlimax-0.15*(xlimax-xlimin),ylimin+0.15*(ylimax-ylimin),['n=',num2str(length(x))])
end






