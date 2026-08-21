#!/usr/bin/env python3
"""Enforce a ratcheting complexity budget for production source code."""

from __future__ import annotations

import argparse
import ast
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = ROOT / "scripts" / "complexity_baseline.json"

FILE_TARGET = 600
FILE_LIMIT = 900
VUE_TARGET = 500
FUNCTION_TARGET = 100
FUNCTION_LIMIT = 200
COMPLEXITY_TARGET = 20
COMPLEXITY_LIMIT = 40
NESTING_TARGET = 3
NESTING_LIMIT = 4


@dataclass(frozen=True, slots=True)
class Finding:
    category: str
    key: str
    value: int
    limit: int


class _ComplexityVisitor(ast.NodeVisitor):
    """Small dependency-free McCabe-style branch counter."""

    def __init__(self) -> None:
        self.value = 1

    def _branch(self, node: ast.AST) -> None:
        self.value += 1
        self.generic_visit(node)

    def visit_If(self, node: ast.If) -> None:  # noqa: N802 - ast visitor API
        self._branch(node)

    def visit_For(self, node: ast.For) -> None:  # noqa: N802 - ast visitor API
        self._branch(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:  # noqa: N802 - ast visitor API
        self._branch(node)

    def visit_While(self, node: ast.While) -> None:  # noqa: N802 - ast visitor API
        self._branch(node)

    def visit_IfExp(self, node: ast.IfExp) -> None:  # noqa: N802 - ast visitor API
        self._branch(node)

    def visit_Assert(self, node: ast.Assert) -> None:  # noqa: N802 - ast visitor API
        self._branch(node)

    def visit_Try(self, node: ast.Try) -> None:
        self.value += len(node.handlers)
        self.value += bool(node.orelse) + bool(node.finalbody)
        self.generic_visit(node)

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        self.value += max(0, len(node.values) - 1)
        self.generic_visit(node)

    def visit_Match(self, node: ast.Match) -> None:
        self.value += max(0, len(node.cases) - 1)
        self.generic_visit(node)

    def visit_comprehension(self, node: ast.comprehension) -> None:
        self.value += 1 + len(node.ifs)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return


_NESTING_NODES = (
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.Try,
    ast.With,
    ast.AsyncWith,
    ast.Match,
)


def _max_nesting(statements: Iterable[ast.stmt], depth: int = 0) -> int:
    maximum = depth
    for statement in statements:
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        child_depth = depth + 1 if isinstance(statement, _NESTING_NODES) else depth
        maximum = max(maximum, child_depth)
        if isinstance(statement, ast.If):
            maximum = max(maximum, _max_nesting(statement.body, child_depth))
            # ``elif`` is represented by Python's AST as a single nested If in
            # ``orelse`` even though it does not add a lexical nesting level.
            # Count an elif chain at the original depth; a real else body stays
            # within the surrounding If depth.
            if len(statement.orelse) == 1 and isinstance(statement.orelse[0], ast.If):
                maximum = max(maximum, _max_nesting(statement.orelse, depth))
            else:
                maximum = max(maximum, _max_nesting(statement.orelse, child_depth))
            continue
        for _field, value in ast.iter_fields(statement):
            if isinstance(value, list):
                nested = [item for item in value if isinstance(item, ast.stmt)]
                maximum = max(maximum, _max_nesting(nested, child_depth))
            elif isinstance(value, ast.stmt):
                maximum = max(maximum, _max_nesting([value], child_depth))
    return maximum


def _function_complexity(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    visitor = _ComplexityVisitor()
    for statement in node.body:
        visitor.visit(statement)
    return visitor.value


def _python_files() -> list[Path]:
    files = list((ROOT / "joyhousebot").rglob("*.py"))
    files.extend((ROOT / "extensions").glob("*/src/**/*.py"))
    return sorted(path for path in files if "__pycache__" not in path.parts)


def _vue_files() -> list[Path]:
    return sorted((ROOT / "apps" / "console" / "src").rglob("*.vue"))


def _qualified_functions(
    body: list[ast.stmt], prefix: tuple[str, ...] = ()
) -> Iterable[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    for node in body:
        if isinstance(node, ast.ClassDef):
            yield from _qualified_functions(node.body, (*prefix, node.name))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            qualified = ".".join((*prefix, node.name))
            yield qualified, node
            yield from _qualified_functions(node.body, (*prefix, node.name))


def collect_findings() -> tuple[list[Finding], dict[str, int]]:
    findings: list[Finding] = []
    summary = {
        "python_files_over_target": 0,
        "vue_files_over_target": 0,
        "functions_over_target": 0,
        "functions_over_complexity_target": 0,
        "functions_over_nesting_target": 0,
    }
    for path in _python_files():
        source = path.read_text(encoding="utf-8")
        relative = str(path.relative_to(ROOT))
        lines = len(source.splitlines())
        if lines > FILE_TARGET:
            summary["python_files_over_target"] += 1
        if lines > FILE_LIMIT:
            findings.append(Finding("file_lines", relative, lines, FILE_LIMIT))
        tree = ast.parse(source, filename=relative)
        for qualified, node in _qualified_functions(tree.body):
            key = f"{relative}::{qualified}"
            function_lines = int(node.end_lineno or node.lineno) - node.lineno + 1
            complexity = _function_complexity(node)
            nesting = _max_nesting(node.body)
            if function_lines > FUNCTION_TARGET:
                summary["functions_over_target"] += 1
            if complexity > COMPLEXITY_TARGET:
                summary["functions_over_complexity_target"] += 1
            if nesting > NESTING_TARGET:
                summary["functions_over_nesting_target"] += 1
            if function_lines > FUNCTION_LIMIT:
                findings.append(
                    Finding("function_lines", key, function_lines, FUNCTION_LIMIT)
                )
            if complexity > COMPLEXITY_LIMIT:
                findings.append(
                    Finding("function_complexity", key, complexity, COMPLEXITY_LIMIT)
                )
            if nesting > NESTING_LIMIT:
                findings.append(Finding("function_nesting", key, nesting, NESTING_LIMIT))
    for path in _vue_files():
        relative = str(path.relative_to(ROOT))
        lines = len(path.read_text(encoding="utf-8").splitlines())
        if lines > VUE_TARGET:
            summary["vue_files_over_target"] += 1
        if lines > FILE_LIMIT:
            findings.append(Finding("file_lines", relative, lines, FILE_LIMIT))
    return sorted(findings, key=lambda item: (item.category, item.key)), summary


def _baseline_payload(findings: list[Finding]) -> dict[str, object]:
    categories: dict[str, dict[str, int]] = {}
    for finding in findings:
        categories.setdefault(finding.category, {})[finding.key] = finding.value
    return {
        "version": 1,
        "limits": {
            "file_lines": FILE_LIMIT,
            "function_lines": FUNCTION_LIMIT,
            "function_complexity": COMPLEXITY_LIMIT,
            "function_nesting": NESTING_LIMIT,
        },
        "violations": categories,
    }


def _load_baseline() -> dict[str, dict[str, int]]:
    payload = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    return {
        str(category): {str(key): int(value) for key, value in values.items()}
        for category, values in dict(payload.get("violations") or {}).items()
    }


def check(findings: list[Finding]) -> list[str]:
    baseline = _load_baseline()
    errors: list[str] = []
    for finding in findings:
        allowed = baseline.get(finding.category, {}).get(finding.key)
        if allowed is None:
            errors.append(
                f"new {finding.category}: {finding.key} = {finding.value} "
                f"(limit {finding.limit})"
            )
        elif finding.value > allowed:
            errors.append(
                f"worsened {finding.category}: {finding.key} = {finding.value} "
                f"(baseline {allowed})"
            )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="check against the committed baseline")
    parser.add_argument(
        "--print-baseline", action="store_true", help="print the current baseline JSON"
    )
    args = parser.parse_args()
    findings, summary = collect_findings()
    if args.print_baseline:
        print(json.dumps(_baseline_payload(findings), indent=2, sort_keys=True))
        return 0
    errors = check(findings)
    print("complexity summary:", json.dumps(summary, sort_keys=True))
    if errors:
        print("complexity guard failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"complexity guard: PASS ({len(findings)} grandfathered hard-limit findings)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
