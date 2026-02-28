"""Multi-Agent Collaboration Orchestrator.

The orchestrator coordinates the entire collaboration process:
1. Task decomposition
2. Task dispatching to agents
3. Parallel/sequential execution
4. Result aggregation
5. Decision making
6. Feedback recording
"""

import asyncio
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from loguru import logger

from joyhousebot.agent.collaboration.aggregator import ResultAggregator
from joyhousebot.agent.collaboration.decision import DecisionEngine
from joyhousebot.agent.collaboration.dispatcher import TaskDispatcher
from joyhousebot.agent.collaboration.executor import ParallelExecutor
from joyhousebot.agent.collaboration.feedback import FeedbackLoop
from joyhousebot.agent.collaboration.options import OrchestratorOptions
from joyhousebot.agent.collaboration.task_decomposer import TaskDecomposer
from joyhousebot.agent.collaboration.trace import ProgressTracker, TraceManager
from joyhousebot.agent.collaboration.types import (
    AgentProfile,
    CollaborationMode,
    CollaborationRequest,
    CollaborationResult,
    CollaborationTrace,
    DeliberationRequest,
    DeliberationResult,
    DeliberationTrace,
    Task,
    TaskExecutionTrace,
    TaskResult,
    TaskStatus,
)

if TYPE_CHECKING:
    from joyhousebot.config.schema import Config
    from joyhousebot.providers.base import LLMProvider


