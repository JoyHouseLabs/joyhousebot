from joyhousebot.api.http.error_helpers import unknown_error_detail


def test_unknown_error_detail_with_exception():
    assert unknown_error_detail(ValueError("x")) == "x"


def test_unknown_error_detail_with_none():
    assert unknown_error_detail(None) == "Unknown error"

