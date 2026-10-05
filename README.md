# DiskSage · 磁盘分析与安全整理

分析任意本机目录的逻辑文件大小、目录占用与大文件；对允许临时目录中的旧 .tmp/.log 给出保守建议，选择后可隔离与恢复。可选 AI 只分析汇总信息。

**状态：v0.1 可运行基础版，按路线图持续开发。默认只允许本机访问。**

## 已实现

- 后台只读扫描、进度与取消、链接/目录联接跳过、权限错误计数
- 目录与大文件排行、扫描限额与部分结果标记
- 七天以上临时文件候选、清理预览、变化校验、隔离日志与恢复
- 服务重启后读取隔离历史；恢复不覆盖同名文件
- 可选模型摘要预览，不发送文件路径和文件内容

## 运行

需要 Python 3.12。Windows PowerShell：

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8765
```

浏览器打开 http://127.0.0.1:8765 。其他系统用 `.venv/bin/python`；已安装依赖可直接运行 `run.cmd`。配置 `APP_DATA_DIR` 可改变数据目录。

## 数据

data/app.db 只存隔离历史；候选在内存，重启后需要重扫。隔离文件保留在系统临时目录 .disksage-quarantine。真实路径不会进入Git。

## 可选模型配置

在启动服务器的 PowerShell 中设置环境变量（不会自动读取 .env 文件）：

```powershell
$env:LLM_API_KEY = "你的密钥"
$env:LLM_MODEL = "服务商支持的模型名称"
$env:LLM_BASE_URL = "https://api.openai.com/v1"
```

密钥只在服务端读取；不要写进前端、仓库或截图。接口为 OpenAI-compatible Chat Completions，可换兼容服务商。本项目不硬编码模型名称。AI 调用由用户主动触发，会按提供商计费；核心功能离线可用。

## 验证

```powershell
python -m unittest discover -s tests -v
python -m compileall -q app tests
```

CI在Linux和Windows运行相同测试。实际执行证据见 [进度](docs/PROGRESS.md)。

## 已知边界

- 报告为逻辑大小，硬链接可能重复计数，不等于磁盘实际分配空间
- 正在变化的目录没有原子快照；权限不足会产生不完整统计
- 隔离不会释放空间，第一版无永久删除；智能分析不等于无人值守删除
- 路径校验不是针对有权限同时恶意替换目录的攻击者的操作系统沙箱
- 最多500000文件/50000目录，超限或取消显示部分结果；长期断电恢复需进一步故障注入

## 设计与后续

- [架构设计](docs/DESIGN.md)
- [按顺序开发的里程碑](docs/ROADMAP.md)

MIT License。用户导入内容不随源码发布。
