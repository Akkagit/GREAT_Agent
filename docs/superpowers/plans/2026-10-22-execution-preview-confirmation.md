# XML 复核与解算确认闭环实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 打通“接受提案 → 检查输入 → 生成 XML → 展示执行预览 → 用户明确确认 → 解算 → 运行监控”，保留已有真实运行记录。

**Architecture:** 沿用 React + FastAPI + SQLite + 独立 Worker。新增持久化的执行预览和逐 attempt 授权；预览只准备文件，确认才产生可领取的任务。Worker 使用确认过的 XML 字节及命令，不在启动前重新渲染。

**Tech Stack:** Python 3.11、Pydantic、SQLAlchemy、Alembic、pytest；React 19、TypeScript、Vitest、现有 CSS。此次不引入新依赖。

**Spec:** 用户在当前对话已批准的完整流程及此前要求的 XML、命令参数、输入报告、输出目录、可执行文件绝对路径展示；背景设计见 `docs/superpowers/specs/2026-10-09-great-agent-design.md`。

## Global Constraints

- 唯一正式项目目录为 `F:\GREAT_Agent`；在现有工作区工作，不新建或切换 worktree，不丢弃未提交改动。
- 本次开发与验收不启动真实 GREAT，不调用真实 GLM；数值解算仅使用已有模拟程序。
- 2024 DOY 350 已完成一次真实运行。实验 `476c79c3-5452-4967-afc7-0bafad975436` 的数据库记录、XML、日志、FLT 和分析产物保持不变；查看不意味着重跑授权。
- 展示程序绝对路径、SHA256、参数数组、工作目录、XML 全文及路径、逐日输入报告、输出目录、日期、测站和场景；不能只展示模板或者示意命令。
- 用户点击接受提案、检查输入、冻结、生成 XML、关闭预览或查看历史，均不能启动进程。
- 只有用户勾选“已核对 XML、程序、输入和输出目录”，再点击“确认并开始解算”，才提交该预览 ID 的执行确认。助手不会代替用户点击。
- 预览绑定 revision、完整日期集、程序和模板身份、XML 内容及目标路径；确认时和启动前都校验。过期、篡改、缺文件、错误实验或错误 attempt 的授权必须拒绝。
- 已冻结计划暂不新增“解冻”能力；需要修改时创建新实验。未接受的卡片修改或待审批提案会阻止进入执行预览，避免实际执行旧 revision。
- 自动绘图现有行为不在此任务中悄悄改变。预览必须如实提示：静态 DF Float 目前成功后会自动分析/绘图；“不绘图”显式开关另行完成前不能宣称已生效。
- 不自动提交、推送代码；不因开发需要重跑上午的真实冒烟。

## 已采用的界面与存储选择

这些是实施建议，不是额外要求用户已经确认过的细节。

- 保留规划台三栏和运行仪表盘；在计划下方增加执行预览区，而非另建一套应用。
- 沿用白底 `#ffffff`、底色 `#f5f7fa`、文字 `#17243b`、主色 `#1d4ed8`、强调 `#0891b2`、确认色 `#18794e`。沿用现有中文 UI 字体，只有 XML/命令使用等宽字体。
- 顶部用有语义的步骤条显示当前阶段；预览区左侧为逐日文件和命令摘要，右侧为可滚动 XML。移动端上下排列，路径允许换行，XML 保留横向滚动。
- 主操作依次为“检查输入”“生成 XML 并预览”“确认并开始解算”“查看运行监控”。生成预览时复用内部冻结能力，并明确提示“计划已冻结，尚未开始解算”。
- 预览原件位于 `runs/<experiment>/previews/<preview_id>/`；XML 内写入实际 attempt 的输出目录。确认后将同一份字节写入目标 attempt 的 `config.xml`，命令中的 `-x` 指向该最终路径。
- 新增预览及授权表，不改写历史任务、attempt 或产物。API 与独立 Worker 通过同一 SQLite 数据库读取授权，不能用只在 API 内存中存在的 token。
- 未确认预览默认 30 分钟有效，返回 `expires_at`；确认后授权不随排队时间自动失效，但仍须通过启动前的文件身份检查。请求中的 `confirmed` 严格要求 JSON 布尔值 `true`，不接受字符串或数字代替。
- 初次运行和失败/中断重试均复用同一预览组件；重试只批准所选任务的下一 attempt，不批准其他日期。

