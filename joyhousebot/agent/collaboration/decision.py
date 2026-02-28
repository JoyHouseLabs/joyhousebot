"""Decision engine - makes final decisions based on aggregated results."""

import json
from typing import TYPE_CHECKING, Any

from loguru import logger

from joyhousebot.agent.collaboration.types import (
    CollaborationRequest,
    CollaborationResult,
    DecisionFactor,
    TaskResult,
    TaskStatus,
)

if TYPE_CHECKING:
    from joyhousebot.providers.base import LLMProvider


DECISION_SYSTEM_PROMPT = """You are a decision-making specialist. Your job is to make a clear, well-reasoned decision based on the analysis results.

Rules:
1. Consider all the evidence presented
2. Weigh the pros and cons
3. Provide a clear decision (not ambiguous)
4. Explain your reasoning
5. Indicate your confidence level (0.0 to 1.0)

Respond with ONLY valid JSON, no markdown fences."""

DECISION_PROMPT_TEMPLATE = """## Goal
{goal}

## Context
{context}

## Aggregated Analysis
{aggregated}

## Task Results Summary
{task_summary}

## Instructions
Based on the above analysis, make a decision.

Respond in this JSON format:
{{
  "decision": "your decision (e.g., buy, sell, hold, proceed, abort, etc.)",
  "confidence": 0.75,
  "reasoning": "Explanation of your decision...",
  "factors": [
    {{
      "name": "factor name",
      "value": "factor value or finding",
      "weight": 1.0,
      "confidence": 0.8
    }}
  ],
  "execution_plan": {{
    "action": "specific action to take",
    "parameters": {{}},
    "risk_level": "low|medium|high",
    "expected_outcome": "what we expect to happen"
  }}
}}
"""


class DecisionEngine:
    """Makes final decisions based on aggregated results."""
    
    def __init__(
        self,
        provider: "LLMProvider",
        default_model: str | None = None,
    ):
        self.provider = provider
        self.default_model = default_model
    
    async def decide(
        self,
        request: CollaborationRequest,
        aggregated: str,
        task_results: dict[str, TaskResult] | None = None,
    ) -> CollaborationResult:
        """
        Make a decision based on aggregated results.
        
        Args:
            request: The original collaboration request
            aggregated: Aggregated analysis results
            task_results: Individual task results for reference
            
        Returns:
            CollaborationResult with the decision
        """
        task_summary = self._summarize_tasks(task_results or {})
        
        prompt = DECISION_PROMPT_TEMPLATE.format(
            goal=request.goal,
            context=json.dumps(request.context, indent=2, ensure_ascii=False),
            aggregated=aggregated[:4000],
            task_summary=task_summary,
        )
        
        try:
            response = await self.provider.chat(
                messages=[
                    {"role": "system", "content": DECISION_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                model=self.default_model,
                temperature=0.2,
            )
            
            content = response.content or ""
            
            if content.startswith("```"):
                content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            
            result = json.loads(content)
            
            factors = [
                DecisionFactor(
                    name=f.get("name", ""),
                    value=f.get("value", ""),
                    weight=f.get("weight", 1.0),
                    source_agent=f.get("source_agent", ""),
                    confidence=f.get("confidence", 0.5),
                )
                for f in result.get("factors", [])
            ]
            
            agent_contributions = {}
            if task_results:
                for tid, tr in task_results.items():
                    if tr.status == TaskStatus.COMPLETED and tr.output:
                        agent_contributions[tid] = tr.output[:500]
            
            decision = CollaborationResult(
                goal=request.goal,
                decision=result.get("decision", "unknown"),
                confidence=result.get("confidence", 0.5),
                reasoning=result.get("reasoning", ""),
                factors=factors,
                task_results=task_results or {},
                agent_contributions=agent_contributions,
                execution_plan=result.get("execution_plan"),
            )
            
            logger.info(f"Decision made: {decision.decision} (confidence: {decision.confidence})")
            return decision
            
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse decision JSON: {e}")
            return self._create_fallback_result(request, aggregated)
        except Exception as e:
            logger.error(f"Decision making failed: {e}")
            return self._create_fallback_result(request, aggregated)
    
    def _summarize_tasks(self, task_results: dict[str, TaskResult]) -> str:
        """Create a summary of task results."""
        if not task_results:
            return "No task results."
        
        completed = sum(1 for r in task_results.values() if r.status == TaskStatus.COMPLETED)
        failed = sum(1 for r in task_results.values() if r.status == TaskStatus.FAILED)
        total = len(task_results)
        
        return f"Tasks: {completed}/{total} completed, {failed} failed"
    
    def _create_fallback_result(
        self,
        request: CollaborationRequest,
        aggregated: str,
    ) -> CollaborationResult:
        """Create a fallback result when decision parsing fails."""
        return CollaborationResult(
            goal=request.goal,
            decision="inconclusive",
            confidence=0.0,
            reasoning=f"Decision engine failed. Raw analysis: {aggregated[:1000]}",
            factors=[],
        )
    
    async def refine_decision(
        self,
        result: CollaborationResult,
        feedback: str,
    ) -> CollaborationResult:
        """Refine a decision based on feedback."""
        prompt = f"""## Previous Decision
Decision: {result.decision}
Confidence: {result.confidence}
Reasoning: {result.reasoning}

## Feedback
{feedback}

## Instructions
Consider the feedback and provide an updated decision in the same JSON format."""

        try:
            response = await self.provider.chat(
                messages=[
                    {"role": "system", "content": DECISION_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                model=self.default_model,
                temperature=0.2,
            )
            
            content = response.content or ""
            if content.startswith("```"):
                content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            
            parsed = json.loads(content)
            
            return CollaborationResult(
                goal=result.goal,
                decision=parsed.get("decision", result.decision),
                confidence=parsed.get("confidence", result.confidence),
                reasoning=parsed.get("reasoning", result.reasoning),
                factors=result.factors,
                task_results=result.task_results,
                agent_contributions=result.agent_contributions,
                execution_plan=parsed.get("execution_plan", result.execution_plan),
            )
            
        except Exception as e:
            logger.error(f"Decision refinement failed: {e}")
            return result
