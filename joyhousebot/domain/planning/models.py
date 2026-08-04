"""Pure, validated task graph specifications."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class PlanTask:
    task_id: str
    capability_id: str
    capability_version: str
    input: dict[str, Any]
    dependencies: tuple[str, ...] = ()
    timeout_seconds: int = 300
    max_attempts: int = 1
    name: str = ""

    def __post_init__(self) -> None:
        if not self.task_id.strip() or not self.capability_id.strip() or not self.capability_version:
            raise ValueError("plan task identity is incomplete")
        if self.timeout_seconds <= 0 or self.max_attempts < 1:
            raise ValueError("invalid plan task execution limits")


@dataclass(frozen=True, slots=True)
class PlanSpec:
    tasks: tuple[PlanTask, ...]
    max_concurrent: int = 4
    fail_fast: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.tasks:
            raise ValueError("plan requires at least one task")
        if not 1 <= self.max_concurrent <= 32:
            raise ValueError("plan concurrency must be between 1 and 32")
        ids = {task.task_id for task in self.tasks}
        if len(ids) != len(self.tasks):
            raise ValueError("plan task ids must be unique")
        graph = {task.task_id: task.dependencies for task in self.tasks}
        for task in self.tasks:
            unknown = set(task.dependencies) - ids
            if unknown:
                raise ValueError(f"task {task.task_id} has unknown dependencies: {sorted(unknown)}")
            if task.task_id in task.dependencies:
                raise ValueError(f"task {task.task_id} cannot depend on itself")
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(task_id: str) -> None:
            if task_id in visiting:
                raise ValueError("plan contains a dependency cycle")
            if task_id in visited:
                return
            visiting.add(task_id)
            for dependency in graph[task_id]:
                visit(dependency)
            visiting.remove(task_id)
            visited.add(task_id)

        for task_id in ids:
            visit(task_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tasks": [
                {**asdict(task), "dependencies": list(task.dependencies)} for task in self.tasks
            ],
            "max_concurrent": self.max_concurrent,
            "fail_fast": self.fail_fast,
            "metadata": self.metadata,
        }
