"""Multi-Agent Collaboration System.

This module provides a framework for multiple agents to collaborate on complex tasks.
It supports task decomposition, parallel execution, result aggregation, and feedback loops.
"""

from joyhousebot.agent.collaboration.types import (
    AgentCapability,
    AgentProfile,
    CollaborationMode,
    CollaborationRequest,
    CollaborationResult,
    CollaborationTrace,
    DecisionFactor,
    DeliberationMode,
    DeliberationRequest,
    DeliberationResult,
    DeliberationTrace,
    FeedbackRecord,
    LLMCallRecord,
    RoundResult,
    RoundTopic,
    Task,
    TaskExecutionTrace,
    TaskResult,
    TaskStatus,
    ToolCallRecord,
)
from joyhousebot.agent.collaboration.orchestrator import Orchestrator
from joyhousebot.agent.collaboration.options import OrchestratorOptions
from joyhousebot.agent.collaboration.deliberation import DeliberationEngine

__all__ = [
    "AgentCapability",
    "AgentProfile",
    "CollaborationMode",
    "CollaborationRequest",
    "CollaborationResult",
    "CollaborationTrace",
    "DecisionFactor",
    "DeliberationEngine",
    "DeliberationMode",
    "DeliberationRequest",
    "DeliberationResult",
    "DeliberationTrace",
    "FeedbackRecord",
    "LLMCallRecord",
    "Orchestrator",
    "OrchestratorOptions",
    "RoundResult",
    "RoundTopic",
    "Task",
    "TaskExecutionTrace",
    "TaskResult",
    "TaskStatus",
    "ToolCallRecord",
]
