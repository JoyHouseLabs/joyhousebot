"""Task decomposer - breaks down goals into executable tasks."""

import json
from typing import TYPE_CHECKING

from loguru import logger

from joyhousebot.agent.collaboration.types import (
    CollaborationMode,
    CollaborationRequest,
    Task,
)

if TYPE_CHECKING:
    from joyhousebot.providers.base import LLMProvider


DECOMPOSE_SYSTEM_PROMPT = """You are a task decomposition expert. Your job is to break down a complex goal into smaller, executable subtasks.

Rules:
1. Each task should be specific and actionable
2. Tasks should have clear required capabilities (e.g., technical_analysis, data_fetch, backtest, macro_analysis)
3. Specify dependencies between tasks if some must complete before others
4. Keep tasks focused - each task should have a single responsibility
5. Assign priority: higher number = higher priority (scale 1-10)

Respond with ONLY valid JSON, no markdown fences."""

DECOMPOSE_PROMPT_TEMPLATE = """Decompose the following goal into subtasks:

## Goal
{goal}

## Context
{context}

## Available Capabilities
{capabilities}

## Execution Mode
{mode}

## Instructions
Create 3-8 subtasks that together accomplish the goal. Each task should:
- Have a clear name and description
- Specify required capabilities from the available list
- List any dependencies (task names it depends on)

Respond in this JSON format:
{{
  "tasks": [
    {{
      "name": "task_name",
      "description": "Detailed description of what this task should accomplish",
      "required_capabilities": ["capability1", "capability2"],
      "dependencies": [],
      "priority": 8
    }}
  ]
}}
"""


class TaskDecomposer:
    """Decomposes collaboration goals into executable tasks."""
    
    def __init__(
        self,
        provider: "LLMProvider",
        available_capabilities: list[str] | None = None,
    ):
        self.provider = provider
        self.available_capabilities = available_capabilities or [
            "technical_analysis",
            "macro_analysis",
            "data_fetch",
            "onchain_analysis",
            "backtest",
            "sentiment_analysis",
            "risk_assessment",
            "report_writing",
            "code_generation",
            "web_search",
        ]
    
    async def decompose(self, request: CollaborationRequest) -> list[Task]:
        """
        Decompose a collaboration request into tasks.
        
        Args:
            request: The collaboration request to decompose
            
        Returns:
            List of Task objects
        """
        prompt = DECOMPOSE_PROMPT_TEMPLATE.format(
            goal=request.goal,
            context=json.dumps(request.context, indent=2, ensure_ascii=False),
            capabilities=", ".join(self.available_capabilities),
            mode=request.mode.value,
        )
        
        try:
            response = await self.provider.chat(
                messages=[
                    {"role": "system", "content": DECOMPOSE_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
            )
            
            content = response.content or ""
            
            if content.startswith("```"):
                content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            
            result = json.loads(content)
            
            tasks = []
            task_name_to_id: dict[str, str] = {}
            
            for task_data in result.get("tasks", []):
                task = Task(
                    name=task_data.get("name", "unnamed_task"),
                    description=task_data.get("description", ""),
                    required_capabilities=task_data.get("required_capabilities", []),
                    dependencies=[],
                    priority=task_data.get("priority", 5),
                    input_data=request.context,
                    timeout_seconds=300,
                )
                tasks.append(task)
                task_name_to_id[task.name] = task.id
            
            for i, task_data in enumerate(result.get("tasks", [])):
                dep_names = task_data.get("dependencies", [])
                if i < len(tasks):
                    dep_ids = [
                        task_name_to_id[name]
                        for name in dep_names
                        if name in task_name_to_id
                    ]
                    tasks[i].dependencies = dep_ids
            
            logger.info(f"Decomposed goal into {len(tasks)} tasks")
            return tasks
            
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse decomposition result: {e}")
            return self._create_fallback_tasks(request)
        except Exception as e:
            logger.error(f"Task decomposition failed: {e}")
            return self._create_fallback_tasks(request)
    
    def _create_fallback_tasks(self, request: CollaborationRequest) -> list[Task]:
        """Create simple fallback tasks when LLM decomposition fails."""
        return [
            Task(
                name="analyze_goal",
                description=f"Analyze and address the goal: {request.goal}",
                required_capabilities=[],
                dependencies=[],
                priority=10,
                input_data=request.context,
            )
        ]
    
    def get_task_dependencies_graph(self, tasks: list[Task]) -> dict[str, list[str]]:
        """Build a dependency graph from tasks."""
        return {task.id: task.dependencies for task in tasks}
    
    def get_execution_order(self, tasks: list[Task]) -> list[list[Task]]:
        """
        Get tasks grouped by execution order (topological sort).
        Tasks in the same group can be executed in parallel.
        """
        task_map = {t.id: t for t in tasks}
        in_degree = {t.id: 0 for t in tasks}
        
        for task in tasks:
            for dep_id in task.dependencies:
                if dep_id in in_degree:
                    in_degree[task.id] += 1
        
        result: list[list[Task]] = []
        remaining = set(in_degree.keys())
        
        while remaining:
            ready = [
                task_map[tid]
                for tid in remaining
                if in_degree[tid] == 0
            ]
            
            if not ready:
                logger.warning("Circular dependency detected in tasks")
                ready = [task_map[tid] for tid in list(remaining)[:1]]
            
            ready.sort(key=lambda t: -t.priority)
            result.append(ready)
            
            for task in ready:
                remaining.discard(task.id)
                for other_id, other_task in task_map.items():
                    if task.id in other_task.dependencies:
                        in_degree[other_id] -= 1
        
        return result
