# Worker 恢复与实验监控实施记录

状态：代码实施、独立审查、完整回归与合成端到端验收完成；尚未切换本地正式服务。

设计：[技术方案](../superpowers/specs/2026-11-12-worker-recovery-and-experiment-events-design.md)。实施：[计划](../superpowers/plans/2026-11-12-worker-recovery-and-experiment-events.md)。

## 交付行为

- Solver Worker 用数据库对应的 OS 锁保证单实例，仍按配额并发管理多个 GREAT；API 不取得该执行锁。
- 新 attempt 保存 owner、启动阶段、PID/创建时间及命令身份。独立恢复循环观察遗留进程、处理持久停止/重查；确认退出后才释放配额。身份不明保持阻塞并显示原因，不自动重跑或推断成功。
- 启动回调/数据库异常按 attempt 隔离；清理无法确认时保留进程引用与配额。恢复终态同步结束未完成的后处理，实际重查持久确认一次。
- 状态、旧任务事件和实验事件同事务保存；实验快照使用显式只读事务，游标绑定实验和日志代际，断线可重放。
- 工作台共享一条实验监控流，保留历史 attempt 选择；图片晚到或重算会刷新，日志只轮询选中且运行中的 attempt。

## 验证

- 最终完整后端：**472 passed in 159.03s，exit0**。命令为 `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/Scripts/python.exe -m pytest -p pytest_asyncio.plugin backend/tests -q --tb=short`（PowerShell中先设置环境变量）；完整输出 `tmp/worker-monitor-backend-verified.log`。

- 数据迁移 RED：旧版本缺少 0005，2个用例按预期失败；随后增加恢复身份与后处理字段及迁移。
- 启动 schema 检查 RED：缺少 schema 模块，1个用例按预期失败；随后增加启动前检查。
- Windows 实际进程集成：`test_recovery_native.py` **2 passed in 14.65s**。只创建可控 Python sleeper，验证新 owner 观察、自然退出收敛为 interrupted、持久化停止收敛为 cancelled，未重新执行 attempt。
- 恢复/原生进程/监控组合：**45 passed in 18.40s**，覆盖清理不确定、DB暂不可用、首次巡检重试、批准程序身份、分析中断和重查回执。
- 数据库升级/启动检查/维护：**9 passed in 11.09s**，包括完整无版本旧库验证后采用、原数据保留、备份、配置数据库选择，以及同名残缺监控表/错误恢复字段拒绝路径。
- 完整前端：**176 passed，14文件，120.75秒**；专项审查修复组 **48 passed**。TypeScript构建和lint通过，临时构建 JS 为 `index-BY4LewTj.js`。保留原有大bundle提示。
- 真实 Chrome + 隔离 FastAPI/SQLite：**9/9通过**。100日期任务、一页一条实验SSE、两标签页两条SSE、零旧任务流；任务终态无需刷新、终态后手工分析刷新图片、切中栏不额外开流、应用内离开实验关闭订阅、浏览器异常为0。
- 两个监控标签保持连接时，10个并发普通任务查询总耗时约119.3ms；这只是本机合成验收记录，不是生产P95性能声明。

浏览器记录：[JSON](2026-11-12-worker-monitor-browser.json)、[图片页截图](assets/2026-11-12-worker-monitor-images.png)。JSON连接统计采于第二工作区退出前，卸载释放由独立检查确认。图片为测试PNG占位符，未生成真实科学结果；会话供应商在该隔离fixture中关闭。初次浏览器完整导航检查没有直接测试React卸载，改用应用内popstate离开实验后通过。

独立审查的材料问题均已补回归：旧attempt资源误标、归档日志轮询、永久停止被网络切换解除、快照HTTP/Retry-After丢失、回调/清理失败释放配额、初始化恢复故障、批准程序匹配、分析中断、重查回执，以及无版本和同名冲突升级。

## 部署边界

本轮保留全部原有未提交实现；没有运行真实 GREAT、调用真实模型、修改正式数据库或重启服务。前端构建写入 `tmp/worker-monitor-dist`，避免改动现有服务直接使用的 `frontend/dist`。

部署时在空闲时段停止API/Worker，备份数据库和runs，再运行 `uv run great-agent migrate --config config/local.toml`。该维护命令只针对配置的数据库；无版本旧库先验证已知表结构再采用版本，未知结构拒绝处理。随后构建正式前端并重启服务。恢复数据库备份后用 `monitor-reset --config ...` 旋转监控代际。

此前回归还暴露测试隔离问题：调度spy缺少实例owner参数、10ms对话超时与仓库lease时钟混用、日志配置在全局关闭pytest handlers，以及Alembic禁用应用logger污染后续断言。仅测试夹具修正这些问题，生产对话/provider代码未改。最终取消重试用例等待真实子进程身份落库后，再验证5秒取消时限，分离冷启动与取消处理预算；恢复/原生组合31项通过。

先前失败输出保留于 `tmp/worker-monitor-backend-full.log`、`tmp/worker-monitor-backend-reviewed.log` 和 `tmp/worker-monitor-backend-final.log`；最终依据为 `tmp/worker-monitor-backend-verified.log`。没有删测试或用过程结果替代最终通过。
