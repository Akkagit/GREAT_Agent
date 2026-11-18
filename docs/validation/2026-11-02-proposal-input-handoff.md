# 接受提案后自动检查输入：验收记录

状态：实现、独立审查、自动化与浏览器验收完成；已更新本地入口。

范围：用户批准的第三项交互问题。咨询/全局 skill 和模型性能保留后续讨论，本轮未更改其协议或配置。

## 实际行为

- 接受提案成功后，使用接受响应的 revision UUID/number 自动检查一次本地输入。
- 对话原卡片展示检查中、通过、缺失详情、请求失败或过期；缺失和失败可在卡内重试，接受状态保持成功。
- 卡片与底部门禁共用检查状态、处理函数和动作锁；迟到结果不能覆盖新计划、其他实验或已卸载工作区。
- 检查通过才提供人工“生成 XML 并预览”；生成仍由现有服务冻结计划并写快照，进入现有复核页。实际执行仍需勾选并明确确认。
- 窄屏下生成或查看 XML 会自动选择工作区并显示复核页；自动输入检查仍留在对话区域，切换保留未发送草稿。生成期间用户主动切换区域后，迟到结果不会强制导航。
- `/validate` 支持版本绑定请求并返回实际检查的 UUID/number；检查前后校验当前版本；保留旧无 body 调用，检查不写数据库或运行文件。

## 验证

| 检查 | 结果 |
| --- | --- |
| 后端完整回归 | 291 passed，162.15 秒，exit 0 |
| 前端完整回归 | 最终 79 passed，27.17 秒，exit 0 |
| typecheck / lint / production build | 最终全部 exit 0；typecheck/lint 无诊断；1513 modules，构建 8.99 秒 |
| 后端独立审查 | Approved，无 P1/P2 |
| 完整代码审查及窄屏修复复审 | Approved，无未解决 P1/P2 |
| 最终浏览器 | 47/47，4 条合成场景，exit 0，无未捕获异常 |
| 本地入口 | http://127.0.0.1:8765/；health、首页、最终 JS 均 200；版本绑定 API schema 已核对 |

首轮浏览器断言只检查了复核页 DOM 存在，视觉检查发现窄屏下仍停留对话区域。已补充受控分区切换和回归；最终浏览器验收在不手动点击“工作区”的情况下确认 XML 复核页可见。另覆盖显式查看已有 XML、不重复生成、缺失重查、HTTP503 重试、检查中和结束保持对话、草稿保留以及零自动执行。Root 再次查看最终截图确认复核页可见。

最终构建：`index-3osZFYIN.js` / `index-B_dRJBUu.css`。保留原有 Vite 大 bundle 提示，本次未改变依赖或拆包方案。

## 可复核证据

- [浏览器检查结果](2026-11-02-input-handoff-browser.json)
- [接受后检查通过](assets/2026-11-02-input-handoff-passed.png)
- [缺失输入与原地重试](assets/2026-11-02-input-handoff-missing.png)
- [窄屏自动进入复核](assets/2026-11-02-input-handoff-mobile.png)

后端命令：`.venv/Scripts/python.exe -m pytest -p pytest_asyncio.plugin backend -q --tb=line --basetemp tmp/pytest-handoff-backend-final`，禁用无关插件自动装载，TEMP/TMP 位于工作区。

前端命令：`npm --prefix frontend test -- --run`，以及 `run typecheck`、`run lint`、`run build`。浏览器命令：`node .codex_work/proposal-input-handoff-20261102/browser_qa.mjs`。最终各命令实际 exit 0；覆盖变更的 RED/GREEN 记录保存在同目录的 backend/frontend report。

全部自动交互验收使用隔离的合成 API/数据，不访问用户数据库、模型或求解器。本地服务仅启动 API，没有启动 Worker 或计算任务。启动前数据库备份：`data/backups/great_agent-before-input-handoff-20261102-174917.db`。API launcher PID35732，server PID39768；启动时仅通过进程环境传入用户变量中的模型密钥，没有输出或保存密钥。

过程证据与变更前备份保留在 `.codex_work/proposal-input-handoff-20261102/`；未提交或推送。
