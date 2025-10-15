# EDA服务器面板后端说明

## Route Map
1. 双重认证2FA接口统一化
2. Docker容器控制管理（认领、启停、映射卷，python docker-sdk）
3. 虚拟机控制管理（和Docker差不多）
4. 小工具的实现（例如设置权限组docker、libvirt这些）
5. 还有更多遇到再说...后续重点部署DAS

## 简要文档
### 文件结构
#### app/api
这个目录下目录下脚本对外提供Web接口。
#### app/services
这个目录下是脚本的python实现，API调用最后会从这里拿信息。**只需要关心这里的就可以了，可以让GPT生成脚本然后写成自己的。**
#### app/utils
这个目录下是工具，装饰器的实现。
#### app/config.py
这个文件存储了配置，当然也会从.env加载。
#### app/__init__.py
这是app的入口，重点关照create_app()函数。
#### xxx_run.py
这是API代理人（），一个开发一个实际生产。

## 重要说明
安全第一安全第一安全第一。
请检查service代码是否存在被注入可能性（最常见漏洞），交给GPT就好了。
目前项目还在开发中，实际使用比较保守。
最后要pyinstaller打包的，注意路径哦~