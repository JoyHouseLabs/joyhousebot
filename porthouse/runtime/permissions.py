"""Central tool permission policy for native runtime runs."""

from __future__ import annotations

from dataclasses import dataclass

from porthouse.runtime.context import ToolExecutionContext


@dataclass(frozen=True, slots=True)
class PermissionDecision:
    allowed: bool
    reason: str = ""


class PermissionEngine:
    """Evaluate deterministic runtime policy before tool-specific safeguards."""

    PLAN_SAFE_TOOLS = frozenset(
        {
            "read_file",
            "list_dir",
            "retrieve",
            "memory_get",
            "web_search",
            "web_fetch",
        }
    )
    HIGH_RISK_TOOLS = frozenset(
        {
            "exec",
            "write_file",
            "edit_file",
            "x402_pay",
            "x402_transfer",
        }
    )

    def evaluate(self, tool_name: str, context: ToolExecutionContext) -> PermissionDecision:
        if tool_name in context.disallowed_tools:
            return PermissionDecision(False, f"Tool '{tool_name}' is disallowed for this run")
        allowlist_enforced = bool(
            context.metadata.get("capability_allowlist_enforced")
        )
        if (context.allowed_tools or allowlist_enforced) and tool_name not in context.allowed_tools:
            return PermissionDecision(False, f"Tool '{tool_name}' is not in the run allowlist")

        mode = (context.permission_mode or "default").strip().lower()
        if mode == "coordinator":
            return PermissionDecision(False, "Tools are unavailable during request coordination")
        if mode == "plan" and tool_name not in self.PLAN_SAFE_TOOLS:
            return PermissionDecision(False, f"Tool '{tool_name}' is unavailable in plan mode")
        if mode in {"dontask", "dont_ask"} and tool_name in self.HIGH_RISK_TOOLS:
            return PermissionDecision(
                False, f"Tool '{tool_name}' is disabled in non-interactive mode"
            )
        return PermissionDecision(True)


permission_engine = PermissionEngine()
