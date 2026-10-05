# DiskSage · 磁盘分析与安全整理

**v0.2 可运行功能版**。默认只允许本机访问。

## 已实现

- 只读后台扫描、取消、权限错误与部分结果；驱动器容量选择、原生目录选择启动器
- 逻辑大小、硬链接去重大小、系统分配空间；目录下钻、面积图、扩展名分布、大文件排行、JSON报告
- 按大小筛选与SHA256复核重复内容；读取预算与变化校验，仅生成审查清单
- 七天以上 .tmp/.log 临时候选，隔离预览、2GiB配额、批次撤销、重启恢复日志
- 隔离记录可独立输入“永久删除”确认释放空间；恢复遇到同名不覆盖，恢复中断可重试
- 页面配置兼容API地址、模型和Key；默认只发送类型/大小汇总，可勾选允许路径并预览确认，用实际大文件与目录记录提问；模型不能触发文件操作

## 运行

Python 3.12，Windows PowerShell：

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8765
```

打开 http://127.0.0.1:8765 。已有依赖时可用 `run.cmd`。其他系统使用 `.venv/bin/python`。`APP_DATA_DIR`覆盖数据目录。

## 可选AI

页面“API设置”填写兼容地址、模型与Key，Key仅在服务端内存保存，重启后使用环境变量。也可设置 `LLM_API_KEY`、`LLM_MODEL`、`LLM_BASE_URL`；不自动读取.env。不要提交或截图密钥。AI操作需用户主动触发，按服务商计费。本轮没有使用付费API。

原生目录选择：`python -m app.desktop`。Windows便携包构建：`powershell -File tools/build-desktop.ps1`；输出dist/DiskSage，整个目录一起分发。打包版数据默认写入LOCALAPPDATA/disksage/data，不会写程序资源目录。

## 数据与备份

数据位于data/，已排除Git；不要提交数据库、导入内容、磁盘清单或密钥。详细启动、备份和部署边界见 [运行说明](docs/OPERATIONS.md)。

## 验证

```powershell
python -m unittest discover -s tests -v
python -m compileall -q app tests
```

19项：18通过，1项Windows符号链接权限跳过。硬链接、权限异常、配额、批次恢复和中断日志均覆盖。2500文件/2560000字节扫描0.785秒，Python峰值2263904字节；这是单次合成基准。 Linux/Windows CI使用同一提交验证。

## 已知边界

- 目录扫描不是原子快照；无权访问和动态变化会影响统计，页面显示覆盖状态并可查看失败项目。系统分配数不包含文件系统元数据。
- 扫描默认没有文件/目录数量截断，明细写入本机SQLite；每页100项并可继续翻页、全局搜索、排序、CSV导出和重启恢复。可选扫描限额与取消仍支持；重复检查每文件1GiB、读预算10GiB。
- 隔离仍占空间；只有用户独立确认永久删除才释放。不能自动清理用户文档或重复文件。
- 路径校验不是对抗有本机文件写权限攻击者的操作系统沙箱。
- Windows便携包已构建和启动验证，未签名；正式安装器和签名证书尚未提供。

[架构设计](docs/DESIGN.md) · [路线图](docs/ROADMAP.md) · [验收记录](docs/PROGRESS.md)

MIT License。用户导入内容不随源码发布。
