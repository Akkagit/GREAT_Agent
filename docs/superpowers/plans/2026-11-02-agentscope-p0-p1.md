# AgentScope P0 与 P1 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 完成已批准的隔离兼容性验证，并建立可供后续 Agent 使用的计划校验、配置引用、持久提案和调度合同。

**Architecture:** 保留现有 FastAPI/SQLite/Worker 和人工执行授权。P0 在独立依赖环境验证 AgentScope 2.0.11 和 antd 6；P1 在现有主路径统一验证并持久化不可变基线提案，原子领取任务时执行两级配额。

**Tech Stack:** Python 3.11、Pydantic 2、SQLAlchemy 2、Alembic、FastAPI、React 19/TypeScript/Vite；隔离验证目标 AgentScope 2.0.11 与 Ant Design 6。

**Spec:** [2026-11-02 重构方案](/F:/GREAT_Agent/docs/superpowers/specs/2026-11-02-agentscope-ant-design-refactor-design.md)，仅实施 P0/P1。

## Global Constraints

- 当前分支 codex/agentscope-p0-p1；保留全部既有未提交改动，不自动提交、推送或操作生产数据库。
- 输入 GNSS 只读；无人工 XML 复核与 confirmed=true 时，新增 solver task 数为 0。
- P0 使用 scripts/compatibility 下独立依赖声明与 tmp 下环境/输出，不将新框架接入产品主路径。
- 真实模型探测仅用合成输入，显式 --live，最多 8 次请求、每次最多 512 输出 token，整次每请求 deadline 不超过 180 秒；只报告安全元数据，不记录密钥/原始响应/用户计划。
- 新增迁移以当前 Alembic head 为基线；测试只操作 tmp 下数据库和 mock solver。
- 现有三栏、卡片、取消、XML 审阅与重试授权语义保留；P1 只更新前端提案合同，不做 antd 产品迁移。
- 完成 P0/P1 后停在可审阅结果，不自动进入 P2/P3。

## Review Focus

1. 模型生成期间发生修订：候选绑定开始读取的版本，迟到响应不能取得新版本授权。
2. 旧提案编辑：派生继承原基线，修改 expected_revision 或重新发完整候选不能覆盖新修订。
3. 多个领取者竞争最后一个全局/实验槽位：事务内判定并占用，不能先计数后无锁更新。
4. 未注册 profile 和非法日期：创建/提案/接受/冻结均拒绝，不能拖到解算时才发现。
5. 旧数据库升级、API 重建、确认重发：历史和授权不变，已登记提案可恢复，接受不产生重复版本。

## Task 1 P0 隔离兼容性探针

**Files:** 新建 scripts/compatibility/ 下的依赖声明、可复现探针和说明；backend/tests/test_compatibility_probe.py（如存在应用自有协议转换逻辑）；docs/validation/2026-11-02-p0-compatibility.md。不要修改产品 pyproject/uv.lock/frontend package。

**Interfaces:** 探针可通过命令行运行默认离线/模拟模式；--live 读取 AppSettings 模型配置及进程环境密钥，不读取科研数据；输出 JSON 摘要与脱敏证据，明确 installed/unavailable/pass/fail/skipped。

- [x] 在独立 Python 3.11 环境解析/安装精确 AgentScope 2.0.11，锁依赖；核实真实 API 与前期调研是否一致，差异如实记录。
- [x] 用可控 transport/合成工具核查 tools/tool_choice、分片参数、流结束、usage、max_tokens 映射、thinking、4xx/429/5xx、取消和总 deadline；应用探针逻辑先写可失败行为测试。
- [x] 小样本真实兼容探测必须显式开启且有请求预算，永不调用 GREAT 或领域执行 API；缺密钥/包不可获取时分别报告，不伪造通过。用户变量配置后补测 3/3 pass，requests=3，exit 0。
- [x] 在独立 Node fixture 验证 React19 + antd6 及可选 X2 的 peer/install/typecheck/build，锁实际可取得补丁；不将 fixture 冒充产品集成。
- [x] 报告可重现命令、版本、耗时/请求数、风险与切换结论；若实际不可安装目标版本，P0 标为未通过并给出可复核证据，P1 仍继续。

## Task 2 统一领域验证和真实默认 profile

**Files:** 新建 backend/src/great_agent/capabilities/{__init__,profiles}.py、services/plan_validation_service.py、backend/tests/test_plan_validation.py；修改 adapters/great_pppflt.py。PlanService/cli/app 由集成任务接入。

**Interfaces:** `PlanValidationService(*, max_concurrency: int = 8, profiles: ProfileRegistry | None = None)`，`validate(spec: ExperimentSpec) -> ExperimentSpec`；`ProfileRegistry.from_settings(settings: PppfltSettings)` 建立默认 input=local_default、solver=great_local、template=pppflt_base 引用，`validate(spec)` 拒绝未知引用；适配器使用注册配置，不静默忽略 ID。

- [x] 先新增行为测试：非闰年 366、逆序/空最终日期、超策略并发、未知三类 profile 拒绝；合法跨年/重叠去重及 DF/TF 产品规则继续成立。
- [x] 运行新增测试，记录预期 RED。
- [x] 实现 schema 重建 + expand_dates + policy + profile 统一验证；不把输入文件存在性检查放入每次计划编辑。
- [x] 在适配器 preflight/render 等接收 spec 的边界核验/解析已注册默认配置；验证注册的 executable/template/input roots 与实际渲染/预检一致。
- [x] 运行验证/日期/适配器测试，记录 GREEN；报告集成接口，不改 PlanService/cli/app 共享文件。

## Task 3 数据库原子两级调度配额

