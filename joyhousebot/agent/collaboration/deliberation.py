"""Multi-round deliberation engine for progressive problem solving."""

import json
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable, Awaitable

from loguru import logger

from joyhousebot.agent.collaboration.types import (
    DeliberationMode,
    DeliberationRequest,
    DeliberationResult,
    DeliberationTrace,
    DecisionFactor,
    RoundResult,
    RoundTopic,
    Task,
    TaskResult,
    TaskStatus,
)

if TYPE_CHECKING:
    from joyhousebot.providers.base import LLMProvider


TOPIC_GENERATION_SYSTEM_PROMPT = """You are a strategic planning expert. Your job is to break down a complex goal into a series of progressive discussion topics.

Each topic should:
1. Build upon the conclusions of previous topics
2. Be specific and actionable
3. Have clear focus questions to guide the discussion
4. Lead towards a final comprehensive solution

Respond with ONLY valid JSON, no markdown fences."""

TOPIC_GENERATION_PROMPT = """## Goal
{goal}

## Context
{context}

## Mode
{mode}

## Instructions
Generate {num_topics} progressive discussion topics that will help reach a comprehensive solution.

Each topic should:
- Have a clear title and description
- Include 2-4 focus questions
- Build logically towards the final goal

Example progression for "open a bubble tea shop":
1. "Feasibility Analysis" - Can this business succeed?
2. "Location Strategy" - Where should we open?
3. "Operational Planning" - How do we execute?

Respond in this JSON format:
{{
  "topics": [
    {{
      "title": "Topic Title",
      "description": "What this round will explore",
      "focus_questions": ["Question 1?", "Question 2?", "Question 3?"],
      "dependencies": []
    }}
  ]
}}
"""


ROUND_EXECUTION_SYSTEM_PROMPT = """You are a collaborative analyst. Your job is to analyze the given topic and provide thorough insights.

Consider:
1. All previous round conclusions
2. The specific focus questions for this round
3. Practical implications and recommendations

Provide a comprehensive analysis that can inform subsequent rounds."""


ROUND_SYNTHESIS_SYSTEM_PROMPT = """You are a synthesis expert. Your job is to synthesize multiple analyses into clear conclusions.

Extract:
1. Key findings (list of main points)
2. A clear conclusion for this round
3. Open questions that need further exploration (if any)

Respond with ONLY valid JSON, no markdown fences."""


FINAL_REPORT_SYSTEM_PROMPT = """You are a strategic advisor. Your job is to compile a comprehensive final report from multiple rounds of deliberation.

The report should include:
1. Executive Summary (2-3 paragraphs)
2. Detailed Analysis (synthesis of all rounds)
3. Clear Decision/Recommendation
4. Action Items (specific next steps)
5. Risks and Mitigations
6. Overall Confidence Assessment

Be thorough but practical. This report will guide real-world decision making."""


