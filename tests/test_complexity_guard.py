import ast

from scripts.check_complexity import _max_nesting, check, collect_findings


def test_committed_complexity_baseline_rejects_new_hard_limit_debt() -> None:
    findings, summary = collect_findings()

    assert summary["python_files_over_target"] > 0
    assert check(findings) == []


def test_elif_chain_does_not_count_as_nested_blocks() -> None:
    tree = ast.parse(
        """
def choose(value):
    if value == 1:
        return 1
    elif value == 2:
        return 2
    elif value == 3:
        return 3
    return 0
"""
    )
    function = tree.body[0]

    assert isinstance(function, ast.FunctionDef)
    assert _max_nesting(function.body) == 1
