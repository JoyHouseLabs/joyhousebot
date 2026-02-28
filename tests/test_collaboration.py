"""Tests for Multi-Agent Collaboration System."""

import asyncio
import json
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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
    FeedbackRecord,
    LLMCallRecord,
    RoundTopic,
    Task,
    TaskExecutionTrace,
    TaskResult,
    TaskStatus,
    ToolCallRecord,
)
from joyhousebot.agent.collaboration.task_decomposer import TaskDecomposer
from joyhousebot.agent.collaboration.dispatcher import TaskDispatcher
from joyhousebot.agent.collaboration.executor import TaskExecutor, ParallelExecutor
from joyhousebot.agent.collaboration.aggregator import ResultAggregator
from joyhousebot.agent.collaboration.decision import DecisionEngine
from joyhousebot.agent.collaboration.feedback import FeedbackLoop
from joyhousebot.agent.collaboration.trace import TraceManager, ProgressTracker
from joyhousebot.agent.collaboration.options import OrchestratorOptions


class TestTypes:
    """Test type definitions."""
    
    def test_agent_capability(self):
        cap = AgentCapability(
            id="technical_analysis",
            name="Technical Analysis",
            description="Analyze technical indicators",
            skills=["chart_reading", "indicator_analysis"],
            tools=["web_search"],
        )
        assert cap.id == "technical_analysis"
        assert len(cap.skills) == 2
    
    def test_agent_profile_capability_match(self):
        profile = AgentProfile(
            agent_id="finance_agent",
            name="Finance Agent",
            capabilities=[
                AgentCapability(id="technical_analysis", name="Technical Analysis"),
                AgentCapability(id="macro_analysis", name="Macro Analysis"),
            ],
        )
        
        assert profile.has_capability("technical_analysis") is True
        assert profile.has_capability("backtest") is False
        
        score = profile.get_capability_score(["technical_analysis"])
        assert score == 1.0
        
        score = profile.get_capability_score(["technical_analysis", "backtest"])
        assert score == 0.5
    
    def test_task_status_transitions(self):
        trace = TaskExecutionTrace(
            task_id="task_123",
            agent_id="agent_1",
        )
        
        assert trace.status == TaskStatus.PENDING
        
        trace.mark_started()
        assert trace.status == TaskStatus.RUNNING
        assert trace.started_at is not None
        
        trace.mark_completed("Task completed successfully")
        assert trace.status == TaskStatus.COMPLETED
        assert trace.output == "Task completed successfully"
        assert trace.duration_ms is not None
    
    def test_collaboration_trace_summary(self):
        trace = CollaborationTrace(
            goal="Test goal",
            tasks=[
                Task(name="task1", description="desc", status=TaskStatus.COMPLETED),
                Task(name="task2", description="desc", status=TaskStatus.FAILED),
            ],
            task_traces={
                "t1": TaskExecutionTrace(task_id="t1", status=TaskStatus.COMPLETED),
                "t2": TaskExecutionTrace(task_id="t2", status=TaskStatus.FAILED),
            },
        )
        
        trace.mark_completed()
        summary = trace.to_summary()
        
        assert summary["task_count"] == 2
        assert summary["completed_tasks"] == 1
        assert summary["failed_tasks"] == 1
    
    def test_feedback_record_verification(self):
        record = FeedbackRecord(
            trace_id="trace_123",
            goal="Test goal",
            decision="buy",
            confidence=0.75,
        )
        
        assert record.verified_at is None
        
        record.verify("price went up", correct=True, notes="Good decision")
        
        assert record.verified_at is not None
        assert record.actual_outcome == "price went up"
        assert record.outcome_correct is True


