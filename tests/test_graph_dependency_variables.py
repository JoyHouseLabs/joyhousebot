from joyhousebot.orchestration.task_graph import render_value
from joyhousebot.runtime.graph_branch_execution import dependency_result_variables


def test_dependency_result_variables_expose_bounded_nested_capability_output() -> None:
    result = {
        "read-work": {
            "content": "read completed",
            "structured_output": {"version": 3},
            "capability_result": {
                "status": "succeeded",
                "data": {
                    "output": {
                        "content": "# Frozen body",
                        "title": "Frozen title",
                    }
                },
            },
        }
    }

    variables = dependency_result_variables(result)

    assert variables["tasks.read-work.content"] == "read completed"
    assert variables["tasks.read-work.structured_output.version"] == 3
    assert (
        variables["tasks.read-work.capability_result.data.output.content"]
        == "# Frozen body"
    )
    rendered = render_value(
        {
            "body": "${tasks.read-work.capability_result.data.output.content}",
            "title": "${tasks.read-work.capability_result.data.output.title}",
        },
        variables,
    )
    assert rendered == {"body": "# Frozen body", "title": "Frozen title"}


def test_dependency_result_variables_ignore_unsafe_path_segments() -> None:
    variables = dependency_result_variables(
        {
            "source": {
                "capability_result": {
                    "data": {
                        "unsafe.key": "not-addressable",
                        "safe_key": "addressable",
                    }
                }
            }
        }
    )

    assert "tasks.source.capability_result.data.unsafe.key" not in variables
    assert variables["tasks.source.capability_result.data.safe_key"] == "addressable"
