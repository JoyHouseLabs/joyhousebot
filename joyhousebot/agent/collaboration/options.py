"""Orchestrator configuration options."""

from pydantic import BaseModel, Field

from joyhousebot.agent.collaboration.types import TaskStatus


class InterventionPoint(BaseModel):
    """Human intervention point configuration."""
    id: str
    phase: str  # before_task | after_task | after_decompose | before_decision | final_review
    description: str = ""
    timeout_seconds: int = 300
    auto_action: str = "continue"  # continue | abort | retry


class OrchestratorOptions(BaseModel):
    """Orchestrator configuration options."""
    
    max_concurrent_tasks: int = 4
    
    task_timeout_seconds: int = 300
    total_timeout_seconds: int = 1800
    
    max_retries: int = 2
    retry_delay_seconds: float = 1.0
    
    checkpoint_enabled: bool = True
    
    require_final_review: bool = False
    intervention_points: list[InterventionPoint] = Field(default_factory=list)
    
    trace_enabled: bool = True
    trace_include_llm_calls: bool = True
    trace_include_tool_calls: bool = True
    
    max_cost_usd: float | None = None
    
    feedback_enabled: bool = True
    feedback_reminder_days: int = 7
    
    required_task_failure_mode: str = "abort"  # abort | continue_with_warning
