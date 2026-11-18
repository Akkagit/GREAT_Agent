# 删除首站提案修复验收

状态：修复、回归、真实 GLM 合成验证、独立审查和本地服务更新完成。

## 定位与修复

原请求在 model_request/schema 阶段被拒绝，约19.99秒。只读查询确认当时计划为default_15/15站；保留组名将测站列表改为14站，可确定复现“default_15 requires exactly 15 stations”。原始模型回复未保存，不能据此断言该次实际非法字段。

保留默认15站预设的严格领域约束。在模型候选进入typed校验前，仅当当前与候选都显式继承default_15、候选有合法非空不重复的字符串测站数组且规范化后确实变化时，复制候选并将分组元数据转custom。其余字段和列表顺序不改，不补必填字段、不删除未知字段；随后完整校验。提示词和schema同时说明预设与有序测站快照规则。

模型失败仍为HTTP502/model_proposal_error，现在带固定类别和API生成的请求编号；公开文案不拼接任意异常或模型正文。浏览器仅接受白名单类别及合法UUID，在对话区显示分类说明和编号。移除了对测站操作无效的“可以直接编辑计划卡片”失败建议。

本轮没有修改咨询/全局skill路由，没有新增关键词路由或手动测站编辑器。现有提案基线、人工接受、接受后自动只读检查、手动XML复核、明确执行批准及取消/移动分区保护保留。没有直接改动用户计划或启动Worker/求解器。

## 验收结果

| 验证 | 结果 |
| --- | --- |
| 后端完整回归 | 312 passed，179.53秒，exit0 |
| 后端覆盖组 | 129 passed，含21项新增回归 |
| 前端完整回归 | 93 passed，7 files；typecheck/lint/build均exit0 |
| 独立代码审查 | Approved，无P1/P2 |
| 真实GLM合成验证 | 1次请求，16.303秒，准确移除首站为14站/custom，其余站点顺序及全部其他参数保持 |
| 本地服务 | health/首页/最终JS均200；新版测站schema说明已核对 |

真实验证使用15个合成站及同一句“我想要去掉第一个测站”，当前产品AgentScopeModelProvider/GLM-4.7、最多1请求、45秒deadline、max_tokens2048，保留原环境代理。没有打开数据库、检查用户输入文件、生成XML或运行工具。[合成验证结果](2026-11-02-station-selection-live.json)

最终入口：http://127.0.0.1:8765/ 。构建为`index-CByM6sri.js` / `index-B_dRJBUu.css`。API launcher PID43400、server PID43732；仅传入进程环境密钥，没有打印或落盘密钥。原有Vite大bundle提示保留。

重启前一致性备份：`data/backups/great_agent-before-station-selection-20261102-191539.db`。进程重启前校验完整API进程归属，跳过Windows控制台宿主，只停止本项目API进程链；没有启动Worker。

实际命令、RED/GREEN、原失败日志副本、基线diff及审查保留在`.codex_work/station-selection-fix-20261102/`。后端完整命令使用显式pytest_asyncio插件并禁用无关插件自动装载；前端运行完整suite/typecheck/lint/build。无提交或推送。
