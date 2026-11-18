# GREAT Agent 功能演示 Implementation Plan

**Goal:** 按用户最新反馈，以实际前端UI交付科研组会演示，并提供可离线回放的单文件HTML。
**Architecture:** 当前frontend/dist + 隔离后端真实服务 + 离线模型/solver fixture；真实页面截图及控件坐标打包成HTML。
**Spec:** docs/superpowers/specs/2026-11-15-functional-demo-design.md

- [x] 核对当前功能与边界；根据反馈改用实际UI。
- [x] 创建隔离演示启动器，复用真实提案、执行门禁、Worker、分析和知识服务。
- [x] 集成测试验证隔离数据、未确认不可运行、提案待审、三图与引用。
- [x] 实际浏览器完成实验/结果/文献流程，采集26帧截图与97个真实控件热点；13项流程检查通过。
- [x] 打包离线单文件回放（5,624,144 bytes，无构建警告）。
- [x] 独立验收导航、热点、图片与布局：287/287通过，26帧与原截图字节一致，97热点全量点击通过，无JS错误或网络请求。
- [x] 交付真实UI演示入口、离线HTML与使用说明，不更换正式页面；原演示HTML入口同步新版。

验收材料：`output/great-agent-ui-demo/qa/qa-report.json`、`captures/capture-verification.json`、`verification.json`。隔离后端最终集成测试1 passed，真实前端66文件SHA逐个一致；本机演示地址http://127.0.0.1:8879/。

用户后续反馈清晰度问题：

- [x] 复现DPR1整屏位图在高分屏上的源像素不足，保存baseline证据。
- [x] 验证实际DOM/CSS离线原生重建、可选文字与精确滚动；修复CSSOM序列化丢失checkbox边框，保留原始CSS。
- [x] 对照验证iframe缩放：transform保持1280×800与滚动位置，避免CSS zoom的1px视口量化偏差。
- [x] 全26帧原生DOM/CSS、2x画布及源图重新采集。
- [x] 打包并验收原生文字、97热点、DPR1/2与三种桌面分辨率；574项完整检查通过，原始CSS保留checkbox白勾。
- [x] 更新原入口与使用说明，新增clear入口，主文件使用原生DOM/CSS回放。
