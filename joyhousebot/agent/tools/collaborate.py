"""Collaborate Tool - enables multi-agent collaboration as a tool."""

import asyncio
from typing import Any

from loguru import logger

from joyhousebot.agent.tools.base import Tool, ToolResult
from joyhousebot.agent.collaboration.types import (
    AgentCapability,
    AgentProfile,
    CollaborationMode,
    CollaborationRequest,
)
from joyhousebot.agent.collaboration.orchestrator import Orchestrator, create_orchestrator
from joyhousebot.agent.collaboration.options import OrchestratorOptions


class CollaborateTool(Tool):
    """
    Tool for multi-agent collaboration on complex tasks.
    
    This tool enables a single agent to coordinate multiple specialized
    agents to work together on complex goals like investment decisions,
    research, or project planning.
    """
    
    name = "collaborate"
    description = """Launch multi-agent collaboration for complex tasks.
    
Use this tool when you need:
- Multiple perspectives on a decision (investment, strategy, etc.)
- Specialized analysis from different domains
- Parallel execution of independent subtasks
- Aggregated results with a final decision

The tool will:
1. Decompose your goal into subtasks
2. Assign tasks to specialized agents
3. Execute tasks in parallel
4. Aggregate results and make a decision
5. Record everything for verification

Returns a structured result with decision, confidence, and reasoning.
"""
    parameters = {
        "type": "object",
        "properties": {
            "goal": {
                "type": "string",
                "description": "The goal or question to collaborate on (e.g., 'Should I buy BTC at current price?')"
            },
            "context": {
                "type": "object",
                "description": "Additional context data (prices, dates, preferences, etc.)",
                "additionalProperties": True
            },
            "mode": {
                "type": "string",
                "enum": ["parallel", "sequential", "pipeline"],
                "description": "Execution mode (default: parallel)",
                "default": "parallel"
            },
            "max_rounds": {
                "type": "integer",
                "description": "Maximum iterations (default: 3)",
                "default": 3
            },
            "require_backtest": {
                "type": "boolean",
                "description": "Whether to require backtest validation",
                "default": false
            },
            "agents": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "name": {"type": "string"},
                        "capabilities": {
                            "type": "array",
                            "items": {"type": "string"}
                        }
                    }
                },
                "description": "Optional custom agents to use (if not provided, uses system agents)"
            }
        },
        "required": ["goal"]
    }
    
    def __init__(self, workspace_path: str, config: Any, provider: Any):
        super().__init__(workspace_path, config, provider)
        self._orchestrator: Orchestrator | None = None
    
    async def execute(
        self,
        goal: str,
        context: dict[str, Any] | None = None,
        mode: str = "parallel",
        max_rounds: int = 3,
        require_backtest: bool = False,
        agents: list[dict[str, Any]] | None = None,
    ) -> ToolResult:
        """
        Execute multi-agent collaboration.
        
        Args:
            goal: The goal to collaborate on
            context: Additional context
            mode: Execution mode
            max_rounds: Maximum iterations
            require_backtest: Whether to require backtest
            agents: Optional custom agents
            
        Returns:
            ToolResult with collaboration outcome
        """
        try:
            if agents:
                agent_profiles = self._build_agent_profiles(agents)
                orchestrator = await self._create_custom_orchestrator(agent_profiles)
            else:
                orchestrator = await create_orchestrator(
                    config=self.config,
                    provider=self.provider,
                    workspace=self.workspace,
                )
            
            request = CollaborationRequest(
                goal=goal,
                context=context or {},
                mode=CollaborationMode(mode),
                max_rounds=max_rounds,
                require_backtest=require_backtest,
            )
            
            progress_events: list[str] = []
            
            def progress_callback(task_id: str, agent_id: str, progress: float):
                event = f"Task {task_id} ({agent_id}): {progress*100:.0f}%"
                progress_events.append(event)
                logger.info(f"[Collaborate] {event}")
            
            result, trace = await orchestrator.collaborate(
                request=request,
                progress_callback=progress_callback,
            )
            
            output = self._format_result(result, trace, progress_events)
            
            return ToolResult(
                success=result.decision not in ("error", "aborted"),
                output=output,
                data={
                    "decision": result.decision,
                    "confidence": result.confidence,
                    "trace_id": trace.trace_id,
                    "factors": [f.model_dump() for f in result.factors],
                    "execution_plan": result.execution_plan,
                }
            )
            
        except Exception as e:
            logger.error(f"Collaborate tool failed: {e}")
            return ToolResult(
                success=False,
                output=f"Collaboration failed: {str(e)}",
                error=str(e),
            )
    
    def _build_agent_profiles(
        self,
        agents: list[dict[str, Any]]
    ) -> dict[str, AgentProfile]:
        """Build agent profiles from config."""
        profiles = {}
        
        for agent_config in agents:
            agent_id = agent_config.get("id", "unknown")
            capabilities = [
                AgentCapability(id=cap, name=cap)
                for cap in agent_config.get("capabilities", [])
            ]
            
            profiles[agent_id] = AgentProfile(
                agent_id=agent_id,
                name=agent_config.get("name", agent_id),
                capabilities=capabilities,
            )
        
        return profiles
    
    async def _create_custom_orchestrator(
        self,
        agents: dict[str, AgentProfile],
    ) -> Orchestrator:
        """Create orchestrator with custom agents."""
        return Orchestrator(
            config=self.config,
            provider=self.provider,
            agents=agents,
            workspace=self.workspace,
            options=OrchestratorOptions(),
        )
    
    def _format_result(
        self,
        result: Any,
        trace: Any,
        progress_events: list[str],
    ) -> str:
        """Format collaboration result for display."""
        lines = [
            "# Collaboration Result",
            "",
            f"**Decision:** {result.decision}",
            f"**Confidence:** {result.confidence:.0%}",
            "",
            "## Reasoning",
            result.reasoning,
            "",
        ]
        
        if result.factors:
            lines.append("## Key Factors")
            for factor in result.factors:
                lines.append(f"- **{factor.name}**: {factor.value} (weight: {factor.weight})")
            lines.append("")
        
        if result.execution_plan:
            lines.append("## Execution Plan")
            plan = result.execution_plan
            if isinstance(plan, dict):
                for key, value in plan.items():
                    lines.append(f"- **{key}**: {value}")
            lines.append("")
        
        if result.agent_contributions:
            lines.append("## Agent Contributions")
            for task_id, contribution in result.agent_contributions.items():
                preview = contribution[:200] + "..." if len(contribution) > 200 else contribution
                lines.append(f"### {task_id}")
                lines.append(preview)
                lines.append("")
        
        lines.extend([
            "## Execution Summary",
            f"- Trace ID: {trace.trace_id}",
            f"- Duration: {trace.total_duration_ms}ms",
            f"- Tasks: {len(trace.tasks)}",
            f"- LLM Calls: {trace.total_llm_calls}",
            f"- Tokens: {trace.total_tokens}",
        ])
        
        return "\n".join(lines)