class Orchestrator:
    """
    Multi-Agent Collaboration Orchestrator.
    
    Coordinates multiple agents to collaboratively complete complex tasks.
    """
    
    def __init__(
        self,
        config: "Config",
        provider: "LLMProvider",
        agents: dict[str, AgentProfile],
        workspace: Path,
        options: OrchestratorOptions | None = None,
    ):
        """
        Initialize the orchestrator.
        
        Args:
            config: Application configuration
            provider: LLM provider for task execution
            agents: Dictionary of agent profiles (agent_id -> AgentProfile)
            workspace: Workspace directory for traces and artifacts
            options: Orchestrator options
        """
        self.config = config
        self.provider = provider
        self.agents = agents
        self.workspace = workspace
        self.options = options or OrchestratorOptions()
        
        model, _ = config.get_agent_model_and_fallbacks()
        
        self.decomposer = TaskDecomposer(provider)
        self.dispatcher = TaskDispatcher(agents)
        self.executor = ParallelExecutor(
            provider=provider,
            workspace=workspace,
            max_concurrent=self.options.max_concurrent_tasks,
            max_retries=self.options.max_retries,
            timeout_seconds=self.options.task_timeout_seconds,
        )
        self.aggregator = ResultAggregator(provider)
        self.decision_engine = DecisionEngine(provider, default_model=model)
        self.feedback_loop = FeedbackLoop(
            workspace=workspace,
            reminder_days=self.options.feedback_reminder_days,
        )
        self.trace_manager = TraceManager(workspace=workspace)
    
    async def collaborate(
        self,
        request: CollaborationRequest,
        progress_callback: Callable[[str, str, float], None] | None = None,
    ) -> tuple[CollaborationResult, CollaborationTrace]:
        """
        Execute a collaboration request.
        
        Args:
            request: The collaboration request
            progress_callback: Optional callback for progress updates (task_id, agent_id, progress)
            
        Returns:
            Tuple of (CollaborationResult, CollaborationTrace)
        """
        trace = self.trace_manager.create_trace(request)
        progress = ProgressTracker(trace.trace_id)
        
        if progress_callback:
            progress.add_listener(
                lambda e: progress_callback(
                    e.task_id or "",
                    e.agent_id or "",
                    e.progress or 0.0,
                ) if e.task_id else None
            )
        
        try:
            await progress.on_phase_started("decomposition")
            
            tasks = await self.decomposer.decompose(request)
            trace.tasks = tasks
            logger.info(f"Decomposed into {len(tasks)} tasks")
            
            await progress.on_phase_started("dispatching")
            
            assignments = self.dispatcher.dispatch(tasks)
            for task in tasks:
                if task.id in assignments:
                    task.assigned_agent = assignments[task.id]
            
            await progress.on_phase_started("execution")
            
            task_results = await self._execute_with_trace(
                tasks=tasks,
                assignments=assignments,
                trace=trace,
                progress=progress,
            )
            
            completed = sum(
                1 for r in task_results.values()
                if r.status == TaskStatus.COMPLETED
            )
            failed = sum(
                1 for r in task_results.values()
                if r.status == TaskStatus.FAILED
            )
            logger.info(f"Execution complete: {completed} completed, {failed} failed")
            
            if failed > 0 and self.options.required_task_failure_mode == "abort":
                critical_failed = [
                    tid for tid, r in task_results.items()
                    if r.status == TaskStatus.FAILED
                ]
                if critical_failed:
                    trace.mark_completed()
                    trace.final_result = CollaborationResult(
                        goal=request.goal,
                        decision="aborted",
                        confidence=0.0,
                        reasoning=f"Critical tasks failed: {critical_failed}",
                        task_results=task_results,
                    )
                    self.trace_manager.save_trace(trace)
                    return trace.final_result, trace
            
            await progress.on_phase_started("aggregation")
            
            aggregated = await self.aggregator.aggregate(request, task_results)
            
            await progress.on_phase_started("decision")
            
            decision = await self.decision_engine.decide(
                request=request,
                aggregated=aggregated,
                task_results=task_results,
            )
            
            await progress.on_decision_made(decision.decision, decision.confidence)
            
            if self.options.feedback_enabled:
                feedback_record = await self.feedback_loop.record(request, decision)
                decision.feedback_id = feedback_record.feedback_id
            
            trace.mark_completed()
            trace.final_result = decision
            self.trace_manager.save_trace(trace)
            
            logger.info(
                f"Collaboration complete: decision={decision.decision}, "
                f"confidence={decision.confidence:.2f}"
            )
            
            return decision, trace
            
        except Exception as e:
            logger.error(f"Collaboration failed: {e}")
            trace.mark_completed()
            trace.final_result = CollaborationResult(
                goal=request.goal,
                decision="error",
                confidence=0.0,
                reasoning=f"Collaboration failed: {str(e)}",
            )
            self.trace_manager.save_trace(trace)
            raise
    
    async def _execute_with_trace(
        self,
        tasks: list[Task],
        assignments: dict[str, str],
        trace: CollaborationTrace,
        progress: ProgressTracker,
    ) -> dict[str, TaskResult]:
        """Execute tasks and record traces."""
        results = await self.executor.execute_all(
            tasks=tasks,
            assignments=assignments,
            agents=self.agents,
        )
        
        for task_id, result in results.items():
            if result.trace:
                trace.task_traces[task_id] = result.trace
            
            if result.status == TaskStatus.COMPLETED:
                await progress.on_task_completed(task_id, result.output or "")
            elif result.status == TaskStatus.FAILED:
                await progress.on_task_failed(task_id, result.error or "Unknown error")
        
        return results
    
    def get_trace(self, trace_id: str) -> CollaborationTrace | None:
        """Load a collaboration trace by ID."""
        return self.trace_manager.load_trace(trace_id)
    
    def list_traces(
        self,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List collaboration trace summaries."""
        return self.trace_manager.list_traces(limit=limit, offset=offset)
    
    def search_traces(
        self,
        query: str,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Search traces by goal text."""
        return self.trace_manager.search_traces(query, limit=limit)
    
    async def verify_decision(
        self,
        feedback_id: str,
        actual_outcome: str,
        correct: bool,
        notes: str | None = None,
    ):
        """Verify a previous decision with actual outcome."""
        await self.feedback_loop.verify(
            feedback_id=feedback_id,
            actual_outcome=actual_outcome,
            correct=correct,
            notes=notes,
        )
    
    def get_accuracy_stats(
        self,
        decision_type: str | None = None,
        days: int | None = None,
    ) -> dict[str, Any]:
        """Get decision accuracy statistics."""
        return self.feedback_loop.get_accuracy_stats(
            decision_type=decision_type,
            days=days,
        )
    
    def get_pending_verifications(self) -> list[dict[str, Any]]:
        """Get decisions pending verification."""
        records = self.feedback_loop.get_pending_verifications()
        return [r.model_dump() for r in records]
    
    async def deliberate(
        self,
        request: DeliberationRequest,
        progress_callback: Callable[[str, str, float], None] | None = None,
    ) -> tuple[DeliberationResult, DeliberationTrace]:
        """
        Execute a multi-round deliberation request.
        
        This method runs progressive rounds of analysis, where each round
        builds upon the conclusions of previous rounds.
        
        Args:
            request: The deliberation request
            progress_callback: Optional callback for progress updates
            
        Returns:
            Tuple of (DeliberationResult, DeliberationTrace)
        """
        from joyhousebot.agent.collaboration.deliberation import DeliberationEngine
        from joyhousebot.agent.collaboration.trace import TraceManager
        
        deliberation_engine = DeliberationEngine(
            provider=self.provider,
            agents=self.agents,
            executor=self.executor,
        )
        
        result, trace = await deliberation_engine.deliberate(request)
        
        if self.options.trace_enabled:
            trace_dir = self.workspace / "collaboration" / "deliberation_traces"
            trace_dir.mkdir(parents=True, exist_ok=True)
            trace_file = trace_dir / f"deliberation_{trace.trace_id}.json"
            with open(trace_file, "w", encoding="utf-8") as f:
                f.write(trace.model_dump_json(indent=2))
        
        if self.options.feedback_enabled and request.feedback_enabled:
            feedback_record = await self.feedback_loop.record(
                CollaborationRequest(
                    goal=request.goal,
                    context=request.context,
                ),
                CollaborationResult(
                    goal=request.goal,
                    decision=result.final_decision,
                    confidence=result.overall_confidence,
                    reasoning=result.executive_summary,
                ),
            )
            result.feedback_id = feedback_record.feedback_id
        
        return result, trace


async def create_orchestrator(
    config: "Config",
    provider: "LLMProvider",
    workspace: Path | None = None,
    agent_ids: list[str] | None = None,
    options: OrchestratorOptions | None = None,
) -> Orchestrator:
    """
    Factory function to create an orchestrator with configured agents.
    
    Args:
        config: Application configuration
        provider: LLM provider
        workspace: Optional workspace path (defaults to config workspace)
        agent_ids: Optional list of agent IDs to use (defaults to all activated)
        options: Orchestrator options
        
    Returns:
        Configured Orchestrator instance
    """
    if workspace is None:
        workspace = config.workspace_path
    
    agents: dict[str, AgentProfile] = {}
    
    for entry in config.agents.agent_list:
        if agent_ids and entry.id not in agent_ids:
            continue
        if not getattr(entry, "activated", True):
            continue
        
        agent_profile = AgentProfile(
            agent_id=entry.id,
            name=entry.name or entry.id,
            model=entry.model,
            temperature=entry.temperature,
            capabilities=[],
        )
        agents[entry.id] = agent_profile
    
    if not agents:
        default_entry = config._resolve_default_entry()
        agents["default"] = AgentProfile(
            agent_id="default",
            name=getattr(default_entry, "name", "default") or "default",
            model=getattr(default_entry, "model", ""),
            temperature=getattr(default_entry, "temperature", 0.7),
            capabilities=[],
        )
    
    return Orchestrator(
        config=config,
        provider=provider,
        agents=agents,
        workspace=workspace,
        options=options,
    )
