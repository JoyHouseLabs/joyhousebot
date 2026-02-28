"""Task dispatcher - assigns tasks to the most suitable agents."""

from typing import TYPE_CHECKING

from loguru import logger

from joyhousebot.agent.collaboration.types import AgentProfile, Task

if TYPE_CHECKING:
    pass


class TaskDispatcher:
    """Dispatches tasks to agents based on capability matching."""
    
    def __init__(
        self,
        agents: dict[str, AgentProfile],
        strategy: str = "best_match",
    ):
        """
        Initialize the dispatcher.
        
        Args:
            agents: Dictionary mapping agent_id to AgentProfile
            strategy: Dispatch strategy - "best_match", "round_robin", "least_loaded"
        """
        self.agents = agents
        self.strategy = strategy
        self._agent_load: dict[str, int] = {agent_id: 0 for agent_id in agents}
    
    def dispatch(self, tasks: list[Task]) -> dict[str, str]:
        """
        Assign tasks to agents.
        
        Args:
            tasks: List of tasks to assign
            
        Returns:
            Dictionary mapping task_id to agent_id
        """
        assignments: dict[str, str] = {}
        
        for task in tasks:
            agent_id = self._find_best_agent(task)
            if agent_id:
                assignments[task.id] = agent_id
                self._agent_load[agent_id] += 1
                logger.debug(f"Task '{task.name}' assigned to agent '{agent_id}'")
            else:
                logger.warning(f"No suitable agent found for task '{task.name}'")
                if self.agents:
                    fallback = next(iter(self.agents.keys()))
                    assignments[task.id] = fallback
                    logger.info(f"Using fallback agent '{fallback}' for task '{task.name}'")
        
        logger.info(f"Dispatched {len(assignments)} tasks to {len(set(assignments.values()))} agents")
        return assignments
    
    def _find_best_agent(self, task: Task) -> str | None:
        """Find the best agent for a task based on strategy."""
        if not self.agents:
            return None
        
        if self.strategy == "best_match":
            return self._find_by_capability(task)
        elif self.strategy == "round_robin":
            return self._find_round_robin(task)
        elif self.strategy == "least_loaded":
            return self._find_least_loaded(task)
        else:
            return self._find_by_capability(task)
    
    def _find_by_capability(self, task: Task) -> str | None:
        """Find agent with best capability match."""
        best_agent: str | None = None
        best_score = -1.0
        
        required_caps = task.required_capabilities
        
        for agent_id, profile in self.agents.items():
            score = profile.get_capability_score(required_caps)
            if score > best_score:
                best_score = score
                best_agent = agent_id
        
        return best_agent
    
    def _find_round_robin(self, task: Task) -> str | None:
        """Find next agent in round-robin order that can handle the task."""
        required_caps = task.required_capabilities
        
        for agent_id in self.agents:
            profile = self.agents[agent_id]
            if profile.get_capability_score(required_caps) > 0:
                return agent_id
        
        return next(iter(self.agents.keys())) if self.agents else None
    
    def _find_least_loaded(self, task: Task) -> str | None:
        """Find agent with lowest load that can handle the task."""
        required_caps = task.required_capabilities
        candidates = []
        
        for agent_id, profile in self.agents.items():
            score = profile.get_capability_score(required_caps)
            if score > 0:
                candidates.append((agent_id, self._agent_load.get(agent_id, 0), score))
        
        if not candidates:
            return next(iter(self.agents.keys())) if self.agents else None
        
        candidates.sort(key=lambda x: (x[1], -x[2]))
        return candidates[0][0]
    
    def get_agent_assignments(self, assignments: dict[str, str]) -> dict[str, list[str]]:
        """Get tasks grouped by agent."""
        result: dict[str, list[str]] = {agent_id: [] for agent_id in self.agents}
        for task_id, agent_id in assignments.items():
            if agent_id in result:
                result[agent_id].append(task_id)
        return result
    
    def reset_load(self):
        """Reset agent load counters."""
        self._agent_load = {agent_id: 0 for agent_id in self.agents}
    
    def get_load_summary(self) -> dict[str, int]:
        """Get current load for each agent."""
        return dict(self._agent_load)