class DeliberationEngine:
    """Engine for multi-round progressive deliberation."""
    
    def __init__(
        self,
        provider: "LLMProvider",
        model: str | None = None,
        agents: dict[str, Any] | None = None,
        executor: Any | None = None,
        progress_callback: Callable[[str, dict], Awaitable[None]] | None = None,
    ):
        self.provider = provider
        self.model = model
        self.agents = agents or {}
        self.executor = executor
        self.progress_callback = progress_callback
    
    async def _emit_progress(self, event_type: str, payload: dict[str, Any]):
        """Emit progress event if callback is set."""
        if self.progress_callback:
            try:
                await self.progress_callback(event_type, payload)
            except Exception as e:
                logger.warning(f"Progress callback error: {e}")
    
    async def generate_topics(
        self,
        request: DeliberationRequest,
    ) -> list[RoundTopic]:
        """Generate discussion topics for the deliberation."""
        
        if request.initial_topics and not request.auto_generate_topics:
            topics = []
            for i, t in enumerate(request.initial_topics):
                if isinstance(t, RoundTopic):
                    topic = RoundTopic(
                        id=t.id or f"topic_{i+1}",
                        round_number=i+1,
                        title=t.title,
                        description=t.description,
                        focus_questions=t.focus_questions,
                        dependencies=t.dependencies,
                        generated=False,
                    )
                else:
                    topic = RoundTopic(
                        id=t.get("id", f"topic_{i+1}"),
                        round_number=i+1,
                        title=t.get("title", f"Round {i+1}"),
                        description=t.get("description", ""),
                        focus_questions=t.get("focus_questions", []),
                        dependencies=t.get("dependencies", []),
                        generated=False,
                    )
                topics.append(topic)
            return topics
        
        prompt = TOPIC_GENERATION_PROMPT.format(
            goal=request.goal,
            context=json.dumps(request.context, indent=2, ensure_ascii=False),
            mode=request.mode.value,
            num_topics=min(request.max_rounds, 5),
        )
        
        try:
            response = await self.provider.chat(
                messages=[
                    {"role": "system", "content": TOPIC_GENERATION_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.4,
                model=self.model,
            )
            
            content = response.content or ""
            if content.startswith("```"):
                content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            
            result = json.loads(content)
            
            topics = []
            valid_ids = set()
            for i, topic_data in enumerate(result.get("topics", [])):
                topic_id = f"topic_{i+1}"
                valid_ids.add(topic_id)
                topic = RoundTopic(
                    id=topic_id,
                    round_number=i+1,
                    title=topic_data.get("title", f"Round {i+1}"),
                    description=topic_data.get("description", ""),
                    focus_questions=topic_data.get("focus_questions", []),
                    dependencies=[f"topic_{i}"] if i > 0 else [],
                    generated=True,
                )
                topics.append(topic)
            
            logger.info(f"Generated {len(topics)} deliberation topics")
            return topics
            
        except Exception as e:
            logger.error(f"Topic generation failed: {e}")
            return self._create_fallback_topics(request)
    
    def _create_fallback_topics(self, request: DeliberationRequest) -> list[RoundTopic]:
        """Create simple fallback topics."""
        return [
            RoundTopic(
                id="topic_1",
                round_number=1,
                title="Initial Analysis",
                description=f"Analyze the goal: {request.goal}",
                focus_questions=["What are the key considerations?"],
                generated=False,
            ),
            RoundTopic(
                id="topic_2",
                round_number=2,
                title="Detailed Planning",
                description="Develop specific recommendations",
                focus_questions=["What are the recommended actions?"],
                dependencies=["topic_1"],
                generated=False,
            ),
        ]
    
    async def execute_round(
        self,
        topic: RoundTopic,
        request: DeliberationRequest,
        previous_results: list[RoundResult],
    ) -> RoundResult:
        """Execute a single deliberation round."""
        
        round_result = RoundResult(
            round_id=f"round_{topic.round_number}",
            round_number=topic.round_number,
            topic=topic,
            started_at=datetime.now(),
        )
        
        context_str = self._build_context(request, previous_results)
        
        prompt = f"""## Round Topic: {topic.title}

{topic.description}

## Focus Questions
{chr(10).join(f"- {q}" for q in topic.focus_questions)}

## Context from Previous Rounds
{context_str}

## Original Goal
{request.goal}

## Instructions
Analyze this topic thoroughly. Address each focus question.
Provide specific, actionable insights that will inform subsequent rounds."""
        
        try:
            response = await self.provider.chat(
                messages=[
                    {"role": "system", "content": ROUND_EXECUTION_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.6,
                model=self.model,
            )
            
            analysis = response.content or ""
            
            synthesis = await self._synthesize_round(topic, analysis, previous_results)
            
            round_result.conclusion = synthesis.get("conclusion", "")
            round_result.key_findings = synthesis.get("key_findings", [])
            round_result.open_questions = synthesis.get("open_questions", [])
            
            round_result.completed_at = datetime.now()
            round_result.duration_ms = int(
                (round_result.completed_at - round_result.started_at).total_seconds() * 1000
            )
            
            logger.info(f"Round {topic.round_number} completed: {topic.title}")
            
        except Exception as e:
            logger.error(f"Round {topic.round_number} failed: {e}")
            round_result.conclusion = f"Analysis failed: {str(e)}"
            round_result.completed_at = datetime.now()
        
        return round_result
    
    async def _synthesize_round(
        self,
        topic: RoundTopic,
        analysis: str,
        previous_results: list[RoundResult],
    ) -> dict[str, Any]:
        """Synthesize round analysis into structured conclusions."""
        
        prev_context = ""
        if previous_results:
            prev_context = "\n\n## Previous Round Conclusions\n"
            for pr in previous_results:
                prev_context += f"- Round {pr.round_number} ({pr.topic.title}): {pr.conclusion[:200]}\n"
        
        prompt = f"""## Analysis to Synthesize

{analysis[:3000]}
{prev_context}

## Instructions
Extract the key conclusions from this analysis.

Respond in this JSON format:
{{
  "conclusion": "A clear, concise conclusion (2-3 sentences)",
  "key_findings": ["Finding 1", "Finding 2", "Finding 3"],
  "open_questions": ["Any unresolved questions that need further exploration"]
}}
"""
        
        try:
            response = await self.provider.chat(
                messages=[
                    {"role": "system", "content": ROUND_SYNTHESIS_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                model=self.model,
            )
            
            content = response.content or ""
            if content.startswith("```"):
                content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            
            return json.loads(content)
            
        except Exception as e:
            logger.warning(f"Round synthesis failed: {e}")
            return {
                "conclusion": analysis[:500] if analysis else "No conclusion",
                "key_findings": [],
                "open_questions": [],
            }
    
    def _build_context(
        self,
        request: DeliberationRequest,
        previous_results: list[RoundResult],
    ) -> str:
        """Build context string from previous rounds."""
        if not previous_results:
            return "This is the first round. No previous context available."
        
        lines = []
        for pr in previous_results:
            lines.append(f"### Round {pr.round_number}: {pr.topic.title}")
            lines.append(f"Conclusion: {pr.conclusion}")
            if pr.key_findings:
                lines.append("Key Findings:")
                for f in pr.key_findings:
                    lines.append(f"  - {f}")
            lines.append("")
        
        return "\n".join(lines)
    
    async def generate_final_report(
        self,
        request: DeliberationRequest,
        round_results: list[RoundResult],
    ) -> DeliberationResult:
        """Generate final comprehensive report."""
        
        rounds_summary = self._summarize_rounds(round_results)
        
        prompt = f"""## Original Goal
{request.goal}

## Initial Context
{json.dumps(request.context, indent=2, ensure_ascii=False)}

## Deliberation Rounds Summary
{rounds_summary}

## Instructions
Compile a comprehensive final report based on all deliberation rounds.

Include:
1. A clear final decision/recommendation
2. Executive summary (2-3 paragraphs)
3. Key action items (specific steps to take)
4. Risks and mitigations
5. Overall confidence level (0.0 to 1.0)

Respond in this JSON format:
{{
  "final_decision": "clear decision or recommendation",
  "overall_confidence": 0.75,
  "executive_summary": "2-3 paragraph summary...",
  "detailed_report": "Full analysis report in markdown format",
  "action_items": [
    {{"priority": "high", "action": "specific action", "timeline": "when"}}
  ],
  "risks": [
    {{"risk": "description", "mitigation": "how to address", "severity": "high/medium/low"}}
  ],
  "recommendations": ["Recommendation 1", "Recommendation 2"]
}}
"""
        
        try:
            response = await self.provider.chat(
                messages=[
                    {"role": "system", "content": FINAL_REPORT_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                model=self.model,
            )
            
            content = response.content or ""
            if content.startswith("```"):
                content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            
            result = json.loads(content)
            
            deliberation_result = DeliberationResult(
                goal=request.goal,
                final_decision=result.get("final_decision", "inconclusive"),
                overall_confidence=result.get("overall_confidence", 0.5),
                executive_summary=result.get("executive_summary", ""),
                detailed_report=result.get("detailed_report", ""),
                round_results=round_results,
                action_items=result.get("action_items", []),
                risks=result.get("risks", []),
                recommendations=result.get("recommendations", []),
                total_rounds=len(round_results),
                total_tasks=sum(len(r.task_results) for r in round_results),
                completed_at=datetime.now(),
            )
            
            logger.info(f"Final report generated: {deliberation_result.final_decision}")
            return deliberation_result
            
        except Exception as e:
            logger.error(f"Final report generation failed: {e}")
            return self._create_fallback_result(request, round_results)
    
    def _summarize_rounds(self, round_results: list[RoundResult]) -> str:
        """Create a summary of all rounds."""
        lines = []
        for rr in round_results:
            lines.append(f"### Round {rr.round_number}: {rr.topic.title}")
            lines.append(f"Description: {rr.topic.description}")
            lines.append(f"Conclusion: {rr.conclusion}")
            if rr.key_findings:
                lines.append("Key Findings:")
                for f in rr.key_findings:
                    lines.append(f"  - {f}")
            if rr.open_questions:
                lines.append("Open Questions:")
                for q in rr.open_questions:
                    lines.append(f"  - {q}")
            lines.append("")
        return "\n".join(lines)
    
    def _create_fallback_result(
        self,
        request: DeliberationRequest,
        round_results: list[RoundResult],
    ) -> DeliberationResult:
        """Create a fallback result when report generation fails."""
        conclusions = [r.conclusion for r in round_results if r.conclusion]
        
        return DeliberationResult(
            goal=request.goal,
            final_decision="see detailed report",
            overall_confidence=0.5,
            executive_summary="\n\n".join(conclusions),
            detailed_report="Report generation failed. See individual round conclusions.",
            round_results=round_results,
            total_rounds=len(round_results),
            completed_at=datetime.now(),
        )
    
    async def deliberate(
        self,
        request: DeliberationRequest,
    ) -> tuple[DeliberationResult, DeliberationTrace]:
        """
        Execute full multi-round deliberation.
        
        Args:
            request: The deliberation request
            
        Returns:
            Tuple of (DeliberationResult, DeliberationTrace)
        """
        trace = DeliberationTrace(
            goal=request.goal,
            request=request,
            created_at=datetime.now(),
        )
        
        try:
            await self._emit_progress("deliberation_start", {
                "goal": request.goal,
                "max_rounds": request.max_rounds,
                "mode": request.mode.value,
            })
            
            topics = await self.generate_topics(request)
            trace.topics = topics
            
            await self._emit_progress("topics_generated", {
                "topics": [{"id": t.id, "title": t.title, "description": t.description} for t in topics],
            })
            
            round_results: list[RoundResult] = []
            
            for i, topic in enumerate(topics):
                if topic.dependencies:
                    dep_ids = set(topic.dependencies)
                    completed_ids = {r.topic.id for r in round_results}
                    if not dep_ids.issubset(completed_ids):
                        logger.warning(
                            f"Skipping topic {topic.id}: dependencies not met"
                        )
                        continue
                
                await self._emit_progress("round_start", {
                    "round_number": i + 1,
                    "total_rounds": len(topics),
                    "topic": {
                        "id": topic.id,
                        "title": topic.title,
                        "description": topic.description,
                        "focus_questions": topic.focus_questions,
                    },
                })
                
                round_result = await self.execute_round(
                    topic=topic,
                    request=request,
                    previous_results=round_results,
                )
                round_results.append(round_result)
                trace.round_results.append(round_result)
                
                await self._emit_progress("round_complete", {
                    "round_number": i + 1,
                    "topic": topic.title,
                    "conclusion": round_result.conclusion[:500] if round_result.conclusion else "",
                    "key_findings": round_result.key_findings[:5] if round_result.key_findings else [],
                })
                
                if len(round_results) >= request.max_rounds:
                    logger.info(f"Reached max rounds limit: {request.max_rounds}")
                    break
            
            await self._emit_progress("generating_report", {
                "rounds_completed": len(round_results),
            })
            
            final_result = await self.generate_final_report(request, round_results)
            trace.final_result = final_result
            trace.mark_completed()
            
            await self._emit_progress("deliberation_complete", {
                "final_decision": final_result.final_decision,
                "confidence": final_result.overall_confidence,
                "total_rounds": final_result.total_rounds,
            })
            
            return final_result, trace
            
        except Exception as e:
            logger.error(f"Deliberation failed: {e}")
            trace.mark_completed()
            
            await self._emit_progress("deliberation_error", {
                "error": str(e),
            })
            
            fallback_result = DeliberationResult(
                goal=request.goal,
                final_decision="error",
                overall_confidence=0.0,
                executive_summary=f"Deliberation failed: {str(e)}",
            )
            trace.final_result = fallback_result
            
            return fallback_result, trace
