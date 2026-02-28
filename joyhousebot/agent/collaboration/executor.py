"""Parallel task executor."""

import asyncio
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from loguru import logger

from joyhousebot.agent.collaboration.types import (
    AgentProfile,
    Task,
    TaskExecutionTrace,
    TaskResult,
    TaskStatus,
)

if TYPE_CHECKING:
    from joyhousebot.providers.base import LLMProvider


EXECUTOR_SYSTEM_PROMPT = """You are a specialized agent executing a specific task as part of a larger collaboration.

Focus only on your assigned task. Be thorough and provide detailed results.
Your output will be used by other agents or aggregated into a final decision."""


class TaskExecutor:
    """Executes tasks using LLM and optional tools."""
    
    def __init__(
        self,
        provider: "LLMProvider",
        workspace: Path,
        max_retries: int = 2,
        timeout_seconds: int = 300,
    ):
        self.provider = provider
        self.workspace = workspace
        self.max_retries = max_retries
        self.timeout_seconds = timeout_seconds
    
    async def execute(
        self,
        task: Task,
        agent: AgentProfile,
        context: dict[str, Any] | None = None,
    ) -> TaskResult:
        """
        Execute a single task.
        
        Args:
            task: The task to execute
            agent: The agent profile executing the task
            context: Additional context (e.g., results from dependency tasks)
            
        Returns:
            TaskResult with execution outcome
        """
        trace = TaskExecutionTrace(
            task_id=task.id,
            agent_id=agent.agent_id,
            input=task.input_data,
        )
        
        trace.mark_started()
        
        for attempt in range(self.max_retries + 1):
            trace.retry_count = attempt
            try:
                result = await asyncio.wait_for(
                    self._execute_with_llm(task, agent, context),
                    timeout=task.timeout_seconds,
                )
                trace.mark_completed(result)
                
                return TaskResult(
                    task_id=task.id,
                    status=TaskStatus.COMPLETED,
                    output=result,
                    trace=trace,
                )
                
            except asyncio.TimeoutError:
                error_msg = f"Task timeout after {task.timeout_seconds}s"
                logger.warning(f"Task {task.id} timeout (attempt {attempt + 1})")
                trace.error = error_msg
                
            except Exception as e:
                error_msg = str(e)
                logger.warning(f"Task {task.id} failed (attempt {attempt + 1}): {e}")
                trace.error = error_msg
            
            if attempt < self.max_retries:
                await asyncio.sleep(1.0 * (2 ** attempt))
        
        trace.mark_failed(trace.error or "Unknown error")
        return TaskResult(
            task_id=task.id,
            status=TaskStatus.FAILED,
            error=trace.error,
            trace=trace,
        )
    
    async def _execute_with_llm(
        self,
        task: Task,
        agent: AgentProfile,
        context: dict[str, Any] | None = None,
    ) -> str:
        """Execute task using LLM."""
        context_str = ""
        if context:
            context_str = "\n## Context from Previous Tasks\n"
            for key, value in context.items():
                preview = str(value)[:1000]
                context_str += f"- {key}: {preview}\n"
        
        prompt = f"""## Your Role
{agent.name}: {agent.description}

## Your Task
{task.name}
{task.description}

## Task Input
{task.input_data}
{context_str}

## Instructions
Complete this task thoroughly. Focus on quality over brevity.
Provide your results in a clear, structured format."""

        response = await self.provider.chat(
            messages=[
                {"role": "system", "content": EXECUTOR_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            model=agent.model,
            temperature=agent.temperature,
        )
        
        return response.content or ""


class ParallelExecutor:
    """Executes multiple tasks in parallel with controlled concurrency."""
    
    def __init__(
        self,
        provider: "LLMProvider",
        workspace: Path,
        max_concurrent: int = 4,
        max_retries: int = 2,
        timeout_seconds: int = 300,
        progress_callback: Callable[[str, str, float], None] | None = None,
    ):
        self.provider = provider
        self.workspace = workspace
        self.max_concurrent = max_concurrent
        self.max_retries = max_retries
        self.timeout_seconds = timeout_seconds
        self.progress_callback = progress_callback
        self.executor = TaskExecutor(
            provider=provider,
            workspace=workspace,
            max_retries=max_retries,
            timeout_seconds=timeout_seconds,
        )
    
    async def execute_all(
        self,
        tasks: list[Task],
        assignments: dict[str, str],
        agents: dict[str, AgentProfile],
        dependency_results: dict[str, TaskResult] | None = None,
    ) -> dict[str, TaskResult]:
        """
        Execute all tasks respecting dependencies and concurrency limits.
        
        Args:
            tasks: List of tasks to execute
            assignments: Task ID to Agent ID mapping
            agents: Agent profiles
            dependency_results: Results from previously completed tasks
            
        Returns:
            Dictionary mapping task_id to TaskResult
        """
        results: dict[str, TaskResult] = dict(dependency_results or {})
        task_map = {t.id: t for t in tasks}
        
        remaining = set(t.id for t in tasks if t.id not in results)
        semaphore = asyncio.Semaphore(self.max_concurrent)
        
        def get_ready_tasks() -> list[str]:
            """Get tasks whose dependencies are all completed."""
            ready = []
            for tid in list(remaining):
                task = task_map[tid]
                deps_completed = all(
                    dep_id in results and results[dep_id].status == TaskStatus.COMPLETED
                    for dep_id in task.dependencies
                )
                if deps_completed:
                    ready.append(tid)
            return ready
        
        async def execute_task(task_id: str) -> tuple[str, TaskResult]:
            """Execute a single task with semaphore."""
            async with semaphore:
                task = task_map[task_id]
                agent_id = assignments.get(task_id)
                
                if not agent_id or agent_id not in agents:
                    result = TaskResult(
                        task_id=task_id,
                        status=TaskStatus.FAILED,
                        error=f"No agent assigned or agent not found",
                    )
                    return task_id, result
                
                agent = agents[agent_id]
                
                dep_context = {}
                for dep_id in task.dependencies:
                    if dep_id in results and results[dep_id].output:
                        dep_context[f"task_{dep_id}"] = results[dep_id].output
                
                if self.progress_callback:
                    self.progress_callback(task_id, agent_id, 0.0)
                
                result = await self.executor.execute(task, agent, dep_context)
                
                if self.progress_callback:
                    progress = 1.0 if result.status == TaskStatus.COMPLETED else 0.0
                    self.progress_callback(task_id, agent_id, progress)
                
                return task_id, result
        
        while remaining:
            ready = get_ready_tasks()
            
            if not ready:
                remaining_tasks = [task_map[tid] for tid in remaining]
                unmet_deps = set()
                for t in remaining_tasks:
                    unmet_deps.update(d for d in t.dependencies if d not in results)
                
                if unmet_deps:
                    logger.error(f"Deadlock: tasks waiting for failed dependencies: {unmet_deps}")
                    for tid in list(remaining):
                        results[tid] = TaskResult(
                            task_id=tid,
                            status=TaskStatus.SKIPPED,
                            error="Dependency failed",
                        )
                    remaining.clear()
                else:
                    logger.warning("No ready tasks, but remaining tasks exist. Forcing execution.")
                    ready = list(remaining)[:self.max_concurrent]
            
            batch = ready[:self.max_concurrent]
            logger.info(f"Executing batch of {len(batch)} tasks")
            
            batch_results = await asyncio.gather(
                *[execute_task(tid) for tid in batch],
                return_exceptions=True,
            )
            
            for item in batch_results:
                if isinstance(item, Exception):
                    logger.error(f"Task execution error: {item}")
                    continue
                task_id, result = item
                results[task_id] = result
                remaining.discard(task_id)
        
        return results
    
    async def execute_single(
        self,
        task: Task,
        agent: AgentProfile,
        context: dict[str, Any] | None = None,
    ) -> TaskResult:
        """Execute a single task."""
        return await self.executor.execute(task, agent, context)
