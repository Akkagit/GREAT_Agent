# Knowledge chat reading Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans task-by-task.

**Goal:** 实现已批准科研聊天布局/公式/真实检索状态/原文翻译复制，实际Tao验收。
**Architecture:** 现有React/Antd/Markdown增量；只读知识引擎保持S-ID与固定版本校验；翻译独立派生服务；真实阶段元数据投影，不暴露模型内部消息。
**Tech Stack:** existing FastAPI/SQLAlchemy/SQLite/AgentScope/GLM + remark-math/rehype-katex/KaTeX。
**Spec:** docs/superpowers/specs/2026-11-14-knowledge-chat-reading-design.md

## Constraints and ownership

沿用dirty codex checkout、不提交推送、不改正式库/dist；构建tmp/knowledge-chat-reading-dist。保留原4请求/6工具/90s/26000输入字符/12000工具字符上限。翻译45s/单请求4096tokens、原片段最大12000字符。凭据仅服务器。

### Task 1 — 聊天气泡/数学/层次/检索状态UI
Own KnowledgeChat.tsx, MarkdownContent.tsx/.css, KnowledgeWorkspace.tsx/.css, api/knowledgeChat.ts, new knowledge progress helper and tests. Root owns npm manifests/dependency install.
- [ ] TDD紧凑气泡、数学/代码/坏输入、真实阶段和单会话关闭重连。
- [ ] 添加baseName传递与可恢复retrieval summary，结论/引用层次，不改引用身份。
- [ ] focused tests/types/lint/build，独立review。

### Task 2 — 翻译后端与持久派生缓存
Own new services/knowledge_translation_service.py, db/knowledge_translation_models.py/repository.py, api/knowledge_translation.py, migration0008, db/models.py/schema.py, cli/app/dependencies/config wiring and tests. Does not edit existing knowledge_chat service/repo/engine (root).
API POST /api/v1/knowledge/conversations/{cid}/messages/{mid}/citations/{sid}/translation body {target_language:'zh-CN'|'en'} -> {original_text,translated_text,target_language,evidence_text_sha256,model_profile,translation_version,cached}. Constructor KnowledgeTranslationService(repository,model_settings,source_validator=...). source_validator(cid,mid,sid) returns authoritative citation after physical original hash availability; root existing citation verification can be extracted/reused by agent independently.
- [ ] actualSQLite + MockTransport RED, immutable original, single-flight, auth pre/post/cache, bad output/provider timeout/cancel.
- [ ] formula/number token protection, separate call no tools, 0008 backward migration and runtime guards.
- [ ] focused verification and review.

### Task 3 — 引用翻译/复制UI
Own KnowledgeSourcePanel.tsx, new KnowledgeSourcePanel.css, api/knowledgeTranslation.ts and focused tests. Do not edit shared KnowledgeChat/API types/CSS ownedTask1.
- [ ] TDD原文/译文/对照、target choice、复制真实原文/译文/带出处、晚到/取消/停用。
- [ ] use above API, no browser keys, only active excerpt request; clipboard failure actionable.
- [ ] focused tests/types/lint; integrate with Task1 renderer.

### Task 4 — Root progress, formula evidence and integration
Own existing knowledge_chat_repository.py/service.py/engine.py, optional knowledge/formula modules, scripts and dependency manifests.
- [ ] TDD真实retrieval metadata in events/GET detail; snapshot library name/doc counts validated, query results actual, no internal prompt exposure.
- [ ] Inspect actual Tao formula original page and extraction. Implement scoped formula retrieval/recognition with verifiable source mapping; distinguish original identity from transcription qualification; no hardcoded question/answer.
- [ ] improve answer formatting and JSON math escaping; preserve original guards/budgets.
- [ ] wire translation/UI; isolated0008 acceptance DB/config; no model downloads at normal startup.

### Task 5 — Final review and real acceptance
- [ ] appropriate backend/frontend regression, types/lint/isolated build.
- [ ] Tao real GLM formula question, inspect formula against PDF; actual translation and source identity unchanged.
- [ ] real browser desktop/mobile/screenshots, formula KaTeX DOM, retrieval progress, clipboard text exact.
- [ ] independent review, report paths/results/limits, leave8877 usable, preserve formal artifacts.
