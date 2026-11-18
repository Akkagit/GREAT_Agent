"""Render the source-verified migration architecture, without a browser or network.

Run from the repository root: python scripts/architecture/render_technical_architecture.py
Pillow is used only to render this code-native engineering diagram and measure text.
SVG, PNG and the self-contained interactive HTML share the same drawing model.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from xml.etree import ElementTree

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs/architecture"
STEM = "2026-11-02-technical-architecture"
W, H = 2400, 2790
FONT_ROOT = Path("C:/Windows/Fonts")
REGULAR = FONT_ROOT / "msyh.ttc"
BOLD = FONT_ROOT / "msyhbd.ttc"
MONO = FONT_ROOT / "consola.ttf"
COLORS = {
    "A": {"stroke": "#2563b9", "fill": "#f1f6ff", "text": "#1f5595", "name": "已实现 · 当前主路径"},
    "B": {"stroke": "#b47716", "fill": "#fff8e9", "text": "#8a590d", "name": "已实现 / 验证 · 未接入"},
    "C": {"stroke": "#8290a6", "fill": "#f7f8fb", "text": "#5d6b81", "name": "待实现 · 目标架构"},
}
NODES: list[dict] = []


class Drawing:
    def __init__(self) -> None:
        self.svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" aria-label="Agent 应用技术架构与实施状态">',
                    '<title>Agent 应用技术架构与实施状态 · 2026-11-02</title>',
                    '<desc>蓝色为当前代码主路径，琥珀色为隔离验证但未替换，灰色虚线为待实现。绿色 P1 标签表示本轮已替换或补强。并非生产部署状态。</desc>',
                    '<defs><pattern id="grid" width="24" height="24" patternUnits="userSpaceOnUse"><circle cx="1" cy="1" r="0.7" fill="#dfe6f0"/></pattern></defs>']
        self.image = Image.new("RGB", (W, H), "#f5f7fb")
        self.draw = ImageDraw.Draw(self.image)
        self.fonts: dict[tuple, ImageFont.FreeTypeFont] = {}
        self.overflow: list[str] = []
        self.bounds: list[dict] = []

    def font(self, size: int, bold=False, mono=False):
        key = (size, bold, mono)
        if key not in self.fonts:
            self.fonts[key] = ImageFont.truetype(str(MONO if mono else BOLD if bold else REGULAR), size)
        return self.fonts[key]

    def text(self, x, y, value, size=23, fill="#314159", bold=False, mono=False, max_width=None):
        font = self.font(size, bold, mono)
        width = self.draw.textlength(value, font=font)
        if max_width is not None and width > max_width:
            self.overflow.append(f"{value}: {width:.1f} > {max_width}")
        family = "Consolas, monospace" if mono else "'Microsoft YaHei', 'Noto Sans SC', sans-serif"
        self.svg.append(f'<text x="{x}" y="{y}" font-family="{family}" font-size="{size}" font-weight="{700 if bold else 400}" fill="{fill}">{html.escape(value)}</text>')
        self.draw.text((x, y), value, font=font, fill=fill, anchor="ls")
        return width

    def rect(self, x, y, w, h, fill, stroke=None, radius=12, sw=1, dashed=False):
        dash = ' stroke-dasharray="9 6"' if dashed else ""
        self.svg.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{radius}" fill="{fill}" stroke="{stroke or "none"}" stroke-width="{sw}"{dash}/>')
        self.draw.rounded_rectangle((x, y, x+w, y+h), radius, fill, outline=None if dashed else stroke, width=sw)
        if dashed and stroke:
            for start, end in [((x+radius,y),(x+w-radius,y)), ((x+w,y+radius),(x+w,y+h-radius)), ((x+w-radius,y+h),(x+radius,y+h)), ((x,y+h-radius),(x,y+radius))]:
                self.line([start, end], stroke, sw, dashed=True, svg=False)

    def line(self, points, color="#8392a7", width=2, dashed=False, arrow=False, svg=True):
        if svg:
            coords = " ".join(f"{x},{y}" for x, y in points)
            dash = ' stroke-dasharray="8 6"' if dashed else ""
            self.svg.append(f'<polyline points="{coords}" fill="none" stroke="{color}" stroke-width="{width}" stroke-linejoin="round"{dash}/>')
        for (x1,y1),(x2,y2) in zip(points, points[1:]):
            length = ((x2-x1)**2+(y2-y1)**2)**.5
            if dashed and length:
                for start in range(0, int(length), 14):
                    end = min(start+8, length)
                    self.draw.line([(x1+(x2-x1)*start/length, y1+(y2-y1)*start/length), (x1+(x2-x1)*end/length, y1+(y2-y1)*end/length)], fill=color, width=width)
            else:
                self.draw.line(((x1,y1),(x2,y2)), fill=color, width=width)
        if arrow:
            (x1,y1),(x2,y2) = points[-2:]
            length = ((x2-x1)**2+(y2-y1)**2)**.5
            ux,uy=(x2-x1)/length,(y2-y1)/length
            tri=[(x2,y2),(x2-12*ux+5*uy,y2-12*uy-5*ux),(x2-12*ux-5*uy,y2-12*uy+5*ux)]
            self.svg.append(f'<polygon points="{" ".join(f"{x},{y}" for x,y in tri)}" fill="{color}"/>')
            self.draw.polygon(tri, fill=color)

    def pill(self, x, y, value, fill, text_color, size=18):
        width = self.draw.textlength(value, font=self.font(size, True))+24
        self.rect(x,y,width,31,fill,radius=7)
        self.text(x+12,y+23,value,size,text_color,True)
        return width

    def panel(self, x, y, w, h, title, subtitle=""):
        self.rect(x,y,w,h,"#ffffff","#dbe3ee",16,1)
        self.text(x+24,y+34,title,24,"#23334c",True)
        if subtitle:
            self.text(x+w-24-self.draw.textlength(subtitle,font=self.font(18)),y+32,subtitle,18,"#718096")

    def card(self, node_id, status, x, y, w, h, title, lines, phase, detail, sources, mini=None):
        node = dict(id=node_id,status=status,title=title,phase=phase,lines=lines,detail=detail,sources=sources,mini=mini)
        NODES.append(node)
        self.bounds.append(dict(id=node_id,x=x,y=y,w=w,h=h))
        c=COLORS[status]
        self.svg.append(f'<g class="node status-{status}" data-node="{node_id}" tabindex="0" role="button" aria-label="{html.escape(node_id+" "+title,quote=True)}"><title>{html.escape(node_id+" · "+c["name"]+" · "+title+"\n"+detail)}</title>')
        self.rect(x,y,w,h,c["fill"],c["stroke"],12,2,status=="C")
        self.text(x+20,y+29,node_id,20,c["text"],True,True)
        tag_w=self.draw.textlength(phase,font=self.font(17,True))+24
        p1="P1" in phase
        self.pill(x+w-tag_w-16,y+10,phase,"#daf2e6" if p1 else "#e5edfa" if status=="A" else "#f9eac8" if status=="B" else "#e8ecf2","#216647" if p1 else c["text"],17)
        self.text(x+20,y+66,title,25,"#1a2c46",True,max_width=w-40)
        body_top=98 if h < 170 else 105
        for i,value in enumerate(lines):
            self.text(x+20,y+body_top+i*32,value,22,max_width=w-40)
        last_y=y+body_top+(len(lines)-1)*32
        if mini:
            at=mini.get("offset",228)
            self.line([(x+20,y+at),(x+w-20,y+at)],"#c5d4e7",1)
            self.text(x+20,y+at+33,mini["title"],21,c["text"],True,max_width=w-40)
            for i,value in enumerate(mini["lines"]):
                self.text(x+20,y+at+65+i*31,value,21,max_width=w-40)
            last_y=y+at+65+(len(mini["lines"])-1)*31
        if last_y > y+h-14:
            self.overflow.append(f"{node_id}: content bottom {last_y} > {y+h-14}")
        self.svg.append("</g>")


def source(path, line=1):
    if not (ROOT/path).is_file():
        raise FileNotFoundError(path)
    return {"path":path,"line":line}


def build() -> Drawing:
    d=Drawing()
    d.rect(0,0,W,H,"#f5f7fb",radius=0)
    d.svg.append(f'<rect width="{W}" height="{H}" fill="url(#grid)"/>')
    d.rect(72,48,9,66,"#1f5595",radius=3)
    d.text(100,88,"Agent 应用技术架构与实施状态",43,"#152743",True)
    d.text(100,126,"通用控制面 / Agent 编排 / 执行运行时  ·  AgentScope 2.0.11 × React / Ant Design",23,"#627189")
    d.pill(2037,60,"2026-11-02", "#e5ebf5", "#405777",19)
    for x,status,label in [(72,"A","A  已实现 · 当前代码主路径"),(624,"B","B  已实现 / 验证 · 尚未替换"),(1208,"C","C  待实现 · 目标架构")]:
        c=COLORS[status]
        d.rect(x,161,22,22,c["fill"],c["stroke"],4,2,status=="C")
        d.text(x+34,181,label,23,c["text"],True)
    d.pill(1768,155,"P1 已替换 / 补强", "#daf2e6", "#216647",19)
    d.text(72,219,"口径：A 表示当前分支已接入，含保留的旧实现；B 是隔离兼容验证区，并非已部署预发环境；生产部署另行验收。",21,"#596b83")
    d.text(96,264,"当前产品主路径  /  API 与领域内核继续复用",26,"#1f5595",True)
    d.text(1680,264,"后续编排 / 协议 / 可选增强",26,"#5d6b81",True)

    d.panel(72,286,1512,234,"01  界面与客户端  /  Browser")
    d.panel(1656,286,672,234,"可选对话组件")
    d.card("A01","A",96,336,460,164,"React / Ant Design 工作台",[
        "React 19 / antd 6.6.8 / TS / Vite",
        "ConfigProvider / 中文 / tokens / 控件"],"本轮已替换",
        "antd 6.6.8 已进入正式依赖与主路径：计划编辑、提案、聊天、执行复核、监控和历史产物控件采用 antd；中文 locale 与统一 tokens。保留自有分栏的键盘/尺寸持久化语义和 XML 精确纯文本着色。",
        [source("frontend/package.json",14),source("frontend/src/main.tsx"),source("frontend/src/theme.ts"),source("frontend/src/pages/ExperimentWorkspace.tsx")])
    d.card("A02","A",580,336,460,164,"提案交互与状态恢复",[
        "服务端 UUID / 基线版本 / diff",
        "pending 恢复 / 迟到结果隔离"],"P1 已接入",
        "服务端 pending 状态独立于本地草稿；未知、加载、失败均关闭执行前检查入口。接受/拒绝由显式 API 完成。本轮未保存完整聊天记录。",
        [source("frontend/src/pages/ExperimentWorkspace.tsx"),source("frontend/src/api/client.ts")])
    d.card("A03","A",1064,336,496,164,"HTTP / 任务事件客户端",[
        "REST + 每任务 SSE / Last-Event-ID",
        "日志与产物轮询 / 本地聊天草稿"],"既有主路径",
        "当前 SSE 只属于任务监控；事件按序回放，前端主要以 task_status 更新状态。模型回复等待完整候选，不是 token 流式聊天。",
        [source("frontend/src/pages/RunDashboard.tsx",29),source("frontend/src/api/client.ts")])
    d.card("B02","B",1680,336,624,164,"Ant Design X（可选）",[
        "Bubble / Sender 静态 fixture 已验证",
        "当前聊天使用 antd；X 仍未启用"],"可选 · 未接入",
        "已验证的 antd 核心组件已接入 A01。可选 X 仅完成静态 fixture 验证，未加入产品依赖。自有分栏保留键盘语义，Splitter 的进一步适配仍需独立验收；本轮不为引入组件而使用内部 DOM 补丁。",
        [source("scripts/compatibility/node/package.json"),source("docs/validation/2026-11-02-validated-cutover.md")])

    d.line([(1390,520),(1390,565)],"#2563b9",3,arrow=True)
    d.text(1010,551,"REST 命令 / 查询 ↓   任务 SSE ↑",21,"#1f5595")
    d.line([(1992,520),(1992,565)],"#8290a6",2,True,True)
    d.panel(72,565,1512,215,"02  API 与传输  /  FastAPI · Pydantic 2", "/api/v1")
    d.panel(1656,565,672,215,"产品流协议")
    d.card("A04","A",96,615,1464,145,"REST 领域命令 / 查询 + 持久任务事件 SSE",[
        "计划 / 修订 / 提案 / 预检 / 执行预览 / 确认 / 任务启停 / 产物读取",
        "聊天 HTTP 断连取消  ·  409 版本与状态冲突  ·  422 输入错误  ·  task sequence 回放"],"已接入主路径",
        "FastAPI 公开业务 API，执行动作由显式用户确认触发。任务事件来自数据库，支持 Last-Event-ID；完整 AgentRun API、AgentEvent 转换和聚合流尚待实现。",
        [source("backend/src/great_agent/app.py",27),source("backend/src/great_agent/api/experiments.py",78),source("backend/src/great_agent/api/chat.py",34),source("backend/src/great_agent/api/events.py",17)])
    d.card("C02","C",1680,615,624,145,"ProductEvent 协议与流适配",[
        "AgentEvent → ProductEvent → SSE",
        "文本 / 工具 / run 事件 · 聚合 / 恢复 / 退避"],"P2 / P3 待实现",
        "把框架事件适配为稳定产品合同，定义 run 生命周期、取消、游标、重连退避、runtime schema 校验；不把 AgentScope 原始事件直接作为前端合同。",
        [source("docs/superpowers/specs/2026-11-02-agentscope-ant-design-refactor-design.md",217)])
    d.line([(1390,780),(1390,824)],"#2563b9",3,arrow=True)
    d.text(1045,809,"命令 / 查询 / schema 校验",21,"#1f5595")
    d.line([(1992,780),(1992,824)],"#8290a6",2,True,True)

    d.panel(72,824,1512,466,"03  应用服务与可信控制面  /  Domain services")
    d.panel(1656,824,672,466,"Agent 编排层替换")
    d.card("A05","A",96,874,460,390,"ChatService / AgentScope SDK",[
        "当前计划 + 本轮消息 → 完整候选",
        "OpenAIChatModel 2.0.11 / 单请求",
        "JSON object / schema · 非流式",
        "总 deadline / 取消 / 终态门禁"],"SDK 已替换",
        "CLI 正式装配 AgentScopeModelProvider，使用 AgentScope 2.0.11 OpenAIChatModel；旧 provider 名仅作兼容导出。一次物理请求、无重试或结构化 fallback，拒绝/截断/interrupted 不生成提案。网络保留 HTTPX 环境代理与 SOCKS 支持。SDK 文本块仍经 JSON/Pydantic 与 A06 领域校验。完整 Agent 工具循环和会话状态仍属 C03/C05。",
        [source("backend/src/great_agent/services/chat_service.py"),source("backend/src/great_agent/adapters/agentscope_model.py"),source("backend/src/great_agent/adapters/openai_compatible.py"),source("backend/src/great_agent/cli.py",65),source("backend/src/great_agent/config.py",67)],
        {"offset":238,"title":"模型供应商边界","lines":["当前配置：GLM-4.7 兼容端点","TOML 配置 / 进程环境密钥"]})
    d.card("A06","A",580,874,460,390,"PlanService / Validation",[
        "统一 schema / 日期 / 并发校验",
        "持久候选 / diff / 内容摘要",
        "基线 UUID / CAS / 幂等接受",
        "提案状态机 / pending 冻结门禁"],"P1 已替换",
        "PlanValidationService 替换散落校验；数据库 proposals 替换进程字典；候选不可通过改 expected_revision 换绑基线。冻结在同一写事务拒绝 pending。ProfileRegistry 仅绑定三类现有默认配置，尚非通用能力注册表。",
        [source("backend/src/great_agent/services/plan_validation_service.py",22),source("backend/src/great_agent/services/plan_service.py",143),source("backend/src/great_agent/capabilities/profiles.py",45)],
        {"offset":238,"title":"ProfileRegistry · P1 已接入","lines":["输入 / 可执行程序 / 模板三类引用","绑定实际配置 · 拒绝未知 profile"]})
    d.card("A07","A",1064,874,496,390,"ExecutionService",[
        "预检 → 冻结 → 配置快照与预览",
        "快照哈希 / 预览有效期 / 幂等入队",
        "confirmed=true → 执行批准",
        "pending 提案阻断冻结 [P1]"],"既有 + P1 门禁",
        "显式用户确认后创建任务与逐 attempt 授权，Worker 启动前再次复核。程序/模板/XML 采用 SHA256；输入快照只记录路径、大小和 mtime，不是全量输入内容哈希。此处是执行授权，不是登录、RBAC 或 AgentScope 权限系统。",
        [source("backend/src/great_agent/services/execution_service.py",87),source("backend/src/great_agent/runtime/worker.py",88)],
        {"offset":238,"title":"用户确认边界","lines":["接受 / 冻结 / 启停由显式产品 API 触发","模型候选须经服务校验与用户确认"]})
    d.card("C03","C",1680,874,624,390,"AgentRunService + AgentScope 2",[
        "Agent · AgentState · reply_stream",
        "Run deadline / cancel / 幂等 / 会话串行",
        "Toolkit / FunctionTool → 受控服务",
        "Middleware：预算 / 上下文 / 审计"],"P2 待实现",
        "替换 A05 的模型编排层。完整 Agent、Toolkit、AgentState、middleware、Run 生命周期尚未进入正式路径，也不是 P0 已验收原型。工具仅开放只读与候选提议，领域校验及执行授权继续由 A06/A07 拥有。",
        [source("docs/superpowers/specs/2026-11-02-agentscope-ant-design-refactor-design.md",137),source("docs/validation/2026-11-02-p0-compatibility.md",50)],
        {"offset":238,"title":"升级 A05 编排 · 复用 A06 / A07","lines":["只读工具 / 候选工具 · 版本化状态快照","接受、冻结、启停继续走显式产品 API"]})
    d.line([(556,1066),(580,1066)],"#2563b9",2,arrow=True)
    d.line([(1040,1066),(1064,1066)],"#2563b9",2,arrow=True)
    d.line([(1992,1264),(1992,1308),(810,1308),(810,1264)],"#8290a6",2,True,True)
    d.rect(1110,1292,552,28,"#f5f7fb",radius=4)
    d.text(1120,1314,"计划接入：受控工具调用 → 复用领域服务",20,"#627189")
    d.line([(1320,1264),(1320,1344),(326,1344),(326,1370)],"#2563b9",3,arrow=True)
    d.rect(564,1328,608,28,"#f5f7fb",radius=4)
    d.text(574,1350,"当前流程：审阅 / 批准 → 任务入队 → 领取",20,"#1f5595")

    d.panel(72,1370,1512,291,"04  执行运行时  /  独立 Worker 进程", "单机部署")
    d.panel(1656,1370,672,291,"工具与能力扩展")
    d.card("A08","A",96,1420,460,217,"原子调度 / 双层配额",[
        "SQLite BEGIN IMMEDIATE",
        "全局 + 实验配额（冻结版本配置）",
        "claim / attempt / event 同一事务",
        "终态释放槽位"],"P1 已替换",
        "按 experiment_id 统计实验运行数；并发上限取自冻结 revision。领取前同时检查全局与实验配额；占槽、attempt、事件原子提交。请求取消但进程未终止时仍占用。调度领取本身不授予执行权限。",
        [source("backend/src/great_agent/db/repositories.py",130),source("backend/tests/test_scheduler_quotas.py")])
    d.card("A09","A",580,1420,460,217,"Worker / ProcessRunner",[
        "执行批准复核 / 生命周期管理",
        "argv 子进程 / 日志 / 取消 / 重试",
        "stale PID 恢复 / 独立 CLI 进程",
        "当前部署约束：单 Worker"],"既有主路径",
        "Worker 复核批准与快照后启动进程；取消通过持久状态传播。重试须重新审阅、确认并生成 attempt。当前恢复基于 PID，未实现跨主机租约或 fencing。",
        [source("backend/src/great_agent/runtime/worker.py",49),source("backend/src/great_agent/runtime/process_runner.py"),source("backend/src/great_agent/cli.py",201)])
    d.card("A10","A",1064,1420,496,217,"外部程序适配与结果处理",[
        "输入预检 / 配置渲染 / argv 生成",
        "输出检查 / 确定性分析 / 产物登记",
        "现有具体实现：GREAT / GNSS",
        "通用插件注册机制归入后续 C04"],"既有主路径",
        "当前有具体外部计算适配器和确定性后处理，不是通用插件市场或模型解释器。图以技术职责表达，仅把现有领域实现作为适配器示例。",
        [source("backend/src/great_agent/adapters/great_pppflt.py"),source("backend/src/great_agent/runtime/result_inspector.py"),source("backend/src/great_agent/services/result_analysis_service.py")])
    d.card("C04","C",1680,1420,624,217,"能力注册 / 检索与证据",[
        "只读：计划 / 状态 / 日志 / 产物",
        "候选：propose_plan / explain_diff",
        "统一元数据 / I/O schema / 版本与来源",
        "文档检索与证据解释按后续阶段接入"],"P2 / 后续",
        "先包装受控服务工具，再形成能力元数据目录；检索与科研解释属于后续阶段。现有 capabilities 目录仅有 profile，不能据目录名标成完整能力注册表。",
        [source("docs/superpowers/specs/2026-11-02-agentscope-ant-design-refactor-design.md",312),source("backend/src/great_agent/capabilities/profiles.py")])
    d.line([(556,1530),(580,1530)],"#2563b9",2,arrow=True)
    d.line([(1040,1530),(1064,1530)],"#2563b9",2,arrow=True)
    d.line([(1390,1661),(1390,1722)],"#2563b9",3,arrow=True)
    d.text(768,1701,"服务写入事务状态  ·  Worker 写入事件 / 日志 / 产物",21,"#1f5595")
    d.line([(1992,1661),(1992,1722)],"#8290a6",2,True,True)

    d.panel(72,1722,1512,327,"05  持久化与运行证据  /  Repository + File system")
    d.panel(1656,1722,672,327,"Agent 状态与可观测性扩展")
    d.card("A11","A",96,1772,660,253,"SQLite / SQLAlchemy 2 / Alembic",[
        "experiments / plan_revisions / plan_proposals",
        "execution_previews / execution_approvals",
        "daily_tasks / task_attempts / task_events",
        "result_files · WAL / 外键 / 写事务 / CAS"],"既有 + P1 表",
        "完整表名：experiments、plan_revisions、plan_proposals（P1 新增）、execution_previews、execution_approvals、daily_tasks、task_attempts、task_events、result_files。未新增 conversation/message/agent_run 等 Agent 存储。本轮只验证迁移脚本，未升级生产库。",
        [source("backend/src/great_agent/db/models.py",26),source("backend/src/great_agent/db/engine.py",10),source("backend/alembic/versions/0003_plan_proposals.py")])
    d.card("A12","A",780,1772,440,253,"本地文件与产物",[
        "runs / 配置快照 / 进程日志",
        "结果 / 图表 / CSV / manifest",
        "程序 / 模板 / 配置：SHA256",
        "输入：路径 / 大小 / mtime"],"既有主路径",
        "本地 runs 保存不可变配置快照、日志、结果和分析产物；输入检查仍以路径/大小/mtime 为依据。产物清单记录来源，当前没有对象存储或分布式文件管理。",
        [source("backend/src/great_agent/runtime/run_storage.py"),source("backend/src/great_agent/services/execution_service.py",87),source("backend/src/great_agent/services/result_analysis_service.py")])
    d.card("A13","A",1244,1772,316,253,"日志与运行证据",[
        "结构化 ID / 阶段 / 耗时",
        "轮转 API 日志",
        "确定性结果检查",
        "持久任务状态与事件"],"既有主路径",
        "已有结构化业务日志、任务事件和确定性结果证据；尚未形成 Agent 全链路 tracing、TTFT/成本指标与可执行 Agent 评测体系。",
        [source("backend/src/great_agent/runtime/worker.py"),source("docs/validation/2026-11-02-p0-p1.md")])
    d.card("C05","C",1680,1772,624,253,"会话持久化 / Agent 可观测性",[
        "conversation / message / agent_run",
        "tool_call / agent_snapshot / agent_event",
        "AgentState 版本与工具指纹 / 恢复",
        "token / 成本 / TTFT / Trace / Eval"],"P2 / 后续",
        "P2 先建立会话、消息、运行、调用和框架状态存储；完整可观测性、评测、证据解释属于后续阶段。P1 仅持久候选与领域执行状态，不保存完整会话。",
        [source("docs/superpowers/specs/2026-11-02-agentscope-ant-design-refactor-design.md"),source("docs/validation/2026-11-02-p0-p1.md",61)])

    d.panel(72,2102,2256,333,"06  验证与切换依据  /  P0 隔离验证 → 正式代码主路径", "A14 → A05   ·   A15 → A01")
    d.card("A14","A",96,2152,1060,259,"AgentScope 2.0.11 · 模型 SDK 已接入",[
        "P0：独立 Python 3.11 / uv.lock / OpenAIChatModel 兼容探针",
        "历史验证：离线 14/14 + GLM 合成 live 3/3（共 3 请求）",
        "本轮：正式依赖锁定 / CLI 装配 / 单请求 guard / 安全异常",
        "新 provider 实测：单次 11.39 秒，提案有效且合成候选一致",
        "完整 Agent / Toolkit / State 待 C03；领域服务拥有执行批准"],"模型 SDK 已接入",
        "P0 独立模型探针作为历史依据保留。本轮正式调用经 AgentScope SDK，新增真实 SDK MockTransport 回归，并用原HTTP/SOCKS代理环境完成新provider单次真实合成调用（11.39秒，候选完整且一致）。没有据此宣称完整 Agent、严格 schema 或真实分片压力验收。最新接入验收见切换记录。",
        [source("pyproject.toml"),source("backend/src/great_agent/adapters/agentscope_model.py"),source("docs/validation/2026-11-02-validated-cutover.md"),source("docs/validation/2026-11-02-p0-live-result.json")])
    d.card("A15","A",1180,2152,1124,259,"React 19 / antd 6.6.8 · 正式组件已替换",[
        "P0：antd-only / antd+X 两份隔离 fixture 与独立锁文件",
        "本轮：正式 antd 依赖 / 中文 ConfigProvider / 统一主题 tokens",
        "计划 / 提案 / 聊天 / XML 审阅 / 确认 / 监控 / 产物控件迁移",
        "分栏键盘与尺寸存储、XML 精确纯文本实现继续保留",
        "产品等效 skipLibCheck=true；严格声明审计问题仍记录在案"],"antd 已接入",
        "核心 antd 控件已进入正式工作台；可选 X 仍为 B02。保留 pending 恢复、基线 CAS、只在显式确认后执行、三栏内容状态与准确 XML。第三方严格声明审计问题没有通过关闭额外审计而隐藏。",
        [source("frontend/package.json"),source("frontend/src/main.tsx"),source("frontend/src/theme.ts"),source("docs/validation/2026-11-02-validated-cutover.md"),source("docs/validation/2026-11-02-p0-compatibility.md",103)])

    d.panel(72,2482,2256,218,"迁移边界与后续扩展")
    d.text(100,2570,"P2  切换 Agent 编排",25,"#334760",True)
    d.text(100,2612,"A05 → C03；经 C02 输出产品事件",22)
    d.text(100,2648,"复用 A06 / A07，状态写入 C05",22)
    d.line([(808,2544),(808,2676)],"#dbe3ee",2)
    d.text(840,2570,"已完成  核心前端组件替换",25,"#334760",True)
    d.text(840,2612,"A01 已采用 antd；可选 X 留在 B02",22)
    d.text(840,2648,"领域状态和授权继续由服务端拥有",22)
    d.line([(1552,2544),(1552,2676)],"#dbe3ee",2)
    d.card("C06","C",1576,2532,728,147,"按需求扩展的系统能力",[
        "只读专家多 Agent / 多用户身份与权限",
        "PostgreSQL / 分布式 Worker 租约与 fencing"],"后续可选",
        "这是后续可选范围，不是当前已部署架构或 P2/P3 的强制依赖。先完成单 Agent 主路径，再按实际并发、协作与数据需求扩展。",
        [source("docs/superpowers/specs/2026-11-02-agentscope-ant-design-refactor-design.md")])
    d.line([(72,2740),(118,2740)],"#2563b9",3,arrow=True)
    d.text(131,2748,"实线：当前主要控制 / 数据流",20,"#52657e")
    d.line([(652,2740),(698,2740)],"#8290a6",2,True,True)
    d.text(711,2748,"虚线：计划接入 / 替换",20,"#52657e")
    d.text(1248,2748,"依据：P0/P1、切换验收与源码；HTML 可点击模块查看证据。",20,"#52657e")
    return d


HTML_TEMPLATE = r'''<!doctype html>
<html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Agent 技术架构 · 实施状态</title>
<style>
:root{font-family:"Microsoft YaHei",system-ui,sans-serif;color:#20334e;background:#e9eef6}*{box-sizing:border-box}body{margin:0}header{height:78px;display:flex;align-items:center;gap:18px;padding:12px 22px;background:#fff;border-bottom:1px solid #d3deed}h1{font-size:18px;margin:0 0 5px}header p{font-size:12px;color:#667890;margin:0}nav{display:flex;gap:6px;margin-left:auto;flex-wrap:wrap}button{border:1px solid #cdd8e6;border-radius:6px;background:#fff;color:#334b6b;padding:8px 11px;cursor:pointer;font:inherit;font-size:13px}button:hover,button.active{background:#e9f1ff;border-color:#7a9ed3}main{height:calc(100vh - 112px);display:flex}#viewport{flex:1;min-width:0;overflow:hidden;touch-action:none}#viewport>svg{width:100%;height:100%;display:block;cursor:grab;user-select:none}#viewport>svg:active{cursor:grabbing}.node{cursor:pointer}.node:focus{outline:none}.node:hover>rect:first-of-type,.node:focus>rect:first-of-type{stroke-width:4}.node.dimmed{opacity:.15}aside{width:380px;overflow:auto;padding:24px;background:#fff;border-left:1px solid #ccd8e7}aside[hidden]{display:none}aside h2{font-size:20px;line-height:1.55;margin:14px 0}aside p{font-size:14px;line-height:1.9;color:#475c76}aside .badge{display:inline-block;font-size:12px;font-weight:bold;padding:6px 8px;border-radius:6px;background:#edf2f8}aside h3{font-size:13px;margin:25px 0 10px}aside a{font-size:12px;line-height:1.8;color:#235a9e;display:block;overflow-wrap:anywhere;margin-bottom:10px}#close{float:right}footer{height:34px;padding:8px 22px;background:#fff;border-top:1px solid #d3deed;font-size:12px;color:#62738c}kbd{font-size:11px;border:1px solid #ccc;border-radius:3px;padding:1px 3px}@media(max-width:900px){header{height:126px;align-items:flex-start;flex-direction:column;gap:8px}nav{margin:0}main{height:calc(100vh - 160px)}aside{position:absolute;right:0;top:126px;bottom:34px;width:min(380px,90vw);box-shadow:-8px 0 24px #1b355522}header p{font-size:11px}}
</style>
<header><div><h1>Agent 技术架构 · 实施状态</h1><p>2026-11-02  /  当前代码接入与未来替换边界  /  点击模块查看源码依据</p></div><nav aria-label="图形工具"><button id="fitwidth">适宽</button><button id="fitall">全图</button><button id="minus" aria-label="缩小">−</button><button id="plus" aria-label="放大">＋</button><button data-filter="all" class="active">全部</button><button data-filter="A">已接入</button><button data-filter="B">隔离验证</button><button data-filter="C">待实现</button></nav></header>
<main><div id="viewport">__SVG__</div><aside id="inspector" hidden aria-label="模块详情"><button id="close">关闭</button><span class="badge" id="badge"></span><h2 id="name"></h2><p id="detail"></p><h3>源码 / 验收依据</h3><div id="sources"></div></aside></main>
<footer>滚轮缩放 · 拖动平移 · 点击模块看依据 · <kbd>Esc</kbd> 关闭详情。蓝色含原有实现；绿色 P1 标签表示本轮替换或补强。隔离验证区不等于已部署预发。</footer>
<script>
const nodes=__NODES__;
const statuses={A:'已实现 · 当前代码主路径',B:'已实现 / 验证 · 尚未替换',C:'待实现 · 目标架构'};
const viewport=document.querySelector('#viewport'),svg=viewport.querySelector('svg'),panel=document.querySelector('#inspector');
let box={x:0,y:0,w:2400,h:2790},drag=null;
function update(){svg.setAttribute('viewBox',`${box.x} ${box.y} ${box.w} ${box.h}`);svg.setAttribute('preserveAspectRatio','none')}
function fitWidth(){const r=viewport.getBoundingClientRect();box={x:0,y:0,w:2400,h:2400*r.height/r.width};update()}
function fitAll(){const r=viewport.getBoundingClientRect(),scale=Math.max(2400/r.width,2790/r.height);box={x:(2400-r.width*scale)/2,y:(2790-r.height*scale)/2,w:r.width*scale,h:r.height*scale};update()}
function zoom(factor,cx=.5,cy=.5){const newW=Math.max(500,Math.min(4200,box.w*factor)),ratio=newW/box.w;box={x:box.x+box.w*cx*(1-ratio),y:box.y+box.h*cy*(1-ratio),w:newW,h:box.h*ratio};update()}
document.querySelector('#fitwidth').onclick=fitWidth;document.querySelector('#fitall').onclick=fitAll;document.querySelector('#minus').onclick=()=>zoom(1.2);document.querySelector('#plus').onclick=()=>zoom(1/1.2);
viewport.addEventListener('wheel',e=>{e.preventDefault();const r=viewport.getBoundingClientRect();zoom(Math.exp(e.deltaY*.0012),(e.clientX-r.left)/r.width,(e.clientY-r.top)/r.height)},{passive:false});
viewport.addEventListener('pointerdown',e=>{if(e.button!==0)return;drag={x:e.clientX,y:e.clientY,startX:e.clientX,startY:e.clientY,moved:false,id:e.pointerId,target:e.target};viewport.setPointerCapture(e.pointerId)});
viewport.addEventListener('pointermove',e=>{if(!drag)return;const r=viewport.getBoundingClientRect();box.x-=(e.clientX-drag.x)*box.w/r.width;box.y-=(e.clientY-drag.y)*box.h/r.height;drag.x=e.clientX;drag.y=e.clientY;if(Math.hypot(e.clientX-drag.startX,e.clientY-drag.startY)>5)drag.moved=true;update()});
viewport.addEventListener('pointerup',()=>{if(drag&&!drag.moved){const el=drag.target.closest('[data-node]');if(el)inspect(el.dataset.node)}drag=null});viewport.addEventListener('pointercancel',()=>drag=null);
function inspect(id){const n=nodes.find(n=>n.id===id);if(!n)return;document.querySelector('#badge').textContent=`${n.id} · ${statuses[n.status]} · ${n.phase}`;document.querySelector('#name').textContent=n.title;document.querySelector('#detail').textContent=n.detail;const list=document.querySelector('#sources');list.replaceChildren();for(const s of n.sources){const a=document.createElement('a');a.textContent=`${s.path}:${s.line}`;a.href='../../'+s.path;a.target='_blank';a.rel='noopener';list.append(a)}panel.hidden=false;const r=viewport.getBoundingClientRect();box.h=box.w*r.height/r.width;update()}
function closePanel(){panel.hidden=true;const r=viewport.getBoundingClientRect();box.h=box.w*r.height/r.width;update()}
document.querySelector('#close').onclick=closePanel;document.addEventListener('keydown',e=>{if(e.key==='Escape')closePanel();const el=e.target.closest?.('[data-node]');if(el&&(e.key==='Enter'||e.key===' ')){e.preventDefault();inspect(el.dataset.node)}});
for(const b of document.querySelectorAll('[data-filter]'))b.onclick=()=>{document.querySelectorAll('[data-filter]').forEach(v=>v.classList.toggle('active',v===b));document.querySelectorAll('[data-node]').forEach(n=>n.classList.toggle('dimmed',b.dataset.filter!=='all'&&!n.classList.contains('status-'+b.dataset.filter)))};
window.addEventListener('resize',()=>{const r=viewport.getBoundingClientRect();box.h=box.w*r.height/r.width;update()});fitWidth();
</script></html>'''


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    d=build()
    if d.overflow:
        raise SystemExit("Text bounds failed:\n"+"\n".join(d.overflow))
    for b in d.bounds:
        assert 0 <= b['x'] < b['x']+b['w'] <= W
        assert 0 <= b['y'] < b['y']+b['h'] <= H
    for i,a in enumerate(d.bounds):
        for b in d.bounds[i+1:]:
            assert not (a['x'] < b['x']+b['w'] and b['x'] < a['x']+a['w'] and a['y'] < b['y']+b['h'] and b['y'] < a['y']+a['h']),f"Card overlap {a['id']}/{b['id']}"
    svg="\n".join(d.svg+["</svg>"])
    ElementTree.fromstring(svg)
    (OUT/f"{STEM}.svg").write_text(svg,encoding="utf-8")
    d.image.save(OUT/f"{STEM}.png",optimize=True)
    document=HTML_TEMPLATE.replace("__SVG__",svg).replace("__NODES__",json.dumps(NODES,ensure_ascii=False))
    (OUT/f"{STEM}.html").write_text(document,encoding="utf-8")
    (OUT/f"{STEM}.json").write_text(json.dumps({"date":"2026-11-02","scope":"Current branch source and isolated P0 evidence, not deployment status","statuses":COLORS,"nodes":NODES},ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    counts={s:sum(n['status']==s for n in NODES) for s in COLORS}
    print(json.dumps({"nodes":counts,"canvas":[W,H],"text_overflow":len(d.overflow),"source_files":len({s['path'] for n in NODES for s in n['sources']}),"outputs":[str(OUT/f"{STEM}.{e}") for e in ('svg','png','html','json')]},ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
