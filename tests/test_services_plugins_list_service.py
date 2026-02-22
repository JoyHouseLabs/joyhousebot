from joyhousebot.services.plugins.list_service import (
    filter_plugin_rows,
    is_native_plugin_row,
    native_plugin_table_row,
    plugin_info_fields,
    plugin_table_row,
    resolve_plugin_info_row,
)


def test_filter_and_table_rows():
    rows = [
        {"id": "a.demo", "name": "Alpha", "status": "loaded", "origin": "native", "source": "/x"},
        {"id": "b.demo", "name": "Beta", "status": "error", "origin": "bridge", "source": "/y"},
    ]
    filtered = filter_plugin_rows(rows, "alpha")
    assert len(filtered) == 1
    assert plugin_table_row(filtered[0])[0] == "a.demo"
    assert is_native_plugin_row(filtered[0]) is True
    native_row = native_plugin_table_row(
        {
            "id": "a.demo",
            "name": "Alpha",
            "status": "loaded",
            "runtime": "",
            "capabilities": ["rpc"],
            "gateway_methods": ["plugins.demo"],
            "hook_names": ["h1"],
            "source": "/x",
        }
    )
    assert native_row[3] == "python-native"


def test_info_helpers():
    rows = [{"id": "a.demo", "name": "Alpha", "status": "loaded"}]
    row = resolve_plugin_info_row(rows, "a.demo")
    assert row is not None
    fields = plugin_info_fields(row)
    field_map = {k: v for k, v in fields}
    assert field_map["id"] == "a.demo"
    assert field_map["status"] == "loaded"

