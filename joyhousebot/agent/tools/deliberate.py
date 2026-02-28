"""Deliberate Tool - enables multi-round deliberation as a tool."""

from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Awaitable

from loguru import logger

from joyhousebot.agent.tools.base import Tool
from joyhousebot.agent.collaboration.types import (
    DeliberationMode,
    DeliberationRequest,
    DeliberationTrace,
)
from joyhousebot.agent.collaboration.deliberation import DeliberationEngine
from joyhousebot.utils.helpers import ensure_dir


class DeliberateTool(Tool):
    """
    Tool for multi-round progressive deliberation on complex decisions.
    
    This tool enables structured multi-round analysis where each round
    builds upon previous conclusions, leading to a comprehensive decision.
    """
    
    name = "deliberate"
    description = """深度多轮分析工具 - 用于复杂决策和投资分析。

【何时必须使用此工具】
当用户问以下类型问题时，请主动调用此工具：
- 投资决策: "我应该买BTC/股票/基金吗？"、"现在买入合适吗？"
- 购买建议: "这个值得买吗？"、"要不要入手？"
- 重大决策: "我应该辞职创业吗？"、"要不要买房？"
- 分析请求: "帮我分析一下..."、"能不能评估一下..."
- 策略规划: 需要多步骤思考的复杂问题

【工具功能】
执行3轮结构化分析:
1. 可行性/现状分析
2. 风险/收益评估  
3. 行动方案/建议

【输出内容】
- 明确的决策建议
- 置信度评估
- 分级行动项
- 风险提示和缓解措施

注意：对于简单的投资/购买问题，使用此工具比直接回答更专业、更全面。"""
    parameters = {
        "type": "object",
        "properties": {
            "goal": {
                "type": "string",
                "description": "The goal or decision to deliberate on (e.g., 'Should I buy BTC at current price?')"
            },
            "context": {
                "type": "object",
                "description": "Additional context data (current price, budget, timeline, etc.)",
                "additionalProperties": True
            },
            "max_rounds": {
                "type": "integer",
                "description": "Maximum number of deliberation rounds (default: 3, max: 5)",
                "default": 3,
                "minimum": 1,
                "maximum": 5
            },
            "mode": {
                "type": "string",
                "enum": ["progressive", "iterative", "exploratory"],
                "description": "Deliberation mode (default: progressive)",
                "default": "progressive"
            },
            "topics": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "description": {"type": "string"},
                        "focus_questions": {
                            "type": "array",
                            "items": {"type": "string"}
                        }
                    }
                },
                "description": "Optional custom topics (if not provided, will auto-generate)"
            }
        },
        "required": ["goal"]
    }
    
    def __init__(self, workspace_path: str, config: Any, provider: Any, model: str | None = None):
        super().__init__()
        self.workspace = workspace_path
        self.config = config
        self.provider = provider
        self.model = model or (provider.get_default_model() if hasattr(provider, 'get_default_model') else None)
    
    async def execute(
        self,
        goal: str,
        context: dict[str, Any] | None = None,
        max_rounds: int = 3,
        mode: str = "progressive",
        topics: list[dict[str, Any]] | None = None,
        execution_stream_callback: Callable[[str, dict], Awaitable[None]] | None = None,
        **kwargs: Any,
    ) -> str:
        """
        Execute multi-round deliberation.
        
        Args:
            goal: The goal to deliberate on
            context: Additional context
            max_rounds: Maximum number of rounds (1-5)
            mode: Deliberation mode
            topics: Optional custom topics
            execution_stream_callback: Callback for streaming progress events
            
        Returns:
            Formatted deliberation result string
        """
        try:
            max_rounds = min(max(1, max_rounds), 5)
            
            request = DeliberationRequest(
                goal=goal,
                context=context or {},
                mode=DeliberationMode(mode),
                max_rounds=max_rounds,
                auto_generate_topics=topics is None,
                initial_topics=topics or [],
                feedback_enabled=True,
            )
            
            engine = DeliberationEngine(
                provider=self.provider,
                model=self.model,
                progress_callback=execution_stream_callback,
            )
            
            result, trace = await engine.deliberate(request)
            
            self._save_trace(trace)
            
            return self._format_result(result, trace)
            
        except Exception as e:
            import traceback
            logger.error(f"Deliberate tool failed: {e}\n{traceback.format_exc()}")
            return f"Deliberation failed: {str(e)}"
    
    def _format_result(self, result: Any, trace: Any) -> str:
        """Format deliberation result for display."""
        lines = [
            "# 多轮讨论结果",
            "",
            f"**最终决策:** {result.final_decision}",
            f"**置信度:** {result.overall_confidence:.0%}",
            f"**讨论轮数:** {result.total_rounds}",
            "",
        ]
        
        if result.executive_summary:
            lines.extend([
                "## 执行摘要",
                result.executive_summary,
                "",
            ])
        
        if result.round_results:
            lines.append("## 各轮讨论结论")
            for rr in result.round_results:
                lines.append(f"### Round {rr.round_number}: {rr.topic.title}")
                lines.append(rr.conclusion)
                if rr.key_findings:
                    lines.append("**关键发现:**")
                    for f in rr.key_findings:
                        lines.append(f"- {f}")
                lines.append("")
        
        if result.action_items:
            lines.append("## 行动项")
            for item in result.action_items:
                priority = item.get("priority", "medium")
                action = item.get("action", "")
                timeline = item.get("timeline", "")
                lines.append(f"- [{priority.upper()}] {action}" + (f" ({timeline})" if timeline else ""))
            lines.append("")
        
        if result.risks:
            lines.append("## 风险评估")
            for risk in result.risks:
                severity = risk.get("severity", "medium")
                desc = risk.get("risk", "")
                mitigation = risk.get("mitigation", "")
                lines.append(f"- **{severity.upper()}**: {desc}")
                if mitigation:
                    lines.append(f"  - 缓解措施: {mitigation}")
            lines.append("")
        
        if result.recommendations:
            lines.append("## 建议")
            for rec in result.recommendations:
                lines.append(f"- {rec}")
            lines.append("")
        
        lines.extend([
            "---",
            f"*Trace ID: {trace.trace_id}*",
            f"*查看完整详情: cat ~/.joyhousebot/workspace/collaboration/traces/{datetime.now().strftime('%Y-%m')}/trace_{trace.trace_id}.json*",
        ])
        
        return "\n".join(lines)
    
    def _save_trace(self, trace: DeliberationTrace) -> Path:
        """Save deliberation trace to file."""
        try:
            traces_dir = Path(self.workspace) / "collaboration" / "traces" / datetime.now().strftime("%Y-%m")
            ensure_dir(traces_dir)
            
            trace_file = traces_dir / f"trace_{trace.trace_id}.json"
            with open(trace_file, "w", encoding="utf-8") as f:
                f.write(trace.model_dump_json(indent=2))
            
            logger.info(f"Deliberation trace saved: {trace_file}")
            return trace_file
        except Exception as e:
            logger.warning(f"Failed to save trace: {e}")
            return Path("")
