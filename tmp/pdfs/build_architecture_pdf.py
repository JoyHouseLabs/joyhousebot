from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Flowable,
    Frame,
    HRFlowable,
    KeepTogether,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "output/pdf/joyhousebot-architecture.pdf"
OUT.parent.mkdir(parents=True, exist_ok=True)

# macOS ships a TrueType Chinese font collection that ReportLab can embed.
# The user font directory often contains CFF OpenType files, which ReportLab
# cannot parse, so prefer the system TTC explicitly.
FONT = Path("/System/Library/Fonts/STHeiti Light.ttc")
FONT_BOLD = Path("/System/Library/Fonts/STHeiti Medium.ttc")
pdfmetrics.registerFont(TTFont("NotoSC", str(FONT), subfontIndex=0))
pdfmetrics.registerFont(TTFont("NotoSC-Bold", str(FONT_BOLD), subfontIndex=0))

PAGE_W, PAGE_H = A4
ORANGE = colors.HexColor("#F26A2E")
INK = colors.HexColor("#1F2937")
MUTED = colors.HexColor("#657184")
LINE = colors.HexColor("#D9E0E8")
PALE = colors.HexColor("#F5F7FA")
BLUE = colors.HexColor("#2D6CDF")
GREEN = colors.HexColor("#18A874")


class ArchitectureDoc(BaseDocTemplate):
    def __init__(self, filename: str):
        super().__init__(filename, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                         topMargin=18 * mm, bottomMargin=17 * mm, title="joyhousebot 当前架构与实现说明")
        frame = Frame(self.leftMargin, self.bottomMargin, self.width, self.height, id="main")
        self.addPageTemplates([PageTemplate(id="normal", frames=frame, onPage=draw_page)])


