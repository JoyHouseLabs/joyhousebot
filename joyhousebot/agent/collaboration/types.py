"""Core type definitions for Multi-Agent Collaboration System."""

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class CollaborationMode(str, Enum):
    """Collaboration execution mode."""
    PARALLEL = "parallel"
    SEQUENTIAL = "sequential"
    PIPELINE = "pipeline"
    MAP_REDUCE = "map_reduce"


class TaskStatus(str, Enum):
    """Task execution status."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    SKIPPED = "skipped"


class AgentCapability(BaseModel):
    """Agent capability declaration."""
    id: str
    name: str
    description: str = ""
    skills: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)


class AgentProfile(BaseModel):
    """Agent profile with capabilities."""
    agent_id: str
    name: str
    description: str = ""
    capabilities: list[AgentCapability] = Field(default_factory=list)
    model: str | None = None
    temperature: float = 0.7
    
    def has_capability(self, capability_id: str) -> bool:
        """Check if agent has a specific capability."""
        return any(c.id == capability_id for c in self.capabilities)
    
    def get_capability_score(self, required_capabilities: list[str]) -> float:
        """Calculate capability match score (0.0 - 1.0)."""
        if not required_capabilities:
            return 1.0
        matched = sum(1 for cap in required_capabilities if self.has_capability(cap))
        return matched / len(required_capabilities)


class Task(BaseModel):
    """Executable task definition."""
    id: str = Field(default_factory=lambda: str(uuid4())[:8])
    name: str
    description: str
    required_capabilities: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    priority: int = 0
    status: TaskStatus = TaskStatus.PENDING
    assigned_agent: str | None = None
    input_data: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int = 300
    
    def __lt__(self, other: "Task") -> bool:
        return self.priority > other.priority


class LLMCallRecord(BaseModel):
    """LLM call trace record."""
    timestamp: datetime = Field(default_factory=datetime.now)
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    cost_estimate: float | None = None
    prompt_preview: str = ""
    response_preview: str = ""


class ToolCallRecord(BaseModel):
    """Tool call trace record."""
    timestamp: datetime = Field(default_factory=datetime.now)
    tool_name: str = ""
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: str = ""
    duration_ms: int = 0
    success: bool = True


class TaskExecutionTrace(BaseModel):
    """Single task execution trace."""
    task_id: str
    agent_id: str = ""
    status: TaskStatus = TaskStatus.PENDING
    
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: int | None = None
    
    input: dict[str, Any] = Field(default_factory=dict)
    output: str | None = None
    output_artifacts: list[str] = Field(default_factory=list)
    
    llm_calls: list[LLMCallRecord] = Field(default_factory=list)
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    
    error: str | None = None
    retry_count: int = 0
    
    def mark_started(self):
        """Mark task as started."""
        self.status = TaskStatus.RUNNING
        self.started_at = datetime.now()
    
    def mark_completed(self, output: str):
        """Mark task as completed."""
        self.status = TaskStatus.COMPLETED
        self.completed_at = datetime.now()
        self.output = output
        if self.started_at:
            self.duration_ms = int((self.completed_at - self.started_at).total_seconds() * 1000)
    
    def mark_failed(self, error: str):
        """Mark task as failed."""
        self.status = TaskStatus.FAILED
        self.completed_at = datetime.now()
        self.error = error
        if self.started_at:
            self.duration_ms = int((self.completed_at - self.started_at).total_seconds() * 1000)


class TaskResult(BaseModel):
    """Task execution result."""
    task_id: str
    status: TaskStatus
    output: str | None = None
    error: str | None = None
    trace: TaskExecutionTrace | None = None
    artifacts: list[str] = Field(default_factory=list)


class CollaborationRequest(BaseModel):
    """Collaboration request definition."""
    goal: str
    context: dict[str, Any] = Field(default_factory=dict)
    mode: CollaborationMode = CollaborationMode.PARALLEL
    max_rounds: int = 3
    require_backtest: bool = False
    feedback_enabled: bool = True
    timeout_seconds: int = 1800
    
    request_id: str = Field(default_factory=lambda: str(uuid4())[:8])


class DecisionFactor(BaseModel):
    """A factor in the decision process."""
    name: str
    value: str
    weight: float = 1.0
    source_agent: str = ""
    confidence: float = 0.5


class CollaborationResult(BaseModel):
    """Collaboration result."""
    goal: str
    decision: str
    confidence: float = 0.0
    reasoning: str = ""
    factors: list[DecisionFactor] = Field(default_factory=list)
    
    task_results: dict[str, TaskResult] = Field(default_factory=dict)
    agent_contributions: dict[str, str] = Field(default_factory=dict)
    
    execution_plan: dict[str, Any] | None = None
    backtest_results: dict[str, Any] | None = None
    feedback_id: str | None = None
    
    created_at: datetime = Field(default_factory=datetime.now)


class CollaborationTrace(BaseModel):
    """Complete collaboration trace for replay and audit."""
    trace_id: str = Field(default_factory=lambda: str(uuid4())[:12])
    goal: str = ""
    created_at: datetime = Field(default_factory=datetime.now)
    completed_at: datetime | None = None
    
    request: CollaborationRequest | None = None
    tasks: list[Task] = Field(default_factory=list)
    task_traces: dict[str, TaskExecutionTrace] = Field(default_factory=dict)
    
    file_path: str | None = None
    
    final_result: CollaborationResult | None = None
    
    total_tokens: int = 0
    total_llm_calls: int = 0
    total_tool_calls: int = 0
    total_duration_ms: int = 0
    
    def mark_completed(self):
        """Mark trace as completed."""
        self.completed_at = datetime.now()
        if self.created_at:
            self.total_duration_ms = int((self.completed_at - self.created_at).total_seconds() * 1000)
        
        self.total_llm_calls = sum(
            len(t.llm_calls) for t in self.task_traces.values()
        )
        self.total_tool_calls = sum(
            len(t.tool_calls) for t in self.task_traces.values()
        )
        self.total_tokens = sum(
            sum(c.input_tokens + c.output_tokens for c in t.llm_calls)
            for t in self.task_traces.values()
        )
    
    def to_summary(self) -> dict[str, Any]:
        """Generate a summary of the trace."""
        return {
            "trace_id": self.trace_id,
            "goal": self.goal,
            "duration_ms": self.total_duration_ms,
            "total_tokens": self.total_tokens,
            "total_llm_calls": self.total_llm_calls,
            "total_tool_calls": self.total_tool_calls,
            "task_count": len(self.tasks),
            "completed_tasks": sum(
                1 for t in self.task_traces.values()
                if t.status == TaskStatus.COMPLETED
            ),
            "failed_tasks": sum(
                1 for t in self.task_traces.values()
                if t.status == TaskStatus.FAILED
            ),
            "decision": self.final_result.decision if self.final_result else None,
            "confidence": self.final_result.confidence if self.final_result else None,
        }


class ProgressEvent(BaseModel):
    """Progress event for real-time tracking."""
    type: str  # phase_started | task_started | task_progress | task_completed | task_failed | decision_made
    trace_id: str
    task_id: str | None = None
    agent_id: str | None = None
    message: str | None = None
    progress: float | None = None
    result_preview: str | None = None
    timestamp: datetime = Field(default_factory=datetime.now)


class FeedbackRecord(BaseModel):
    """Feedback record for decision verification."""
    feedback_id: str = Field(default_factory=lambda: str(uuid4())[:8])
    trace_id: str = ""
    goal: str = ""
    decision: str = ""
    confidence: float = 0.0
    reasoning: str = ""
    context: dict[str, Any] = Field(default_factory=dict)
    
    created_at: datetime = Field(default_factory=datetime.now)
    reminder_at: datetime | None = None
    verified_at: datetime | None = None
    
    actual_outcome: str | None = None
    outcome_correct: bool | None = None
    notes: str | None = None
    
    def verify(self, actual_outcome: str, correct: bool, notes: str | None = None):
        """Verify the decision with actual outcome."""
        self.actual_outcome = actual_outcome
        self.outcome_correct = correct
        self.notes = notes
        self.verified_at = datetime.now()


class DeliberationMode(str, Enum):
    """Multi-round deliberation mode."""
    PROGRESSIVE = "progressive"
    ITERATIVE = "iterative"
    EXPLORATORY = "exploratory"


class RoundTopic(BaseModel):
    """A single round topic in multi-round deliberation."""
    id: str = Field(default_factory=lambda: str(uuid4())[:8])
    round_number: int = 1
    title: str
    description: str
    focus_questions: list[str] = Field(default_factory=list)
    
    dependencies: list[str] = Field(default_factory=list)
    
    generated: bool = False


class RoundResult(BaseModel):
    """Result of a single deliberation round."""
    round_id: str
    round_number: int
    topic: RoundTopic
    
    conclusion: str = ""
    key_findings: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    
    task_results: dict[str, TaskResult] = Field(default_factory=dict)
    decision_factors: list[DecisionFactor] = Field(default_factory=list)
    
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: int | None = None


class DeliberationRequest(BaseModel):
    """Multi-round deliberation request."""
    goal: str
    context: dict[str, Any] = Field(default_factory=dict)
    mode: DeliberationMode = DeliberationMode.PROGRESSIVE
    
    max_rounds: int = 5
    auto_generate_topics: bool = True
    initial_topics: list[RoundTopic] = Field(default_factory=list)
    
    convergence_criteria: str = "all_topics_addressed"
    require_consensus: bool = False
    
    feedback_enabled: bool = True
    timeout_seconds: int = 3600
    
    request_id: str = Field(default_factory=lambda: str(uuid4())[:8])


class DeliberationResult(BaseModel):
    """Multi-round deliberation final result."""
    goal: str
    final_decision: str
    overall_confidence: float = 0.0
    
    executive_summary: str = ""
    detailed_report: str = ""
    
    round_results: list[RoundResult] = Field(default_factory=list)
    
    action_items: list[dict[str, Any]] = Field(default_factory=list)
    risks: list[dict[str, Any]] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    
    total_rounds: int = 0
    total_tasks: int = 0
    
    created_at: datetime = Field(default_factory=datetime.now)
    completed_at: datetime | None = None
    
    feedback_id: str | None = None


class DeliberationTrace(BaseModel):
    """Complete trace for multi-round deliberation."""
    trace_id: str = Field(default_factory=lambda: str(uuid4())[:12])
    goal: str = ""
    created_at: datetime = Field(default_factory=datetime.now)
    completed_at: datetime | None = None
    
    request: DeliberationRequest | None = None
    topics: list[RoundTopic] = Field(default_factory=list)
    round_results: list[RoundResult] = Field(default_factory=list)
    
    final_result: DeliberationResult | None = None
    
    total_tokens: int = 0
    total_llm_calls: int = 0
    total_duration_ms: int = 0
    
    file_path: str | None = None
    
    def mark_completed(self):
        """Mark trace as completed."""
        self.completed_at = datetime.now()
        if self.created_at:
            self.total_duration_ms = int((self.completed_at - self.created_at).total_seconds() * 1000)
    
    def to_summary(self) -> dict[str, Any]:
        """Generate a summary of the deliberation."""
        return {
            "trace_id": self.trace_id,
            "goal": self.goal,
            "total_rounds": len(self.round_results),
            "duration_ms": self.total_duration_ms,
            "total_tokens": self.total_tokens,
            "final_decision": self.final_result.final_decision if self.final_result else None,
            "confidence": self.final_result.overall_confidence if self.final_result else None,
        }
