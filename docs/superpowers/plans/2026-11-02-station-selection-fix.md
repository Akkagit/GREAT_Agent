# Station Selection Proposal Fix

**Goal:** 修复默认15站计划删除测站时的可复现候选约束冲突，同时提供安全可定位的失败提示。

**Confirmed evidence:** request5326ab8e-ded0-4051-8fb5-80a3f13ad0dc 在模型 schema 阶段失败；用户实验 rev3 是 default_15/15站，首站AHB0。保留default_15删成14站可确定复现校验失败，但原始模型响应未留存，无法确定该次的具体非法字段。

**Scope:** 仅测站选择兼容和失败分类；咨询/全局skill设计仍延期。保留default_15恰好15站约束、全部领域校验、提案基线/接受/自动只读检查/人工XML与执行门禁。不加中文关键词路由，不直接修改用户计划，不运行Worker/求解器，不更换模型或依赖，不提交推送。

## Task 1: backend candidate and safe errors
- [x] 先写失败回归：真实SDK MockTransport返回删除首站的14站候选但继承default_15；生成custom候选且其余字段不变。预览不更新原revision；非法/空/重复/缺字段及未知字段仍拒绝。
- [x] 仅在provider typed校验前：current和candidate均显式default_15，候选包含合法非空不重复的字符串测站数组，规范化后与current不同，复制payload将组名改custom；不补字段、不丢未知字段。已custom保持不变；未改站点保持default_15。prompt/schema说明顺序和预设规则。
- [x] ModelProposalError承载固定类别(schema/invalid_json/timeout/provider)和request_id；ChatService不拼接任意异常文本；app错误响应维持502/model_proposal_error，增加category/request_id，公开文案使用固定中文映射，原始正文/输入/密钥不进入响应或日志。
- [x] 运行覆盖测试和完整backend；独立审查。

## Task 2: frontend failure diagnostics
- [x] 失败先行测试：客户端保留类别/请求编号；聊天失败提示解释校验/超时/连接并显示编号，当前计划不变，恢复pending与取消仍安全。
- [x] ApiError增加可选category/requestId；sendMessage设置明确安全文案及失败progress；ChatPanel显示失败编号。移除不能完成测站移除的误导性“可直接编辑卡片”建议。本次不加手动测站编辑器。
- [x] 完整frontend suite/typecheck/lint/build；独立审查。

## Task 3: integrated acceptance
- [x] 一次真实GLM合成用例（15个合成测站、同一句删除首站输入、最多1请求、45秒deadline、零数据库/执行副作用），与离线回归分别记录；不声称恢复了原始失败响应。
- [x] 重启本项目API并核对最终前端资产/新契约，保留密钥环境注入与历史数据；记录验收。