**Files:** backend/src/great_agent/db/repositories.py、runtime/worker.py、backend/tests/test_scheduler_quotas.py；可更新 test_worker.py 中与新合同冲突的已有假设。

**Interfaces:** `TaskRepository.claim_next(worker_id: str, *, max_concurrency: int | None = None) -> ClaimedTask | None`；Worker 必须传配置全局上限。实验上限从 task 引用的冻结 revision.spec_json concurrency 读取；默认兼容直接 repository 调用，但也必须遵守每实验配额。

- [x] 先写真实 SQLite 行为测试：单实验上限 1 时第二次领取为空；另一个实验可占剩余槽位；全局上限生效；失败/完成/取消后释放；两个独立 repository/线程竞争仅一个占最后槽位。
- [x] 运行 RED；禁止用串行 mock 计数证明并发安全。
- [x] 将配额判定、选择、pending→running、attempt/event 创建放入同一 SQLite 写事务（BEGIN IMMEDIATE 或同等原子 SQL）；等待数据库锁不得计为重复领取。
- [x] 保留状态机、日期排序、attempt 历史与启动前授权；没有授权的领取行为不等于允许启动 GREAT。
- [x] 运行新配额测试及相关 worker/repository 测试，记录 GREEN。

## Task 4 持久提案和服务端基线合同

**Files:** db/models.py、新迁移、services/plan_service.py、services/chat_service.py、api/{chat,experiments,dependencies}.py、app.py、cli.py、backend/tests/test_proposal_persistence.py，以及受合同影响的现有 API/chat 测试。

**Interfaces:** RevisionPreview 新增 base_revision_id（UUID）、base_revision_number（int）、candidate_hash、status、source_proposal_id；`preview_revision(..., source, base_revision_id: UUID | None = None, source_proposal_id: UUID | None = None)` 持久化候选；`get_proposal(experiment_id, proposal_id)` 和 `accept_proposal(experiment_id, proposal_id, *, expected_revision)` 负责读取/接受。内部调用不传基线时可原子读取当前版本；HTTP 手工创建必须显式给基线，模型请求必须传启动时捕获基线。

- [x] 先写失败测试：新服务实例恢复候选；跨实验不能读取/接受；候选期间新修订导致冲突；派生不能改变原基线；客户端换 expected_revision 不能接受旧候选；重复接受返回原修订、不重复新增。
- [x] 引入 plan_proposals 表，包含 base_revision_id、candidate_json/hash、diff/source、status、source_proposal_id、accepted_revision_id、created_at；以当前迁移 head 为 down_revision，兼容应用 create_all 已建表场景。
- [x] 在事务内验证当前版本、提案状态和基线，CAS 接受并记录 accepted_revision_id；普通内部 accept_revision 继续用于测试/服务，但 HTTP 不绕过持久提案。
- [x] Task2 的统一 validator 注入 PlanService；create/preview/accept/freeze 共用；build_app 使用实际 max_concurrency 与配置 registry；增加 PlanValidationError 的稳定 422 响应。
- [x] ChatService 在上游 await 前捕获 revision ID，将其传入候选校验；避免服务层/路由各持久化一份提案。取消完成前不保存候选；及时检查取消/断连，保留旧请求语义。
- [x] 移除 API 对进程内 proposal dict 的依赖，增加 GET 提案列表/详情供刷新恢复；不在 P1 建 conversation/AgentRun 表。
- [x] 验证新/旧数据库升级与已有历史、执行授权不变；只在临时数据库测试迁移，不自动升级用户生产 DB。

## Task 5 旧 UI 合同升级与最终验证

**Files:** frontend/src/api/{types,client}.ts、components/ProposalCard.tsx、pages/ExperimentWorkspace.tsx；相关前端测试；README.md 与 docs/validation/2026-11-02-p0-p1.md。

**Interfaces:** ExperimentView 含 current_revision_id；Proposal 含 base_revision_id/base_revision_number。卡片初建保存所编辑基线，派生发送 source_proposal_id 并继承基线；HTTP 缺基线不能由服务端静默赋最新版。客户端提供 list/getProposals 支持恢复持久 pending 提案。

- [x] 先写前端行为测试：新手工卡片带当前基线；模型卡片编辑带 source_proposal_id；旧卡片遇新 revision 后不能被重新预览“洗新”；刷新后可恢复 pending 卡片；冲突保留当前接受版本。
- [x] 更新客户端/卡片状态与 workspace；不安装 antd 到产品，不改变三栏或确认组件。
- [x] 运行完整 frontend test、typecheck、lint、build，以及完整后端 pytest；使用已验证的共享 TEMP 和必要的沙箱外 asyncio 测试权限。
- [x] 独立审阅本次相对工作区基线的改动；修复重要问题，记录明确的剩余限制。
- [x] 更新 P0/P1 报告、实际版本和验证结果；保留未完成门禁的真实状态，不将 P1 通过称作 AgentScope 主路径已切换。

## 执行记录

任务工作目录：.codex_work/agentscope-p0-p1-20261102。baseline-sha256.json 和 baseline-git-status.txt 在任何产品修改前保存。具体进度、评审和恢复信息写入该目录 progress.md。

## 最终结果

P0/P1完成。P1主任务最终验证：后端231通过（195.34s）、前端64通过（46.98s），typecheck/lint/build通过，独立审阅及fix1复审通过。P0已安装锁定版本并完成离线14项、预算5项、两份Node主检查；用户变量配置后的live补测3/3通过、requests=3、live11.234秒、exit0。首次缺密钥的零请求结果保留为历史；P2/P3尚未开始。详见 docs/validation/2026-11-02-p0-p1.md。