class TestTaskDecomposer:
    """Test task decomposition."""
    
    @pytest.fixture
    def mock_provider(self):
        provider = AsyncMock()
        provider.chat = AsyncMock(return_value=MagicMock(
            content=json.dumps({
                "tasks": [
                    {
                        "name": "technical_analysis",
                        "description": "Analyze price trends and indicators",
                        "required_capabilities": ["technical_analysis"],
                        "dependencies": [],
                        "priority": 8
                    },
                    {
                        "name": "macro_analysis",
                        "description": "Analyze macro economic factors",
                        "required_capabilities": ["macro_analysis"],
                        "dependencies": [],
                        "priority": 7
                    },
                    {
                        "name": "final_recommendation",
                        "description": "Combine analyses into recommendation",
                        "required_capabilities": ["report_writing"],
                        "dependencies": ["technical_analysis", "macro_analysis"],
                        "priority": 9
                    }
                ]
            })
        ))
        return provider
    
    @pytest.mark.asyncio
    async def test_decompose_goal(self, mock_provider):
        decomposer = TaskDecomposer(mock_provider)
        
        request = CollaborationRequest(
            goal="Should I buy BTC at $67000?",
            context={"current_price": "67000"},
        )
        
        tasks = await decomposer.decompose(request)
        
        assert len(tasks) == 3
        assert tasks[0].name == "technical_analysis"
        assert len(tasks[2].dependencies) == 2
    
    @pytest.mark.asyncio
    async def test_decompose_fallback(self):
        provider = AsyncMock()
        provider.chat = AsyncMock(side_effect=Exception("LLM error"))
        
        decomposer = TaskDecomposer(provider)
        request = CollaborationRequest(goal="Test goal")
        
        tasks = await decomposer.decompose(request)
        
        assert len(tasks) == 1
        assert "analyze_goal" in tasks[0].name.lower()
    
    def test_execution_order(self):
        tasks = [
            Task(id="t1", name="task1", description="", dependencies=[]),
            Task(id="t2", name="task2", description="", dependencies=["t1"]),
            Task(id="t3", name="task3", description="", dependencies=["t1"]),
            Task(id="t4", name="task4", description="", dependencies=["t2", "t3"]),
        ]
        
        decomposer = TaskDecomposer(AsyncMock())
        order = decomposer.get_execution_order(tasks)
        
        assert len(order) == 3
        assert len(order[0]) == 1
        assert order[0][0].id == "t1"
        assert len(order[1]) == 2
        assert {t.id for t in order[1]} == {"t2", "t3"}
        assert order[2][0].id == "t4"


class TestTaskDispatcher:
    """Test task dispatching."""
    
    @pytest.fixture
    def agents(self):
        return {
            "technical": AgentProfile(
                agent_id="technical",
                name="Technical Analyst",
                capabilities=[
                    AgentCapability(id="technical_analysis", name="Tech"),
                    AgentCapability(id="indicator_analysis", name="Indicators"),
                ],
            ),
            "macro": AgentProfile(
                agent_id="macro",
                name="Macro Analyst",
                capabilities=[
                    AgentCapability(id="macro_analysis", name="Macro"),
                    AgentCapability(id="policy_analysis", name="Policy"),
                ],
            ),
            "general": AgentProfile(
                agent_id="general",
                name="General Agent",
                capabilities=[
                    AgentCapability(id="technical_analysis", name="Tech"),
                    AgentCapability(id="macro_analysis", name="Macro"),
                    AgentCapability(id="report_writing", name="Writing"),
                ],
            ),
        }
    
    def test_dispatch_best_match(self, agents):
        dispatcher = TaskDispatcher(agents, strategy="best_match")
        
        tasks = [
            Task(id="t1", name="task1", description="", required_capabilities=["technical_analysis"]),
            Task(id="t2", name="task2", description="", required_capabilities=["macro_analysis"]),
            Task(id="t3", name="task3", description="", required_capabilities=["report_writing"]),
        ]
        
        assignments = dispatcher.dispatch(tasks)
        
        assert assignments["t1"] in ["technical", "general"]
        assert assignments["t2"] in ["macro", "general"]
        assert assignments["t3"] == "general"
    
    def test_dispatch_least_loaded(self, agents):
        dispatcher = TaskDispatcher(agents, strategy="least_loaded")
        
        tasks = [
            Task(id=f"t{i}", name=f"task{i}", description="", required_capabilities=["technical_analysis"])
            for i in range(6)
        ]
        
        assignments = dispatcher.dispatch(tasks)
        
        load = dispatcher.get_load_summary()
        assert sum(load.values()) == 6
    
    def test_dispatch_fallback(self):
        agents = {
            "agent1": AgentProfile(
                agent_id="agent1",
                name="Agent 1",
                capabilities=[],
            ),
        }
        
        dispatcher = TaskDispatcher(agents)
        tasks = [
            Task(id="t1", name="task1", description="", required_capabilities=["unknown_capability"]),
        ]
        
        assignments = dispatcher.dispatch(tasks)
        
        assert "t1" in assignments


