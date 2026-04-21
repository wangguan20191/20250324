#!/bin/bash
# 更新软件包并安装Nginx
sudo yum update -y
sudo amazon-linux-extras install nginx1 -y
# 启动Nginx服务并设置开机自启
sudo systemctl start nginx
sudo systemctl enable nginx
# 创建自定义欢迎页
echo "<h1>Welcome to my Terraform-managed Nginx server!</h1>" | sudo tee /usr/share/nginx/html/index.html