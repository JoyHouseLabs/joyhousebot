"""Hook dispatcher - manages hook registration and emission."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Union

from loguru import logger

from joyhousebot.plugins.hooks.types import (
    HookHandler,
    PluginHookRegistration,
)


class HookDispatcher:
    """Manages plugin hook registration and emission.
    
    Hooks are called in priority order (lower number = earlier execution).
    Multiple hooks can be registered for the same event.
    """
    
    def __init__(self) -> None:
        self._registrations: dict[str, list[PluginHookRegistration]] = {}
    
    def register(
        self,
        hook_name: str,
        handler: HookHandler,
        priority: int = 0,
        plugin_id: str = "",
        source: str = "",
    ) -> None:
        """Register a hook handler.
        
        Args:
            hook_name: Name of the hook event.
            handler: The handler function.
            priority: Execution priority (lower = earlier). Default 0.
            plugin_id: ID of the plugin registering this hook.
            source: Source file path for debugging.
        """
        if hook_name not in self._registrations:
            self._registrations[hook_name] = []
        
        registration = PluginHookRegistration(
            plugin_id=plugin_id,
            hook_name=hook_name,
            handler=handler,
            priority=priority,
            source=source,
        )
        self._registrations[hook_name].append(registration)
        self._registrations[hook_name].sort(key=lambda r: r.priority)
        
        logger.debug(
            f"Hook registered: {hook_name} [{plugin_id}] priority={priority}"
        )
    
    def unregister_all(self, plugin_id: str) -> int:
        """Unregister all hooks for a plugin.
        
        Returns:
            Number of hooks removed.
        """
        count = 0
        for hook_name in list(self._registrations.keys()):
            before = len(self._registrations[hook_name])
            self._registrations[hook_name] = [
                r for r in self._registrations[hook_name]
                if r.plugin_id != plugin_id
            ]
            count += before - len(self._registrations[hook_name])
        return count
    
    def get_handlers(self, hook_name: str) -> list[PluginHookRegistration]:
        """Get all registered handlers for a hook, sorted by priority."""
        return list(self._registrations.get(hook_name, []))
    
    async def emit(
        self,
        hook_name: str,
        event: Any,
        context: Any,
    ) -> list[Any]:
        """Emit a hook event to all registered handlers.
        
        Args:
            hook_name: Name of the hook event.
            event: The event data object.
            context: The context object (HookContext or variant).
        
        Returns:
            List of non-None results from handlers.
        """
        results: list[Any] = []
        registrations = self._registrations.get(hook_name, [])
        
        if not registrations:
            return results
        
        for reg in registrations:
            try:
                start = time.time()
                result = reg.handler(event, context)
                
                if asyncio.iscoroutine(result):
                    result = await result
                
                if result is not None:
                    results.append(result)
                
                duration = (time.time() - start) * 1000
                logger.debug(
                    f"Hook {hook_name} [{reg.plugin_id}] "
                    f"executed in {duration:.1f}ms"
                )
            except Exception as e:
                logger.error(
                    f"Hook {hook_name} [{reg.plugin_id}] error: {e}"
                )
        
        return results
    
    async def emit_first_result(
        self,
        hook_name: str,
        event: Any,
        context: Any,
    ) -> Any | None:
        """Emit a hook event and return the first non-None result.
        
        Useful for hooks that modify behavior (like before_tool_call).
        """
        results = await self.emit(hook_name, event, context)
        return results[0] if results else None
    
    def emit_sync(
        self,
        hook_name: str,
        event: Any,
        context: Any,
    ) -> list[Any]:
        """Synchronous version of emit for non-async contexts."""
        results: list[Any] = []
        registrations = self._registrations.get(hook_name, [])
        
        for reg in registrations:
            try:
                start = time.time()
                result = reg.handler(event, context)
                
                if asyncio.iscoroutine(result):
                    logger.warning(
                        f"Hook {hook_name} [{reg.plugin_id}] "
                        f"is async but emit_sync was called"
                    )
                    continue
                
                if result is not None:
                    results.append(result)
                
                duration = (time.time() - start) * 1000
                logger.debug(
                    f"Hook {hook_name} [{reg.plugin_id}] "
                    f"executed in {duration:.1f}ms (sync)"
                )
            except Exception as e:
                logger.error(
                    f"Hook {hook_name} [{reg.plugin_id}] error: {e}"
                )
        
        return results
    
    def clear(self) -> None:
        """Clear all registered hooks."""
        self._registrations.clear()
    
    def get_all_hooks(self) -> dict[str, list[PluginHookRegistration]]:
        """Get all registered hooks (for debugging/inspection)."""
        return {
            name: list(regs)
            for name, regs in self._registrations.items()
        }


_dispatcher: HookDispatcher | None = None


def get_hook_dispatcher() -> HookDispatcher:
    """Get the global hook dispatcher singleton."""
    global _dispatcher
    if _dispatcher is None:
        _dispatcher = HookDispatcher()
    return _dispatcher


def reset_hook_dispatcher() -> None:
    """Reset the global hook dispatcher (for testing)."""
    global _dispatcher
    _dispatcher = None


async def emit_hook(hook_name: str, event: Any, context: Any) -> list[Any]:
    """Convenience function to emit a hook using the global dispatcher."""
    return await get_hook_dispatcher().emit(hook_name, event, context)


async def emit_hook_first_result(
    hook_name: str,
    event: Any,
    context: Any,
) -> Any | None:
    """Convenience function to emit a hook and get first result."""
    return await get_hook_dispatcher().emit_first_result(hook_name, event, context)
