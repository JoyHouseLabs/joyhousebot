"""Tool registry for dynamic tool management.

在整体架构中：AgentLoop 通过 ToolRegistry 注册与执行内置及 MCP 工具，可选 allowlist 控制启用集合。
"""

from typing import Any

from joyhousebot.agent.tools.base import Tool


class ToolRegistry:
    """
    Registry for agent tools.
    
    Allows dynamic registration and execution of tools.
    """
    
    def __init__(self, optional_allowlist: list[str] | None = None):
        self._tools: dict[str, Tool] = {}
        self._optional_tools: set[str] = set()
        normalized = [str(x).strip() for x in (optional_allowlist or []) if str(x).strip()]
        self._optional_allowlist: set[str] | None = set(normalized) if normalized else None
    
    def register(self, tool: Tool, *, optional: bool = False) -> None:
        """Register a tool."""
        self._tools[tool.name] = tool
        if optional:
            self._optional_tools.add(tool.name)
        else:
            self._optional_tools.discard(tool.name)
    
    def unregister(self, name: str) -> None:
        """Unregister a tool by name."""
        self._tools.pop(name, None)
    
    def get(self, name: str) -> Tool | None:
        """Get a tool by name."""
        if not self._is_enabled(name):
            return None
        return self._tools.get(name)
    
    def has(self, name: str) -> bool:
        """Check if a tool is registered."""
        return name in self._tools and self._is_enabled(name)
    
    def get_definitions(self) -> list[dict[str, Any]]:
        """Get all tool definitions in OpenAI format."""
        return [tool.to_schema() for name, tool in self._tools.items() if self._is_enabled(name)]
    
    async def execute(self, name: str, params: dict[str, Any], **extra_kwargs: Any) -> str:
        """
        Execute a tool by name with given parameters.
        
        Args:
            name: Tool name.
            params: Tool parameters.
            **extra_kwargs: Optional extra arguments passed to tool.execute (e.g. execution_stream_callback).
        
        Returns:
            Tool execution result as string.
        
        Raises:
            KeyError: If tool not found.
        """
        if not isinstance(params, dict):
            params = {}
        tool = self._tools.get(name)
        if not tool:
            return f"Error: Tool '{name}' not found"
        if not self._is_enabled(name):
            return f"Error: Tool '{name}' is disabled"

        try:
            errors = tool.validate_params(params)
            if errors:
                return f"Error: Invalid parameters for tool '{name}': " + "; ".join(errors)
            return await tool.execute(**params, **extra_kwargs)
        except Exception as e:
            return f"Error executing {name}: {str(e)}"
    
    @property
    def tool_names(self) -> list[str]:
        """Get list of registered tool names."""
        return [name for name in self._tools if self._is_enabled(name)]
    
    def __len__(self) -> int:
        return len(self._tools)
    
    def __contains__(self, name: str) -> bool:
        return name in self._tools and self._is_enabled(name)

    def _is_enabled(self, name: str) -> bool:
        if name not in self._tools:
            return False
        if name not in self._optional_tools:
            return True
        if self._optional_allowlist is None:
            return True
        return name in self._optional_allowlist
