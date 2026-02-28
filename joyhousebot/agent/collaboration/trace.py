"""Trace management for collaboration auditing and replay."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from loguru import logger

from joyhousebot.agent.collaboration.types import (
    CollaborationRequest,
    CollaborationResult,
    CollaborationTrace,
    LLMCallRecord,
    ProgressEvent,
    Task,
    TaskExecutionTrace,
    TaskResult,
    TaskStatus,
    ToolCallRecord,
)


class TraceManager:
    """Manages collaboration traces for storage and retrieval."""
    
    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.traces_dir = workspace / "collaboration" / "traces"
        self.traces_dir.mkdir(parents=True, exist_ok=True)
    
    def create_trace(
        self,
        request: CollaborationRequest,
    ) -> CollaborationTrace:
        """Create a new trace for a collaboration request."""
        trace = CollaborationTrace(
            goal=request.goal,
            request=request,
            created_at=datetime.now(),
        )
        
        month_dir = self.traces_dir / datetime.now().strftime("%Y-%m")
        month_dir.mkdir(parents=True, exist_ok=True)
        
        trace_file = month_dir / f"trace_{trace.trace_id}.json"
        trace.file_path = str(trace_file)
        
        return trace
    
    def save_trace(self, trace: CollaborationTrace):
        """Save trace to disk."""
        trace.mark_completed()
        
        if hasattr(trace, "file_path") and trace.file_path:
            trace_path = Path(trace.file_path)
        else:
            month_dir = self.traces_dir / datetime.now().strftime("%Y-%m")
            month_dir.mkdir(parents=True, exist_ok=True)
            trace_path = month_dir / f"trace_{trace.trace_id}.json"
        
        with open(trace_path, "w", encoding="utf-8") as f:
            f.write(trace.model_dump_json(indent=2))
        
        logger.debug(f"Trace saved: {trace_path}")
    
    def load_trace(self, trace_id: str) -> CollaborationTrace | None:
        """Load a trace by ID."""
        for month_dir in self.traces_dir.iterdir():
            if not month_dir.is_dir():
                continue
            
            trace_file = month_dir / f"trace_{trace_id}.json"
            if trace_file.exists():
                with open(trace_file, "r", encoding="utf-8") as f:
                    return CollaborationTrace.model_validate_json(f.read())
        
        return None
    
    def list_traces(
        self,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List trace summaries."""
        traces = []
        
        for month_dir in sorted(self.traces_dir.iterdir(), reverse=True):
            if not month_dir.is_dir():
                continue
            
            for trace_file in sorted(month_dir.glob("trace_*.json"), reverse=True):
                try:
                    with open(trace_file, "r", encoding="utf-8") as f:
                        trace = CollaborationTrace.model_validate_json(f.read())
                    traces.append(trace.to_summary())
                except Exception as e:
                    logger.warning(f"Failed to load trace {trace_file}: {e}")
        
        return traces[offset:offset + limit]
    
    def search_traces(
        self,
        query: str,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Search traces by goal text."""
        results = []
        query_lower = query.lower()
        
        for month_dir in self.traces_dir.iterdir():
            if not month_dir.is_dir():
                continue
            
            for trace_file in month_dir.glob("trace_*.json"):
                try:
                    with open(trace_file, "r", encoding="utf-8") as f:
                        trace = CollaborationTrace.model_validate_json(f.read())
                    
                    if query_lower in trace.goal.lower():
                        results.append(trace.to_summary())
                        
                        if len(results) >= limit:
                            return results
                except Exception:
                    continue
        
        return results


class ProgressTracker:
    """Tracks and broadcasts progress events."""
    
    def __init__(
        self,
        trace_id: str,
        listeners: list[Callable[[ProgressEvent], None]] | None = None,
    ):
        self.trace_id = trace_id
        self.listeners = listeners or []
        self.events: list[ProgressEvent] = []
    
    def add_listener(self, listener: Callable[[ProgressEvent], None]):
        """Add a progress event listener."""
        self.listeners.append(listener)
    
    async def emit(self, event: ProgressEvent):
        """Emit a progress event to all listeners."""
        self.events.append(event)
        
        for listener in self.listeners:
            try:
                if asyncio.iscoroutinefunction(listener):
                    await listener(event)
                else:
                    listener(event)
            except Exception as e:
                logger.warning(f"Progress listener error: {e}")
    
    async def on_phase_started(self, phase: str):
        """Emit phase started event."""
        await self.emit(ProgressEvent(
            type="phase_started",
            trace_id=self.trace_id,
            message=f"Phase started: {phase}",
        ))
    
    async def on_task_started(self, task_id: str, agent_id: str):
        """Emit task started event."""
        await self.emit(ProgressEvent(
            type="task_started",
            trace_id=self.trace_id,
            task_id=task_id,
            agent_id=agent_id,
        ))
    
    async def on_task_progress(
        self,
        task_id: str,
        message: str,
        progress: float,
    ):
        """Emit task progress event."""
        await self.emit(ProgressEvent(
            type="task_progress",
            trace_id=self.trace_id,
            task_id=task_id,
            message=message,
            progress=progress,
        ))
    
    async def on_task_completed(
        self,
        task_id: str,
        result_preview: str,
    ):
        """Emit task completed event."""
        await self.emit(ProgressEvent(
            type="task_completed",
            trace_id=self.trace_id,
            task_id=task_id,
            result_preview=result_preview[:500],
        ))
    
    async def on_task_failed(self, task_id: str, error: str):
        """Emit task failed event."""
        await self.emit(ProgressEvent(
            type="task_failed",
            trace_id=self.trace_id,
            task_id=task_id,
            message=error,
        ))
    
    async def on_decision_made(self, decision: str, confidence: float):
        """Emit decision made event."""
        await self.emit(ProgressEvent(
            type="decision_made",
            trace_id=self.trace_id,
            message=f"Decision: {decision} (confidence: {confidence:.2f})",
        ))


import asyncio