## Review Focus

1. 双击确认、HTTP 超时后重发、两个不同预览并发确认：最多产生一组任务和一份对应 attempt 授权。
2. 浏览器保留旧预览，或磁盘 XML、程序、模板及输入状态在等待期间改变：拒绝执行，并提示重新检查/预览。
3. 页面刷新、API/Worker 分离启动：授权不丢失；未确认预览不会被 Worker 领取。
4. 直接调用旧 `/run`、`/retry` 或旧 smoke 路径：不能绕过确认；没有授权的旧 pending 任务也不能启动真实程序。
5. 打开已有 DOY 350 实验、切换监控页、连接断开重连：仅恢复展示，不重新入队，不覆盖旧 XML。

---

### Task 1: 持久化逐日执行预览

**Files:**
- Create: `backend/src/great_agent/services/execution_service.py`
- Create: `backend/alembic/versions/0002_execution_previews.py`
- Modify: `backend/src/great_agent/db/models.py`
- Modify: `backend/src/great_agent/adapters/great_pppflt.py`
- Modify: `backend/src/great_agent/api/dependencies.py`
- Modify: `backend/src/great_agent/api/experiments.py`
- Modify: `backend/src/great_agent/cli.py`
- Modify: `backend/src/great_agent/errors.py`, `backend/src/great_agent/app.py`
- Test: `backend/tests/test_execution_preview.py`, `backend/tests/test_pppflt_adapter.py`

**Interfaces:**
- `ExecutionService.prepare(experiment_id: UUID, *, expected_revision: int, task_id: UUID | None = None) -> ExecutionPreview`。
- `ExecutionPreview` 含 `id`, `experiment_id`, `revision`, `intent` (`initial`/`retry`), `expires_at`, `executable_path`, `executable_sha256`, `template_path`, `template_sha256`, `jobs`。每个 job 含 `year`, `doy`, `attempt_number`, `xml_content`, `xml_sha256`, `xml_path`, `output_dir`, `cwd`, `command: tuple[str, ...]`, `inputs`。
- `POST /api/v1/experiments/{id}/execution-preview` 请求 `{expected_revision, task_id?}`；返回上述实际预览，失败返回稳定错误码。
- Adapter 的 `render_task(spec, date, task_dir, *, xml_path: Path | None = None)` 保持默认调用兼容；服务传入独立预览 XML 路径，输出仍定位真实 attempt。

- [ ] 写失败测试：预览返回全文/真实路径/全部输入，XML 输出路径与返回值一致；创建预览后每日任务数仍为 0，`Worker.run_once()` 不启动进程。
- [ ] 写失败测试：缺输入、旧 revision、错误 task 所属实验、正在运行或已成功任务的重试均被拒绝；跨年日期去重后每日期一份 XML，两个预览互不覆盖。
- [ ] 运行 `uv run pytest backend/tests/test_execution_preview.py backend/tests/test_pppflt_adapter.py -q`，确认失败来自缺失功能。
- [ ] 实现服务、模型与 Alembic 增量迁移；记录预览、程序/模板 SHA256、输入文件路径/大小/mtime 和目标 attempt。扩展 Adapter XML 保存位置且保持路径安全约束。
- [ ] 接入服务和 API；对重复冻结保持安全兼容；重新运行上述测试并确认通过。

### Task 2: 确认后入队，Worker 只执行已审核快照

**Files:**
- Modify: `backend/src/great_agent/services/execution_service.py`
- Modify: `backend/src/great_agent/services/task_service.py`
- Modify: `backend/src/great_agent/db/repositories.py`
- Modify: `backend/src/great_agent/runtime/worker.py`
- Modify: `backend/src/great_agent/api/experiments.py`, `backend/src/great_agent/api/tasks.py`
- Modify: `backend/src/great_agent/cli.py`
- Modify: `scripts/real-smoke.ps1`
- Test: `backend/tests/test_execution_confirmation.py`, `backend/tests/test_worker.py`, `backend/tests/test_api.py`, `backend/tests/test_task_materialization.py`, `backend/tests/test_mock_e2e.py`