class TestTaskExecutor:
    """Test task execution."""
    
    @pytest.fixture
    def mock_provider(self):
        provider = AsyncMock()
        provider.chat = AsyncMock(return_value=MagicMock(
            content="Task analysis result: The trend is positive."
        ))
        return provider
    
    @pytest.fixture
    def agent(self):
        return AgentProfile(
            agent_id="test_agent",
            name="Test Agent",
            model="gpt-4",
            temperature=0.7,
        )
    
    @pytest.mark.asyncio
    async def test_execute_task_success(self, mock_provider, agent):
        with tempfile.TemporaryDirectory() as tmpdir:
            executor = TaskExecutor(
                provider=mock_provider,
                workspace=Path(tmpdir),
                max_retries=2,
            )
            
            task = Task(
                id="t1",
                name="Test Task",
                description="Analyze the data",
                timeout_seconds=60,
            )
            
            result = await executor.execute(task, agent)
            
            assert result.status == TaskStatus.COMPLETED
            assert "positive" in result.output.lower()
            assert result.trace is not None
            assert result.trace.duration_ms is not None
    
    @pytest.mark.asyncio
    async def test_execute_task_failure(self, agent):
        provider = AsyncMock()
        provider.chat = AsyncMock(side_effect=Exception("API error"))
        
        with tempfile.TemporaryDirectory() as tmpdir:
            executor = TaskExecutor(
                provider=provider,
                workspace=Path(tmpdir),
                max_retries=1,
            )
            
            task = Task(id="t1", name="Test", description="", timeout_seconds=10)
            result = await executor.execute(task, agent)
            
            assert result.status == TaskStatus.FAILED
            assert "API error" in result.error


class TestResultAggregator:
    """Test result aggregation."""
    
    @pytest.fixture
    def mock_provider(self):
        provider = AsyncMock()
        provider.chat = AsyncMock(return_value=MagicMock(
            content="Aggregated summary:\n1. Technical analysis shows uptrend\n2. Macro conditions are favorable\n3. Overall sentiment is positive"
        ))
        return provider
    
    @pytest.mark.asyncio
    async def test_aggregate_results(self, mock_provider):
        aggregator = ResultAggregator(mock_provider)
        
        request = CollaborationRequest(
            goal="Should I buy BTC?",
            context={},
        )
        
        task_results = {
            "t1": TaskResult(
                task_id="t1",
                status=TaskStatus.COMPLETED,
                output="Technical analysis: RSI is 65, MACD shows bullish crossover",
            ),
            "t2": TaskResult(
                task_id="t2",
                status=TaskStatus.COMPLETED,
                output="Macro analysis: Fed likely to cut rates, positive for risk assets",
            ),
        }
        
        result = await aggregator.aggregate(request, task_results)
        
        assert "uptrend" in result.lower() or "positive" in result.lower()
    
    @pytest.mark.asyncio
    async def test_aggregate_no_results(self, mock_provider):
        aggregator = ResultAggregator(mock_provider)
        request = CollaborationRequest(goal="Test")
        
        result = await aggregator.aggregate(request, {})
        
        assert "no task results" in result.lower()


class TestDecisionEngine:
    """Test decision making."""
    
    @pytest.fixture
    def mock_provider(self):
        provider = AsyncMock()
        provider.chat = AsyncMock(return_value=MagicMock(
            content=json.dumps({
                "decision": "buy",
                "confidence": 0.75,
                "reasoning": "Technical and macro factors align positively",
                "factors": [
                    {"name": "Technical Signal", "value": "Bullish", "weight": 1.0, "confidence": 0.8},
                    {"name": "Macro Environment", "value": "Favorable", "weight": 0.8, "confidence": 0.7},
                ],
                "execution_plan": {
                    "action": "market_buy",
                    "parameters": {"amount": "10% of portfolio"},
                    "risk_level": "medium",
                }
            })
        ))
        return provider
    
    @pytest.mark.asyncio
    async def test_make_decision(self, mock_provider):
        engine = DecisionEngine(mock_provider)
        
        request = CollaborationRequest(
            goal="Should I buy BTC at $67000?",
            context={"current_price": "67000"},
        )
        
        aggregated = "Technical analysis shows uptrend. Macro conditions favorable."
        
        result = await engine.decide(request, aggregated)
        
        assert result.decision == "buy"
        assert result.confidence == 0.75
        assert len(result.factors) == 2
        assert result.execution_plan is not None
    
    @pytest.mark.asyncio
    async def test_decision_fallback(self):
        provider = AsyncMock()
        provider.chat = AsyncMock(side_effect=Exception("API error"))
        
        engine = DecisionEngine(provider)
        request = CollaborationRequest(goal="Test")
        
        result = await engine.decide(request, "Some analysis")
        
        assert result.decision == "inconclusive"
        assert result.confidence == 0.0


