"""Safe, deterministic conditions for clarification DAG edges."""

from __future__ import annotations

import ast
from typing import Any

_ALLOWED_NODES = (
    ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.UnaryOp, ast.Not,
    ast.Compare, ast.Eq, ast.NotEq, ast.In, ast.NotIn, ast.Name, ast.Load,
    ast.Constant, ast.List, ast.Tuple, ast.Call,
)


def _parse(condition: str) -> ast.Expression:
    text = str(condition or "true").strip()
    if text.casefold() == "true":
        text = "True"
    elif text.casefold() == "false":
        text = "False"
    try:
        expression = ast.parse(text, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"invalid clarification edge condition: {condition!r}") from exc
    for node in ast.walk(expression):
        if not isinstance(node, _ALLOWED_NODES):
            raise ValueError("clarification edge condition contains unsupported syntax")
        if isinstance(node, ast.Call) and (
            not isinstance(node.func, ast.Name)
            or node.func.id not in {"present", "missing"}
            or len(node.args) != 1
            or node.keywords
        ):
            raise ValueError("clarification edge conditions only allow present(field) or missing(field)")
        if isinstance(node, ast.Name) and node.id.startswith("__"):
            raise ValueError("clarification edge condition contains an invalid field name")

    class _HelperArgumentNames(ast.NodeTransformer):
        def visit_Call(self, node: ast.Call) -> ast.AST:
            self.generic_visit(node)
            if (
                isinstance(node.func, ast.Name)
                and node.func.id in {"present", "missing"}
                and isinstance(node.args[0], ast.Name)
            ):
                node.args[0] = ast.Constant(node.args[0].id)
            return node

    expression = _HelperArgumentNames().visit(expression)
    ast.fix_missing_locations(expression)
    return expression


def validate_condition(condition: str) -> None:
    _parse(condition)


def condition_matches(condition: str, values: dict[str, Any]) -> bool:
    expression = _parse(condition)

    def present(name: Any) -> bool:
        value = values.get(str(name))
        return value is not None and value != "" and value != []

    def missing(name: Any) -> bool:
        return not present(name)

    scope = dict(values)
    scope.update({"present": present, "missing": missing, "True": True, "False": False})
    try:
        return bool(eval(compile(expression, "<clarification-condition>", "eval"), {"__builtins__": {}}, scope))
    except Exception as exc:  # pragma: no cover - defensive after AST validation
        raise ValueError(f"could not evaluate clarification edge condition: {condition!r}") from exc
