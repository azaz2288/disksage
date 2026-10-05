# 架构设计 · v0.2

engine.py负责有界只读扫描和内容复核，allocation.py调用Windows GetCompressedFileSizeW或Unix st_blocks。snapshot.py保存完整文件明细、目录与失败原因；main.py维护扫描状态和SQLite文件操作日志；pending/quarantined/restoring/restored/purging/purged可追踪状态。desktop.py提供Tk原生目录选择与本机服务入口。

## 模块和边界

FastAPI + SQLite + 无构建原生Web UI。数据库外部调用不放入长写事务；默认Host/Origin校验、CSP和输入转义。

- 目录扫描不是原子快照；无权访问和动态变化会影响统计，页面显示覆盖状态并可查看失败项目。系统分配数不包含文件系统元数据。
- 扫描默认没有文件/目录数量截断，明细写入本机SQLite；每页100项并可继续翻页、全局搜索、排序、CSV导出和重启恢复。可选扫描限额与取消仍支持；重复检查每文件1GiB、读预算10GiB。
- 隔离仍占空间；只有用户独立确认永久删除才释放。不能自动清理用户文档或重复文件。
- 路径校验不是对抗有本机文件写权限攻击者的操作系统沙箱。
- Windows便携包已构建和启动验证，未签名；正式安装器和签名证书尚未提供。
