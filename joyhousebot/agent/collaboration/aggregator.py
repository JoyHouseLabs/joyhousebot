"""Result aggregator - combines task results into a unified summary."""

import json
from typing import TYPE_CHECKING

from loguru import logger

from joyhousebot.agent.collaboration.types import (
    CollaborationRequest,
    TaskResult,
    TaskStatus,
)

if TYPE_CHECKING:
    from joyhousebot.providers.base import LLMProvider


AGGREGATOR_SYSTEM_PROMPT = """You are a result aggregation specialist. Your job is to combine multiple task results into a coherent, unified summary.

Rules:
1. Identify key findings from each task
2. Note any agreements or disagreements between tasks
3. Highlight the most important insights
4. Structure the output clearly for decision-making

Respond with a well-structured summary."""

AGGREGATOR_PROMPT_TEMPLATE = """## Goal
{goal}

## Task Results
{task_results}

## Instructions
Aggregate these task results into a comprehensive summary:
1. Key findings from each analysis
2. Areas of agreement
3. Areas of disagreement or uncertainty
4. Most critical factors for decision-making
5. Overall assessment

Provide a clear, structured summary that will help make a final decision."""


class ResultAggregator:
    """Aggregates results from multiple tasks."""
    
    def __init__(self, provider: "LLMProvider"):
        self.provider = provider
    
    async def aggregate(
        self,
        request: CollaborationRequest,
        task_results: dict[str, TaskResult],
    ) -> str:
        """
        Aggregate task results into a unified summary.
        
        Args:
            request: The original collaboration request
            task_results: Results from all tasks
            
        Returns:
            Aggregated summary string
        """
        completed_results = {
            tid: result
            for tid, result in task_results.items()
            if result.status == TaskStatus.COMPLETED and result.output
        }
        
        if not completed_results:
            logger.warning("No completed tasks to aggregate")
            return "No task results available for aggregation."
        
        if len(completed_results) == 1:
            result = next(iter(completed_results.values()))
            return result.output or "No output from single task."
        
        results_text = self._format_results(completed_results)
        
        prompt = AGGREGATOR_PROMPT_TEMPLATE.format(
            goal=request.goal,
            task_results=results_text,
        )
        
        try:
            response = await self.provider.chat(
                messages=[
                    {"role": "system", "content": AGGREGATOR_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
            )
            
            aggregated = response.content or ""
            logger.info(f"Aggregated {len(completed_results)} task results")
            return aggregated
            
        except Exception as e:
            logger.error(f"Aggregation failed: {e}")
            return self._fallback_aggregation(completed_results)
    
    def _format_results(self, results: dict[str, TaskResult]) -> str:
        """Format task results for the prompt."""
        lines = []
        for i, (task_id, result) in enumerate(results.items(), 1):
            output = result.output or "(no output)"
            preview = output[:2000]
            lines.append(f"### Task {i} ({task_id})")
            lines.append(f"Status: {result.status.value}")
            lines.append(f"Result:\n{preview}")
            lines.append("")
        return "\n".join(lines)
    
    def _fallback_aggregation(self, results: dict[str, TaskResult]) -> str:
        """Simple fallback aggregation when LLM fails."""
        lines = ["# Task Results Summary\n"]
        for task_id, result in results.items():
            output = result.output or "(no output)"
            preview = output[:500]
            lines.append(f"## {task_id}")
            lines.append(preview)
            lines.append("")
        return "\n".join(lines)
    
    def extract_key_points(self, aggregated: str) -> list[str]:
        """Extract key points from aggregated result."""
        lines = aggregated.split("\n")
        key_points = []
        
        for line in lines:
            line = line.strip()
            if line.startswith(("-", "*", "•", "1.", "2.", "3.", "4.", "5.")):
                key_points.append(line.lstrip("-*• ").lstrip("0123456789. "))
        
        return key_points[:10]
