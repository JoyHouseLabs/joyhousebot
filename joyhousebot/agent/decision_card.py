"""
Decision card schema and helpers for information decision center.

Structured output: conclusion, evidence, risks, uncertainties, next_actions.
Used by the decision-card skill and any tool that produces decision output.
"""

from typing import Any


DECISION_CARD_SECTIONS = (
    "conclusion",
    "evidence",
    "risks",
    "uncertainties",
    "next_actions",
)


def decision_card_schema() -> dict[str, Any]:
    """JSON schema for a decision card (for programmatic validation or LLM output)."""
    return {
        "type": "object",
        "properties": {
            "conclusion": {"type": "string", "description": "Main conclusion or recommendation"},
            "evidence": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Evidence snippets with optional source trace",
            },
            "risks": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Risks or caveats",
            },
            "uncertainties": {
                "type": "array",
                "items": {"type": "string"},
                "description": "What remains unclear",
            },
            "next_actions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Concrete next steps",
            },
        },
        "required": ["conclusion"],
    }


def format_decision_card(data: dict[str, Any]) -> str:
    """Format a decision card dict as markdown for display."""
    lines = []
    if data.get("conclusion"):
        lines.append("**Conclusion**\n\n" + data["conclusion"].strip() + "\n")
    if data.get("evidence"):
        lines.append("**Evidence**\n")
        for e in data["evidence"]:
            lines.append("- " + (e if isinstance(e, str) else str(e)))
        lines.append("")
    if data.get("risks"):
        lines.append("**Risks / Caveats**\n")
        for r in data["risks"]:
            lines.append("- " + (r if isinstance(r, str) else str(r)))
        lines.append("")
    if data.get("uncertainties"):
        lines.append("**Uncertainties**\n")
        for u in data["uncertainties"]:
            lines.append("- " + (u if isinstance(u, str) else str(u)))
        lines.append("")
    if data.get("next_actions"):
        lines.append("**Next actions**\n")
        for a in data["next_actions"]:
            lines.append("- " + (a if isinstance(a, str) else str(a)))
        lines.append("")
    return "\n".join(lines).strip()