**Interfaces:**
- `ExecutionService.confirm(experiment_id: UUID, *, preview_id: UUID, expected_revision: int, confirmed: Literal[True]) -> tuple[DailyTaskRecord, ...]`；同一确认幂等。
- `POST /api/v1/experiments/{id}/run` 必须提交 `{preview_id, expected_revision, confirmed: true}`。旧的无请求体调用返回 `execution_confirmation_required`，不入队。
- 重试走同一 `confirm()`，预览内 intent/task_id 决定只授权下一 attempt；旧 `POST /tasks/{id}/retry` 无确认不得入队。
- `ExecutionService.approved_job(task_id: UUID, attempt_number: int) -> ExecutionJob` 由 Worker 查询。找不到授权或校验不通过时记录明确错误，不调用 `ProcessRunner.run()`。
- 授权关联 `(task_id, attempt_number, preview_id)` 且唯一；确认消费和任务创建/重试在同一 SQLite 事务内完成。

- [ ] 写失败测试：无确认、`confirmed: false`、跨实验 token、旧 token、XML/程序/模板改动、输入被移除都不能入队或启动；直接运行旧无授权 pending 任务也不能启动。
- [ ] 写失败测试：确认幂等与不同预览并发确认均不重复排队；API 服务重新创建后 token 仍能读取；已成功任务不会因再次确认重新运行。
- [ ] 写失败测试：超过 30 分钟的未确认预览、`confirmed: "true"` 和 `confirmed: 1` 均被拒绝；已确认任务排队超过 30 分钟不需要重新授权，但必须重新检查文件。
- [ ] 写失败测试：模拟 Worker 实际读到的 XML 字节、程序路径与命令参数和用户确认的预览一致；Worker 不调用 `render_task()`；重试必须复核下一 attempt。
- [ ] 运行 `uv run pytest backend/tests/test_execution_confirmation.py backend/tests/test_worker.py backend/tests/test_api.py backend/tests/test_task_materialization.py backend/tests/test_mock_e2e.py -q`，确认预期红灯。
- [ ] 实现事务确认与逐 attempt 授权；Worker 校验后复制已审核字节到最终 XML，再执行批准命令。保留已确认任务内的模拟 command factory 供现有模拟测试使用，不作为生产绕过开关。
- [ ] 旧 CLI smoke 的自动 materialize 路径改为拒绝绕过并提示进入 Web 预览确认；`preflight` 保持只检查/生成预览，不执行。更新脚本提示，不能默默继续真实运行。
- [ ] 更新模拟测试夹具走真实确认流程，再运行上述测试确认通过。

### Task 3: 规划台、执行预览及监控导航

**Files:**
- Create: `frontend/src/components/ExecutionPreview.tsx`, `frontend/src/components/ExecutionPreview.css`
- Modify: `frontend/src/api/types.ts`, `frontend/src/api/client.ts`
- Modify: `frontend/src/pages/ExperimentWorkspace.tsx`, `frontend/src/pages/ExperimentWorkspace.css`
- Modify: `frontend/src/pages/RunDashboard.tsx`, `frontend/src/components/TaskBoard.tsx`
- Modify: `frontend/src/App.tsx`
- Test: `frontend/src/tests/ExecutionPreview.test.tsx`, `frontend/src/tests/ExperimentWorkspace.test.tsx`, `frontend/src/tests/RunDashboard.test.tsx`, `frontend/src/tests/App.test.tsx`

**Interfaces:**
- 客户端新增 `prepareExecution(id, {expected_revision, task_id?})`、`confirmExecution(id, {preview_id, expected_revision, confirmed: true})`，类型与 Task 1/2 一致。
- `ExecutionPreview` 接收预览、确认中状态、错误提示、`onConfirm`、`onClose`；确认框默认未勾选，勾选前禁止提交，关闭/切换预览清除勾选。
- App 管理规划/监控视图并同步 `?experiment=<id>&view=run`，支持浏览器前进/后退和刷新；不得把切换视图当作启动任务。

