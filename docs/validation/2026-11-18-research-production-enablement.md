# 科研分析 Tab 当前环境启用记录

2026-11-18，经用户明确授权完成备份、数据库迁移、前端构建及服务启动。

试用地址：<http://127.0.0.1:8765/?workspace=research>。新建课题后选入材料，再发送问题。

## 已执行

1. 只读预检确认正式库原版本为 `0009`，完整性和外键检查通过；原有两个实验 attempt 均为 succeeded，没有 pending/running 实验或运行中的解算器。原 PID 记录已失效，按现有停止脚本清理。
2. 使用项目备份工具同时备份数据库、runs、文献原件和配置，并再次执行 verify。结果：125 个文件，数据库完整性通过。
3. 在备份的第二份数据库副本上试迁移，确认原有表数据不变后，通过原 CLI 正式迁移至 `0010`。迁移前后逐表比较原有 36 张表的行数与规范化内容 SHA-256，全部一致。
4. 使用现有启动器构建前端到 staging，验证资源完整后切换 `frontend/dist`。构建固定采用同源 API 路径 `/api/v1`，配置文件与产品源码未修改。
5. 以 `-ApiOnly` 启动 API，再确认研究队列为空后单独启动研究 Worker。求解器 Worker 保持关闭，避免认领历史排队实验。

## 当前服务与备份

| 项目 | 值 |
| --- | --- |
| API | PID 34840；`http://127.0.0.1:8765` |
| Research Worker | PID 45732；受管标记 `data/pids/research-worker.pid` |
| Solver Worker | 未启动，`data/pids/worker.pid` 不存在 |
| 数据库 | `data/great_agent.db`；实际 Alembic 版本 `0010` |
| 完整备份 | `data/backups/research-enable-20261118-190332` |
| 备份清单 | 同目录 `manifest.json`，含文件大小和 SHA-256 |
| 发布及 API 日志 | `frontend/.releases/20261118T110858Z-2ce4726e` |
| 研究 Worker 日志与启用报告 | `data/activations/research-enable-20261118-190332` |
| 原前端备份 | 上述发布目录的 `previous-dist` |

## 实际验收

- 健康、API-only readiness、科研 catalog、模型配置状态、课题列表和原实验列表的 GET 请求均通过；返回的 HTML 与当前构建文件完全一致。
- 当前科研材料目录可见 4 个实验、6 份文献和 1 个注册源码来源。研究模型继承现有模型凭据，配置状态为就绪；没有发送供应商请求来测试真实模型。
- 正式浏览器验证仅发出 7 个 GET 请求，无写请求、页面错误或 HTTP 错误。科研 Tab、新课题表单与原有导航正常，390px 布局无横向溢出。
- 启动后再次核对实际数据库：原有 36 张表内容仍与备份相同，两个历史 attempt 仍为 succeeded。研究课题、研究轮次和研究模型调用均为 0；未创建测试课题、修改研究源码、启动新实验或重新后处理。
- API 和研究 Worker 的 PID、进程创建时间及命令身份核对通过；求解器 Worker 未启动。
- 产品源码 SHA-256 为 `951dda536b18193b923e736dc8f2d327232bcf0cb4ad4ae69ed12a2c3551d173`，与此前 1049 项后端/脚本、359 项前端测试及类型检查/lint/构建通过的源码一致。

原始结果：启用目录中的 `migration-result.json`、`processes.json`、`verification.json`、`browser-readonly-production.json`。截图为 `browser-readonly-research-desktop.png` 与 `browser-readonly-research-mobile.png`。

诊断接口目前仍将 `schema.revision` 标签写死为 `0009`；其 schema 就绪判断执行真实结构校验。本次实际版本由只读查询 `alembic_version=0010` 确认，标签问题未在本次运维启用中改动产品源码。

## 后续操作

刷新或重新打开试用地址即可使用。科研 Agent 只有读取、已有指标比较及证据检索工具，代码修改和验证步骤始终作为未应用建议展示。

本次采用“API + 科研 Worker”模式。普通 `scripts/start.ps1` 默认会启动求解器 Worker；若希望继续保持本次只读试用模式，不应直接使用默认三进程启动。停止时可使用现有 `scripts/stop.ps1`，它会按 PID 记录停止 API 和科研 Worker。

需要重新启动本次模式时，先确认科研队列状态，再启动 API（复用已验证前端）：

```powershell
.\scripts\start.ps1 -Config config\local.toml -ApiOnly -NoBuild -NoBrowser
# 确认研究队列后，单独启动 research-worker，并保存受管 PID。
# 本次执行的具体启动与进程记录见上述启用目录。
```

本记录中的 PID 是本次启动身份，后续重启应以 `data/pids` 和实际进程身份为准。