def draw_page(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(LINE)
    canvas.setLineWidth(0.5)
    canvas.line(doc.leftMargin, 12 * mm, PAGE_W - doc.rightMargin, 12 * mm)
    canvas.setFont("NotoSC", 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(doc.leftMargin, 7 * mm, "joyhousebot | 当前架构与实现说明 | 2026-08")
    canvas.drawRightString(PAGE_W - doc.rightMargin, 7 * mm, f"{doc.page}")
    canvas.restoreState()


class FlowDiagram(Flowable):
    def __init__(self, rows, width=170 * mm, height=None):
        super().__init__()
        self.rows = rows
        self.width = width
        self.height = height or (len(rows) * 18 * mm + 8 * mm)

    def draw(self):
        c = self.canv
        row_h = 14 * mm
        gap = 4 * mm
        box_w = self.width / 3 - 5 * mm
        for i, row in enumerate(self.rows):
            y = self.height - (i + 1) * (row_h + gap) + gap
            for j, (label, tone) in enumerate(row):
                x = j * (box_w + 7.5 * mm)
                c.setFillColor(tone)
                c.setStrokeColor(tone)
                c.roundRect(x, y, box_w, row_h, 4 * mm, fill=1, stroke=0)
                c.setFillColor(colors.white)
                c.setFont("NotoSC-Bold", 9)
                c.drawCentredString(x + box_w / 2, y + row_h / 2 - 3, label)
                if j < len(row) - 1:
                    c.setStrokeColor(MUTED)
                    c.setLineWidth(1)
                    c.line(x + box_w + 1 * mm, y + row_h / 2, x + box_w + 6.2 * mm, y + row_h / 2)
                    c.line(x + box_w + 5.2 * mm, y + row_h / 2 + 1.2 * mm, x + box_w + 6.2 * mm, y + row_h / 2)
                    c.line(x + box_w + 5.2 * mm, y + row_h / 2 - 1.2 * mm, x + box_w + 6.2 * mm, y + row_h / 2)


styles = getSampleStyleSheet()
styles.add(ParagraphStyle(name="CoverTitle", fontName="NotoSC-Bold", fontSize=28, leading=37, textColor=INK, alignment=TA_LEFT, spaceAfter=8))
styles.add(ParagraphStyle(name="CoverSub", fontName="NotoSC", fontSize=13, leading=21, textColor=MUTED, spaceAfter=12))
styles.add(ParagraphStyle(name="H1x", fontName="NotoSC-Bold", fontSize=19, leading=27, textColor=INK, spaceBefore=4, spaceAfter=8))
styles.add(ParagraphStyle(name="H2x", fontName="NotoSC-Bold", fontSize=13, leading=19, textColor=INK, spaceBefore=10, spaceAfter=5))
styles.add(ParagraphStyle(name="Bodyx", fontName="NotoSC", fontSize=9.6, leading=16, textColor=INK, spaceAfter=5))
styles.add(ParagraphStyle(name="Smallx", fontName="NotoSC", fontSize=8.2, leading=13, textColor=MUTED, spaceAfter=3))
styles.add(ParagraphStyle(name="Bulletx", fontName="NotoSC", fontSize=9.4, leading=15, leftIndent=12, firstLineIndent=-8, textColor=INK, spaceAfter=3))
styles.add(ParagraphStyle(name="CodeX", fontName="NotoSC", fontSize=7.8, leading=12, textColor=colors.HexColor("#334155"), leftIndent=8, rightIndent=8, spaceBefore=4, spaceAfter=7))
styles.add(ParagraphStyle(name="Kicker", fontName="NotoSC-Bold", fontSize=8, leading=12, textColor=ORANGE, tracking=1.5, spaceAfter=5))


def P(text, style="Bodyx"):
    return Paragraph(text, styles[style])


def bullets(items):
    return [P("• " + x, "Bulletx") for x in items]


def table(rows, widths=None, header=True):
    t = Table(rows, colWidths=widths, repeatRows=1 if header else 0, hAlign="LEFT")
    commands = [
        ("GRID", (0, 0), (-1, -1), 0.4, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]
    if header:
        commands += [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EDF1F5")),
                     ("TEXTCOLOR", (0, 0), (-1, 0), INK),
                     ("FONTNAME", (0, 0), (-1, 0), "NotoSC-Bold")]
    t.setStyle(TableStyle(commands))
    return t


story = []

# Cover
story += [Spacer(1, 28 * mm), P("JOYHOUSEBOT", "Kicker"), P("当前架构与实现说明", "CoverTitle"),
          P("面向多用户、分布式 Agent 云服务的 PG-first 运行时框架", "CoverSub"),
          HRFlowable(width="100%", thickness=1.2, color=ORANGE, spaceBefore=8, spaceAfter=18),
          P("本文档基于当前代码库与线上部署形态整理，描述实际已经存在的组件、数据流、职责边界、扩展方式和已知边界。它不是理想化设计稿，而是当前实现的架构基线。", "Bodyx"),
          Spacer(1, 12 * mm),
          FlowDiagram([[('HTTP API', BLUE), ('Coordinator', ORANGE), ('PostgreSQL', GREEN)],
                       [('Channel', BLUE), ('Scheduler / Worker', ORANGE), ('Plugin / Tool', GREEN)],
                       [('Web Console', BLUE), ('Trace / Event', ORANGE), ('Memory', GREEN)]]),
          Spacer(1, 23 * mm), P("版本：PG-first distributed runtime", "Smallx"),
          P("更新时间：2026 年 8 月", "Smallx"), PageBreak()]

# 1
story += [P("01 设计定位与核心原则", "H1x"),
          P("joyhousebot 已从单机 Agent 形态收敛为一个可承载多个用户、多个会话、多个并发任务的 Agent 服务框架。核心不是某一个具体业务 Agent，而是把请求接入、意图协调、能力调用、异步执行、状态持久化和全过程追踪标准化。", "Bodyx"),
          table([[P("原则", "Smallx"), P("当前实现", "Smallx")],
                 [P("PG-first", "Bodyx"), P("PostgreSQL 是 Run、Task、事件、Trace、记忆、租约和 Channel Outbox 的共享权威。", "Bodyx")],
                 [P("单一运行模型", "Bodyx"), P("HTTP、Web UI、Channel 最终都转换为统一 Run，不维护两套 Agent 执行链。", "Bodyx")],
                 [P("主 Agent 协调", "Bodyx"), P("Coordinator 负责意图、场景、追问和计划；具体业务能力由 Tool、Skill、Plugin 承担。", "Bodyx")],
                 [P("可解释执行", "Bodyx"), P("阶段事件、决策摘要、工具调用、耗时和产物均可查询与回放。", "Bodyx")],
                 [P("业务解耦", "Bodyx"), P("dinq-discover 作为 dinq-plugin 接入，不把业务代码写入 joyhousebot 核心。", "Bodyx")]], widths=[37 * mm, 130 * mm]),
          P("当前系统的主要取舍", "H2x"),
          *bullets(["优先保证可恢复、可观测和可扩展，再优化单次请求的最低延迟。", "所有长任务使用持久化 Run，短任务也沿用同一模型，以降低系统复杂度。", "模型原始隐藏推理不作为公共接口契约；对外展示结构化思考摘要和执行事件。"]), PageBreak()]

# 2
story += [P("02 总体分层与组件职责", "H1x"),
          FlowDiagram([[('接入层', BLUE), ('协调层', ORANGE), ('执行层', GREEN)],
                       [('HTTP / UI', BLUE), ('Coordinator', ORANGE), ('Scheduler', GREEN)],
                       [('Channel', BLUE), ('Scenario / DAG', ORANGE), ('Worker / Task', GREEN)],
                       [('Admin API', BLUE), ('Capability Registry', ORANGE), ('Tool / Skill / Plugin', GREEN)]]),
          Spacer(1, 4 * mm),
          table([[P("层", "Smallx"), P("职责", "Smallx"), P("主要代码区域", "Smallx")],
                 [P("接入层", "Bodyx"), P("用户认证、会话、Run 提交、SSE、Channel 消息接入。", "Bodyx"), P("joyhousebot/api、joyhousebot/channels", "CodeX")],
                 [P("协调层", "Bodyx"), P("意图识别、场景匹配、追问判断、能力选择、执行计划。", "Bodyx"), P("joyhousebot/runtime/request_coordination.py", "CodeX")],
                 [P("调度层", "Bodyx"), P("入队、领取、Lease、重试、超时、恢复、通知唤醒。", "Bodyx"), P("joyhousebot/runtime、joyhousebot/storage", "CodeX")],
                 [P("能力层", "Bodyx"), P("工具、Skill、Scenario、Plugin 的发现、校验、调用和结果规范化。", "Bodyx"), P("joyhousebot/capabilities、dinq-plugin", "CodeX")],
                 [P("状态层", "Bodyx"), P("PostgreSQL 表、事件、Trace Blob、Memory、Outbox。", "Bodyx"), P("joyhousebot/storage、joyhousebot/agent", "CodeX")],
                 [P("控制台", "Bodyx"), P("运行监控、配置、Agent 试用、场景模拟和插件运维。", "Bodyx"), P("frontend/", "CodeX")]], widths=[27 * mm, 86 * mm, 54 * mm]), PageBreak()]

# 3
story += [P("03 一次请求的完整执行链", "H1x"),
          P("每条用户消息都以 user_id、session_id 和 agent_id 作为基础身份上下文，创建一个可持久化 Run。短请求和长任务使用相同生命周期，区别只在执行时长、超时策略和交付方式。", "Bodyx"),
          FlowDiagram([[('请求接收', BLUE), ('创建 Run', ORANGE), ('写入 PostgreSQL', GREEN)],
                       [('PG NOTIFY', BLUE), ('Scheduler 唤醒', ORANGE), ('Worker 领取', GREEN)],
                       [('意图 / 场景', BLUE), ('计划 / 子任务', ORANGE), ('Tool 执行', GREEN)],
                       [('验证结果', BLUE), ('保存产物', ORANGE), ('SSE / Channel 返回', GREEN)]]),
          P("调度细节", "H2x"),
          *bullets(["SKIP LOCKED 是唯一任务竞争裁决；PG NOTIFY 只负责低延迟唤醒，不负责一致性。", "正常空闲 Worker 时，入队到领取目标是几十毫秒级；轮询仅作为 100-250ms 可配置的容灾兜底。", "事件中记录 wake_source、queue_wait_ms、claim_latency_ms，可区分通知漏唤醒、无 Worker 容量和数据库延迟。", "Worker 使用租约和心跳执行任务，异常退出后由恢复流程重新开放或标记失败。"]),
          P("Run 状态示例", "H2x"),
          table([[P("状态", "Smallx"), P("含义", "Smallx"), P("下一步", "Smallx")],
                 [P("queued", "CodeX"), P("已接受并等待执行", "Bodyx"), P("Scheduler / Worker 领取", "Bodyx")],
                 [P("running", "CodeX"), P("正在协调或执行 Task", "Bodyx"), P("继续执行、重试或等待子任务", "Bodyx")],
                 [P("waiting_input", "CodeX"), P("需要用户补充场景要求", "Bodyx"), P("用户回复后恢复同一会话", "Bodyx")],
                 [P("completed / failed", "CodeX"), P("已完成或不可恢复失败", "Bodyx"), P("展示结果、事件和诊断", "Bodyx")]], widths=[35 * mm, 75 * mm, 57 * mm]), PageBreak()]

# 4
story += [P("04 Coordinator、场景与追问 DAG", "H1x"),
          P("Coordinator 是对外服务的主 Agent，负责把自然语言请求转换为可验证、可执行的计划。它不直接承载 dinq-discover 的全部业务逻辑，而是消费已注册的能力。", "Bodyx"),
          FlowDiagram([[('用户请求', BLUE), ('意图识别', ORANGE), ('场景匹配', GREEN)],
                       [('字段抽取', BLUE), ('追问 DAG', ORANGE), ('能力边界校验', GREEN)],
                       [('执行计划', BLUE), ('并发 Task', ORANGE), ('结果合并', GREEN)]]),
          P("场景配置包含", "H2x"),
          *bullets(["场景 ID、名称、版本、说明和启用状态。", "触发条件、必填字段、字段类型和默认值。", "追问节点、问题顺序、跳转条件和最大追问次数。", "允许的 Tool、Skill、Plugin、并发策略和结果规范。", "模拟输入、预期路由和测试结果，供业务人员在控制台验证。"]),
          P("以 dinq-discover 为例", "H2x"),
          P("推荐保留一个 Main Coordinator。它负责判断是人才搜索、候选人丰富、平台搜索还是通用问答，再调用 dinq-plugin 中的搜索工具和 Skill。场景命中后，Coordinator 可以先补充地点、岗位、经验等必要条件，再启动并发搜索和结果合并。", "Bodyx"), PageBreak()]

# 5
story += [P("05 Channel 外部消息接入层", "H1x"),
          P("Channel 是消息系统适配层，不是另一套 Agent Runtime。所有 Channel 消息都会被规范化为统一的 user_id、session_id、agent_id 和 Run。", "Bodyx"),
          FlowDiagram([[('Telegram / Slack', BLUE), ('Channel Plugin', ORANGE), ('RunAdapter', GREEN)],
                       [('飞书 / 钉钉', BLUE), ('ChannelManager', ORANGE), ('统一 Run', GREEN)],
                       [('Webhook / Email', BLUE), ('Lease + Outbox', ORANGE), ('原渠道回复', GREEN)]]),
          table([[P("组件", "Smallx"), P("作用", "Smallx")],
                 [P("Channel Plugin", "Bodyx"), P("实现外部平台的连接、消息解析、发送和平台特有能力。", "Bodyx")],
                 [P("ChannelManager", "Bodyx"), P("加载启用的渠道，维护连接、Lease 和发送 Worker。", "Bodyx")],
                 [P("ChannelRuntimeBridge", "Bodyx"), P("将入站消息转换为 Run，并将终态结果路由回原消息。", "Bodyx")],
                 [P("channel_outbox", "Bodyx"), P("持久化出站消息，支持重试、幂等和多进程投递。", "Bodyx")],
                 [P("RunAdapter", "Bodyx"), P("定义 Channel 与 Runtime 之间的统一适配契约。", "Bodyx")]], widths=[50 * mm, 117 * mm]),
          P("当前状态", "H2x"), P("Channel 是可选能力。配置为空时 API 和 Web UI 仍可正常工作，日志中的 No channels enabled 只表示没有启动外部消息连接器。当前适配器仍随核心包内置，但 ChannelPlugin、RunAdapter 和 ChannelRuntimeBridge 已形成独立边界；未来可拆成 joyhousebot-channel-* 包，不改变统一 Run/Task 契约。", "Bodyx"), PageBreak()]

# 6
story += [P("06 Memory 记忆层", "H1x"),
          P("Memory 是可按 Agent 配置启用的用户个性化能力。运行时以 PostgreSQL 为唯一权威，MEMORY.md 等名称只是逻辑文档路径和导入导出格式，不再依赖本地文件。", "Bodyx"),
          FlowDiagram([[('user_id', BLUE), ('scope_key', ORANGE), ('memory_documents', GREEN)],
                       [('Agent Policy', BLUE), ('分层读写', ORANGE), ('Context Builder', GREEN)],
                       [('Profile', BLUE), ('Long-term', ORANGE), ('Episodic / Session', GREEN)]]),
          table([[P("记忆层", "Smallx"), P("用途", "Smallx"), P("默认特点", "Smallx")],
                 [P("working", "Bodyx"), P("当前执行过程临时上下文", "Bodyx"), P("读，不持久化", "Bodyx")],
                 [P("session", "Bodyx"), P("当前会话上下文", "Bodyx"), P("随会话持久化", "Bodyx")],
                 [P("episodic", "Bodyx"), P("历史事件和每日记录", "Bodyx"), P("可追加、可裁剪", "Bodyx")],
                 [P("profile", "Bodyx"), P("用户个人属性和偏好", "Bodyx"), P("按用户隔离", "Bodyx")],
                 [P("long_term", "Bodyx"), P("稳定事实和长期知识", "Bodyx"), P("按用户和 Agent 隔离", "Bodyx")],
                 [P("agent", "Bodyx"), P("Agent 自身知识和配置", "Bodyx"), P("默认不向用户上下文暴露", "Bodyx")]], widths=[31 * mm, 82 * mm, 54 * mm]),
          P("dinq-discover 建议", "H2x"), P("搜索型 Agent 通常关闭 profile 和 long_term，避免把个人历史带入候选人搜索；若需要保存用户常用搜索条件，可以只启用 session 或受控 profile。", "Bodyx"), PageBreak()]

# 7
story += [P("07 Capability Plugin、Tool 与 Skill", "H1x"),
          P("能力注册表把框架执行机制与业务实现隔离。joyhousebot 只负责发现、授权、调用、超时、重试、记录和结果规范化；dinq-discover 的搜索逻辑放在独立 dinq-plugin 中。", "Bodyx"),
          FlowDiagram([[('Registry', BLUE), ('Scenario', ORANGE), ('Coordinator', GREEN)],
                       [('Tool', BLUE), ('Skill', ORANGE), ('Sub-agent', GREEN)],
                       [('Input Schema', BLUE), ('Execution', ORANGE), ('Normalized Result', GREEN)]]),
          table([[P("能力类型", "Smallx"), P("定位", "Smallx"), P("示例", "Smallx")],
                 [P("Tool", "Bodyx"), P("一次明确的外部操作，具有输入输出契约。", "Bodyx"), P("dinq.talent.filter", "CodeX")],
                 [P("Skill", "Bodyx"), P("可复用的业务策略、提示和工具组合。", "Bodyx"), P("按条件找人、候选人丰富", "Bodyx")],
                 [P("Scenario", "Bodyx"), P("定义路由、追问和能力边界。", "Bodyx"), P("Dinq Discover Search", "CodeX")],
                 [P("Plugin", "Bodyx"), P("独立分发的一组工具、Skill、Scenario 和适配器。", "Bodyx"), P("dinq-plugin", "CodeX")]], widths=[34 * mm, 85 * mm, 48 * mm]),
          P("统一调用契约", "H2x"), P("调用前校验输入 Schema 和能力边界，调用中记录准备、开始、成功或失败事件，调用后将供应商或业务返回转换为统一结果结构，并保留原始诊断信息供回放。", "Bodyx"),
          P("MCP 对外适配", "H2x"), P("Streamable HTTP MCP 网关位于 /mcp/。已发布且启用的 tool / connector 能力会动态映射为 MCP tools；tools/call 不直接执行业务函数，而是创建持久化 Run/Task，复用同一套鉴权、权限、Lease、事件、Trace、产物和回放链路。MCP 是协议适配层，不是第二套执行运行时。", "Bodyx"), PageBreak()]

# 8
story += [P("08 可观测性、Trace 与前端控制台", "H1x"),
          P("系统把一次 Run 看成可回放的执行事实，而不是只有最终文本。控制台因此可以同时承担 Agent 试用、运行监控、配置管理和插件运维。", "Bodyx"),
          FlowDiagram([[('Run Event', BLUE), ('Trace Span', ORANGE), ('Trace Blob', GREEN)],
                       [('时间线', BLUE), ('原始响应', ORANGE), ('工具诊断', GREEN)],
          [('Runs', BLUE), ('聊天试用', ORANGE), ('插件 / 场景管理', GREEN)]]),
          P("配置入口边界", "H2x"), P("侧栏将平台收拢到配置菜单的子项。平台只负责访问控制、集群发布、审计和运行摘要；Agent、Skills、Tools、MCP Server 分别在配置子菜单维护；Dinq 运维作为独立插件运维入口保留，避免平台页与能力编辑页重复。", "Bodyx"),
          P("前端可以观察的关键事实", "H2x"),
          *bullets(["请求接受、进入队列、调度决策、Worker 领取、阶段切换。", "Coordinator 的意图、场景、能力选择和追问摘要。", "模型调用耗时、工具准备与执行耗时、Task 并发进度。", "任务失败原因、重试次数、供应商错误、产物保存和验证结果。", "PG NOTIFY、poll、recovery 唤醒来源及 queue_wait_ms、claim_latency_ms。"]),
          P("显示边界", "H2x"), P("对外展示结构化决策摘要和事件，不把模型供应商的隐藏原始思维链作为稳定 API 契约。开发调试环境可以保留更细的内部 Trace，但需要权限控制、脱敏和保留期限。", "Bodyx"), PageBreak()]

# 9
story += [P("09 PostgreSQL 数据与分布式一致性", "H1x"),
          P("PostgreSQL 既是持久化存储，也是多个 API、Scheduler、Worker 进程之间的协调面。系统不依赖单机内存作为任务事实来源。", "Bodyx"),
          table([[P("机制", "Smallx"), P("作用", "Smallx"), P("解决的问题", "Smallx")],
                 [P("事务", "Bodyx"), P("原子写入 Run、Task、事件和状态", "Bodyx"), P("部分写入和状态漂移", "Bodyx")],
                 [P("SKIP LOCKED", "Bodyx"), P("并发领取任务的唯一裁决", "Bodyx"), P("多个 Worker 重复执行", "Bodyx")],
                 [P("Lease / Heartbeat", "Bodyx"), P("标识执行所有权并支持过期恢复", "Bodyx"), P("Worker 崩溃后任务悬挂", "Bodyx")],
                 [P("LISTEN / NOTIFY", "Bodyx"), P("低延迟唤醒调度器和 Worker", "Bodyx"), P("固定轮询带来的延迟", "Bodyx")],
                 [P("Outbox", "Bodyx"), P("保证外部消息投递可重试", "Bodyx"), P("网络失败导致结果丢失", "Bodyx")],
                 [P("Advisory Lock", "Bodyx"), P("迁移和共享初始化的互斥", "Bodyx"), P("多进程重复建表", "Bodyx")]], widths=[35 * mm, 73 * mm, 59 * mm]),
          P("扩展方式", "H2x"), P("增加 API 进程、Scheduler 或 Worker 不改变数据模型；Worker 通过 PG 竞争任务，Channel 通过 Lease 竞争连接所有权，所有节点都能从同一状态恢复。瓶颈主要来自数据库连接池、模型供应商限流、工具外部服务和单个 Agent 的并发上限。", "Bodyx"), PageBreak()]

# 10
story += [P("10 部署与运行拓扑", "H1x"),
          FlowDiagram([[('Nginx / TLS', BLUE), ('API Gateway', ORANGE), ('PostgreSQL', GREEN)],
          [('Web UI', BLUE), ('Scheduler', ORANGE), ('Worker x N', GREEN)],
          [('Channel', BLUE), ('Plugin / MCP', ORANGE), ('LLM Provider', GREEN)]]),
          P("当前线上形态", "H2x"),
          *bullets(["域名：joyhousebot.joyhouse.me，公网入口由反向代理转发至本机 API。", "API、Scheduler、Worker 使用独立 systemd 服务，工作目录为 /opt/joyhousebot。", "配置通过 config.json 和环境文件提供；运行时状态在已有 PostgreSQL 中。", "前端构建产物发布到 joyhousebot/static/ui，由 API 或反向代理提供。", "可以在同一台服务器运行多个 Worker，也可以扩展到多节点，只要连接同一 PostgreSQL。"]),
          P("启动与健康检查", "H2x"), P("服务启动后检查 /healthz 和 /readyz；控制台顶部同时展示 API / PostgreSQL 状态。部署前应备份代码、配置和静态资源，重启后验证 systemd 状态、端口、数据库连接和公网页面。", "Bodyx"),
          P("安全注意", "H2x"), P("当前开发部署仍可能启用 allow_insecure_auth=true。生产环境必须关闭该选项，使用数据库 API Token、管理员表和最小权限；Channel Secret、LLM Key 和数据库连接串只能来自安全环境变量或密钥管理系统。", "Bodyx"), PageBreak()]

# 11
story += [P("11 当前优势、边界与演进方向", "H1x"),
          table([[P("方面", "Smallx"), P("优势", "Smallx"), P("当前边界 / 下一步", "Smallx")],
                 [P("并发", "Bodyx"), P("PG 事务、SKIP LOCKED、Lease 支持多 Worker。", "Bodyx"), P("需根据连接池、模型限流和工具耗时做压测与容量配置。", "Bodyx")],
                 [P("可恢复", "Bodyx"), P("Run、Task、事件、记忆和 Outbox 都可持久化。", "Bodyx"), P("需持续验证异常重启、超时、死锁和重复投递场景。", "Bodyx")],
                 [P("可解释", "Bodyx"), P("事件时间线和 Trace 能定位每一步瓶颈。", "Bodyx"), P("需完善敏感信息脱敏、Trace 保留策略和权限模型。", "Bodyx")],
                 [P("业务隔离", "Bodyx"), P("dinq-plugin 可独立演进，核心只提供运行能力。", "Bodyx"), P("需要稳定 SDK、插件版本兼容和契约测试。", "Bodyx")],
                 [P("记忆", "Bodyx"), P("多层、可配置、用户和 Agent 隔离、PG 持久化。", "Bodyx"), P("需补充检索质量评估、冲突合并和隐私生命周期。", "Bodyx")],
                 [P("入口", "Bodyx"), P("HTTP、UI、Channel 统一进入 Run。", "Bodyx"), P("需完善各 Channel 的生产级限流、重连和监控。", "Bodyx")]], widths=[25 * mm, 67 * mm, 75 * mm]),
          P("推荐演进顺序", "H2x"),
          *bullets(["先完成 API Token、管理员权限、密钥保护和生产安全基线。", "以 dinq-plugin 建立工具、Skill、Scenario 的契约测试和回放样例。", "增加 PG NOTIFY 延迟、队列深度、Worker 利用率、LLM 成本和失败率指标。", "建立压测矩阵：单用户长任务、多用户短任务、工具慢响应、Worker 重启和数据库故障。", "再引入可选缓存、模型路由和跨节点弹性伸缩，不改变统一 Run 契约。"]),
          Spacer(1, 6 * mm), P("结论", "H2x"), P("joyhousebot 当前最重要的架构特征是：以 PostgreSQL 为共享状态中心，以 Coordinator 为统一协调入口，以 Worker 集群执行持久化任务，以 Plugin 隔离业务能力，以事件和 Trace 提供全过程可解释性，并通过 Channel、Memory 和 Web 控制台扩展服务边界。", "Bodyx"), PageBreak()]

# Appendix
story += [P("附录 A 关键概念速查", "H1x"),
          table([[P("概念", "Smallx"), P("一句话定义", "Smallx")],
                 [P("Run", "Bodyx"), P("用户一次请求对应的可持久化执行实例。", "Bodyx")],
                 [P("Task", "Bodyx"), P("Run 中可调度、可重试、可并发的执行单元。", "Bodyx")],
                 [P("Coordinator", "Bodyx"), P("负责意图、场景、追问和执行计划的主 Agent。", "Bodyx")],
                 [P("Scenario", "Bodyx"), P("业务人员可配置的路由、字段、追问和能力边界。", "Bodyx")],
                 [P("Skill", "Bodyx"), P("可复用的策略和工具组合。", "Bodyx")],
                 [P("Tool", "Bodyx"), P("具有明确输入输出契约的一次外部操作。", "Bodyx")],
                 [P("Plugin", "Bodyx"), P("独立承载业务能力的扩展包，例如 dinq-plugin。", "Bodyx")],
                 [P("Channel", "Bodyx"), P("外部消息平台的接入和结果投递适配器。", "Bodyx")],
                 [P("Memory", "Bodyx"), P("按用户和 Agent 隔离的可配置持久化记忆。", "Bodyx")],
                 [P("Trace", "Bodyx"), P("模型、工具、阶段和结果的可回放诊断记录。", "Bodyx")],
                 [P("Lease", "Bodyx"), P("任务或 Channel 所有权的带过期时间租约。", "Bodyx")]], widths=[40 * mm, 127 * mm]),
          Spacer(1, 10 * mm), P("附录 B 代码导航", "H2x"),
          P("API：joyhousebot/api/；运行时：joyhousebot/runtime/；PostgreSQL：joyhousebot/storage/；记忆：joyhousebot/agent/memory*.py；Channel：joyhousebot/channels/；前端：frontend/；业务插件：独立 dinq-plugin 项目。", "Bodyx"),
          P("本文档描述当前实现基线。代码演进后，应同步更新本文件并重新执行部署和验证流程。", "Smallx")]


doc = ArchitectureDoc(str(OUT))
doc.build(story)
print(OUT)