- [ ] 写失败测试：接受提案与检查输入不会调用执行；检查通过才允许生成；待审批提案或未保存的卡片修改阻止生成；生成后仍未调用确认。
- [ ] 写失败测试：逐日预览展示 XML/命令/程序完整路径/输入/输出，未勾选不能确认，取消不能执行；切换预览与失败后不保留旧确认。
- [ ] 写失败测试：明确确认只发一次请求；确认成功进入监控，失败保留预览并展示后端具体错误；已有任务打开即展示“查看运行监控”而非重跑按钮。
- [ ] 写失败测试：重试按钮先准备所选 task 的预览；关闭不入队，确认后才重试；查询参数刷新和浏览器后退保持导航正确。
- [ ] 运行 `npm --prefix frontend test -- --run`，确认新增用例因缺功能失败。
- [ ] 实现预览组件与客户端，再接规划台状态、未保存修改检查、确认请求和导航；复用该组件处理监控页重试。
- [ ] 清晰展示成功后的自动后处理行为；保持现有配色和主要布局，用步骤条与可访问表单组织新增内容，不做无关视觉重构。
- [ ] 运行前端完整测试、类型检查和 lint，修正回归。

### Task 4: 已有记录入口与无真实解算验收

**Files:**
- Modify: `backend/src/great_agent/services/plan_service.py`, `backend/src/great_agent/api/experiments.py`
- Modify: `frontend/src/App.tsx`, `frontend/src/api/types.ts`, `frontend/src/api/client.ts`
- Modify: `README.md`
- Create: `docs/validation/2026-10-22-execution-preview-confirmation.md`
- Test: `backend/tests/test_api.py`, `frontend/src/tests/App.test.tsx`

**Interfaces:**
- `PlanService.list_experiments(limit: int = 20)` / `GET /api/v1/experiments` 返回本机最近实验的 ID、状态、当前 revision 和日期摘要；列表只读，不触发任务。
- 首页加入“已有实验”入口，进入规划或查看监控。DOY 350 通过通用历史列表出现，不硬编码为虚构示例或新建实验。

- [ ] 先写并运行历史列表/API/UI 失败测试，覆盖已有成功实验打开后不会提交运行请求。
- [ ] 实现只读历史入口与使用说明，测试数据库从旧 schema 升级后历史数据/任务数/产物引用不变。
- [ ] 记录基线中已有的未完成 GLM 测试：`test_glm_json_mode_supplies_schema_in_prompt_and_uses_correct_endpoint`、`test_model_deadline_limits_total_request_time` 当前要求尚未实现字段；不得删除测试或把其失败冒称本轮已通过。若仍失败，在最终验收报告明确列出，另行处理模型等待改进。
- [ ] 完整执行 `uv run pytest -q`；记录所有失败名称，不只报告新增测试。
- [ ] 执行 `npm --prefix frontend test -- --run`、`npm --prefix frontend run typecheck`、`npm --prefix frontend run lint`、`npm --prefix frontend run build`。
- [ ] 使用独立临时数据库/运行目录启动模拟端到端流程，验证预览后零进程、确认后模拟解算成功、监控状态/日志/结果可见；禁止使用 `config/local.toml` 启动真实执行。
- [ ] 用可用浏览器工具核对桌面与窄屏：路径换行、XML 滚动、输入清单、确认勾选、按钮禁用、键盘焦点、监控导航；若工具不可用，明确报告无法完成视觉验收。
- [ ] 如需更新本机正式服务，先检查当前 API/Worker 及其子进程和运行任务；没有活动任务后再安全重启，不能重复启动 Worker。仅读取 DOY 350 任务与结果，不调用运行/重试接口。
- [ ] 文档记录验证命令、结果、未解决项、服务更新状态，以及“真实 GREAT 启动次数：0”。不把模拟成功描述为新的真实冒烟通过。

## 完成条件

上述七阶段在网页可见；预览本身不执行，确认授权同时在 API、重试和 Worker 层生效；运行的是复核过的那份 XML；已有 DOY 350 可只读查看。所有验证结果真实记录，真实运行必须等待用户在新的执行预览里明确确认。