class TestFeedbackLoop:
    """Test feedback loop."""
    
    def test_record_decision(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            feedback = FeedbackLoop(
                workspace=Path(tmpdir),
                reminder_days=7,
            )
            
            request = CollaborationRequest(
                request_id="req_123",
                goal="Buy BTC?",
                context={},
            )
            
            result = CollaborationResult(
                goal="Buy BTC?",
                decision="buy",
                confidence=0.75,
                reasoning="Good entry point",
            )
            
            record = asyncio.run(feedback.record(request, result))
            
            assert record.feedback_id is not None
            assert record.decision == "buy"
            assert record.reminder_at is not None
    
    def test_verify_decision(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            feedback = FeedbackLoop(workspace=Path(tmpdir))
            
            request = CollaborationRequest(request_id="req_123", goal="Test")
            result = CollaborationResult(goal="Test", decision="buy", confidence=0.75)
            
            record = asyncio.run(feedback.record(request, result))
            
            verified = asyncio.run(feedback.verify(
                feedback_id=record.feedback_id,
                actual_outcome="Price increased 5%",
                correct=True,
                notes="Good call",
            ))
            
            assert verified is not None
            assert verified.outcome_correct is True
    
    def test_accuracy_stats(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            feedback = FeedbackLoop(workspace=Path(tmpdir))
            
            for i in range(3):
                request = CollaborationRequest(request_id=f"req_{i}", goal="Test")
                result = CollaborationResult(goal="Test", decision="buy", confidence=0.7)
                record = asyncio.run(feedback.record(request, result))
                asyncio.run(feedback.verify(
                    feedback_id=record.feedback_id,
                    actual_outcome="Outcome",
                    correct=(i < 2),
                ))
            
            stats = feedback.get_accuracy_stats()
            
            assert stats["verified"] == 3
            assert stats["correct"] == 2
            assert stats["accuracy"] == 2/3


class TestTraceManager:
    """Test trace management."""
    
    def test_create_and_save_trace(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = TraceManager(workspace=Path(tmpdir))
            
            request = CollaborationRequest(
                goal="Test goal",
                context={"key": "value"},
            )
            
            trace = manager.create_trace(request)
            
            assert trace.trace_id is not None
            assert trace.goal == "Test goal"
            
            trace.tasks = [Task(name="task1", description="desc")]
            manager.save_trace(trace)
            
            loaded = manager.load_trace(trace.trace_id)
            
            assert loaded is not None
            assert loaded.goal == "Test goal"
            assert len(loaded.tasks) == 1
    
    def test_list_traces(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = TraceManager(workspace=Path(tmpdir))
            
            for i in range(3):
                request = CollaborationRequest(goal=f"Goal {i}")
                trace = manager.create_trace(request)
                trace.tasks = []
                manager.save_trace(trace)
            
            traces = manager.list_traces(limit=10)
            
            assert len(traces) == 3
    
    def test_search_traces(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = TraceManager(workspace=Path(tmpdir))
            
            for goal in ["Buy BTC", "Sell ETH", "Analyze BTC trend"]:
                request = CollaborationRequest(goal=goal)
                trace = manager.create_trace(request)
                trace.tasks = []
                manager.save_trace(trace)
            
            results = manager.search_traces("BTC")
            
            assert len(results) == 2


class TestProgressTracker:
    """Test progress tracking."""
    
    @pytest.mark.asyncio
    async def test_progress_events(self):
        events = []
        
        def listener(event):
            events.append(event)
        
        tracker = ProgressTracker("trace_123", listeners=[listener])
        
        await tracker.on_task_started("task_1", "agent_1")
        await tracker.on_task_progress("task_1", "50% done", 0.5)
        await tracker.on_task_completed("task_1", "Result")
        
        assert len(events) == 3
        assert events[0].type == "task_started"
        assert events[1].progress == 0.5
        assert events[2].type == "task_completed"


class TestIntegration:
    """Integration tests for the full collaboration flow."""
    
    @pytest.fixture
    def mock_provider(self):
        provider = AsyncMock()
        
        def chat_side_effect(messages, **kwargs):
            system_msg = str(messages[0]["content"]).lower() if messages else ""
            
            response = MagicMock()
            
            if "decomposition" in system_msg:
                response.content = json.dumps({
                    "tasks": [
                        {"name": "analysis", "description": "Analyze", "required_capabilities": [], "dependencies": [], "priority": 5}
                    ]
                })
            elif "result aggregation" in system_msg:
                response.content = "Aggregated analysis shows positive outlook"
            elif "decision-making" in system_msg:
                response.content = json.dumps({
                    "decision": "proceed",
                    "confidence": 0.8,
                    "reasoning": "Analysis supports this decision",
                    "factors": []
                })
            else:
                response.content = "Task completed successfully"
            
            return response
        
        provider.chat = AsyncMock(side_effect=chat_side_effect)
        return provider
    
    @pytest.fixture
    def mock_config(self):
        config = MagicMock()
        config.agents = MagicMock()
        config.agents.agent_list = []
        config.agents.defaults = MagicMock(model="gpt-4", temperature=0.7)
        config.workspace_path = Path(tempfile.gettempdir())
        config.get_agent_model_and_fallbacks = MagicMock(return_value=("gpt-4", []))
        config._resolve_default_entry = MagicMock(return_value=MagicMock(
            name="default",
            model="gpt-4",
            temperature=0.7,
        ))
        return config
    
    @pytest.mark.asyncio
    async def test_full_collaboration_flow(self, mock_provider, mock_config):
        from joyhousebot.agent.collaboration.orchestrator import Orchestrator
        
        with tempfile.TemporaryDirectory() as tmpdir:
            agents = {
                "analyst": AgentProfile(
                    agent_id="analyst",
                    name="Analyst",
                    capabilities=[AgentCapability(id="analysis", name="Analysis")],
                ),
            }
            
            orchestrator = Orchestrator(
                config=mock_config,
                provider=mock_provider,
                agents=agents,
                workspace=Path(tmpdir),
                options=OrchestratorOptions(feedback_enabled=False),
            )
            
            request = CollaborationRequest(
                goal="Should I invest in BTC?",
                context={"price": "67000"},
                feedback_enabled=False,
            )
            
            result, trace = await orchestrator.collaborate(request)
            
            assert result.decision == "proceed"
            assert result.confidence == 0.8
            assert trace.trace_id is not None
            assert len(trace.tasks) >= 1


class TestDeliberation:
    """Test multi-round deliberation functionality."""
    
    @pytest.fixture
    def mock_provider(self):
        provider = AsyncMock()
        
        def chat_side_effect(messages, **kwargs):
            system_msg = str(messages[0]["content"]).lower() if messages else ""
            user_msg = str(messages[-1]["content"]).lower() if len(messages) > 1 else ""
            
            response = MagicMock()
            
            if "strategic planning expert" in system_msg:
                response.content = json.dumps({
                    "topics": [
                        {
                            "title": "Feasibility Analysis",
                            "description": "Analyze if opening a bubble tea shop is viable",
                            "focus_questions": ["Is there market demand?", "What are the costs?"],
                            "dependencies": []
                        },
                        {
                            "title": "Location Strategy",
                            "description": "Determine the best location",
                            "focus_questions": ["Which district has highest foot traffic?", "What are rental costs?"],
                            "dependencies": ["topic_1"]
                        },
                        {
                            "title": "Operational Planning",
                            "description": "Plan the execution",
                            "focus_questions": ["What licenses are needed?", "How to hire staff?"],
                            "dependencies": ["topic_2"]
                        }
                    ]
                })
            elif "synthesis expert" in system_msg:
                response.content = json.dumps({
                    "conclusion": "This round concludes that the plan is viable",
                    "key_findings": ["Finding 1", "Finding 2"],
                    "open_questions": []
                })
            elif "strategic advisor" in system_msg:
                response.content = json.dumps({
                    "final_decision": "proceed with opening",
                    "overall_confidence": 0.75,
                    "executive_summary": "After thorough analysis, we recommend proceeding.",
                    "detailed_report": "# Full Report\n\nDetailed analysis here...",
                    "action_items": [
                        {"priority": "high", "action": "Secure location", "timeline": "1 month"}
                    ],
                    "risks": [
                        {"risk": "Competition", "mitigation": "Differentiate product", "severity": "medium"}
                    ],
                    "recommendations": ["Start with small location", "Focus on quality"]
                })
            elif "collaborative analyst" in system_msg:
                response.content = "Detailed analysis of the topic with key insights and recommendations."
            else:
                response.content = "Generic response"
            
            return response
        
        provider.chat = AsyncMock(side_effect=chat_side_effect)
        return provider
    
    @pytest.mark.asyncio
    async def test_generate_topics(self, mock_provider):
        from joyhousebot.agent.collaboration.deliberation import DeliberationEngine
        from joyhousebot.agent.collaboration.types import DeliberationMode
        
        engine = DeliberationEngine(mock_provider)
        
        request = DeliberationRequest(
            goal="Open a bubble tea shop in Nanjing",
            context={"budget": "200000", "city": "Nanjing"},
            max_rounds=3,
        )
        
        topics = await engine.generate_topics(request)
        
        assert len(topics) == 3
        assert topics[0].title == "Feasibility Analysis"
        assert len(topics[0].focus_questions) == 2
    
    @pytest.mark.asyncio
    async def test_execute_round(self, mock_provider):
        from joyhousebot.agent.collaboration.deliberation import DeliberationEngine
        from joyhousebot.agent.collaboration.types import RoundTopic
        
        engine = DeliberationEngine(mock_provider)
        
        request = DeliberationRequest(
            goal="Open a bubble tea shop",
            context={},
        )
        
        topic = RoundTopic(
            id="topic_1",
            round_number=1,
            title="Test Topic",
            description="Test description",
            focus_questions=["Question 1?"],
        )
        
        result = await engine.execute_round(topic, request, [])
        
        assert result.round_number == 1
        assert result.conclusion != ""
        assert result.duration_ms is not None
    
    @pytest.mark.asyncio
    async def test_full_deliberation(self, mock_provider):
        from joyhousebot.agent.collaboration.deliberation import DeliberationEngine
        
        engine = DeliberationEngine(mock_provider)
        
        request = DeliberationRequest(
            goal="Open a bubble tea shop in Nanjing Gulou District",
            context={"budget": "200000"},
            max_rounds=3,
            feedback_enabled=False,
        )
        
        result, trace = await engine.deliberate(request)
        
        assert result.final_decision == "proceed with opening"
        assert result.overall_confidence == 0.75
        assert len(result.round_results) == 3
        assert trace.trace_id is not None
    
    @pytest.mark.asyncio
    async def test_deliberation_with_initial_topics(self, mock_provider):
        from joyhousebot.agent.collaboration.deliberation import DeliberationEngine
        from joyhousebot.agent.collaboration.types import RoundTopic
        
        engine = DeliberationEngine(mock_provider)
        
        initial_topics = [
            {
                "title": "Market Research",
                "description": "Analyze the market",
                "focus_questions": ["What is the demand?"],
            },
            {
                "title": "Final Decision",
                "description": "Make the call",
                "focus_questions": ["Go or no go?"],
            },
        ]
        
        request = DeliberationRequest(
            goal="Test goal",
            auto_generate_topics=False,
            initial_topics=initial_topics,
            feedback_enabled=False,
        )
        
        result, trace = await engine.deliberate(request)
        
        assert len(trace.topics) == 2
        assert trace.topics[0].title == "Market Research"
    
    @pytest.mark.asyncio
    async def test_orchestrator_deliberate(self, mock_provider):
        from joyhousebot.agent.collaboration.orchestrator import Orchestrator
        
        mock_config = MagicMock()
        mock_config.agents = MagicMock()
        mock_config.agents.agent_list = []
        mock_config.workspace_path = Path(tempfile.gettempdir())
        mock_config.get_agent_model_and_fallbacks = MagicMock(return_value=("gpt-4", []))
        
        with tempfile.TemporaryDirectory() as tmpdir:
            orchestrator = Orchestrator(
                config=mock_config,
                provider=mock_provider,
                agents={},
                workspace=Path(tmpdir),
                options=OrchestratorOptions(feedback_enabled=False),
            )
            
            request = DeliberationRequest(
                goal="Open a bubble tea shop",
                max_rounds=2,
                feedback_enabled=False,
            )
            
            result, trace = await orchestrator.deliberate(request)
            
            assert result.final_decision != ""
            assert trace.trace_id is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
