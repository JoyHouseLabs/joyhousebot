"""Ordinary Worker logs must never preview private prompt or response bodies."""

from __future__ import annotations

import inspect

from porthouse.agent.message_processor import MessageProcessorMixin


def test_message_processor_logs_only_content_shape() -> None:
    source = inspect.getsource(MessageProcessorMixin._process_message_inner)

    assert "msg.content[:" not in source
    assert "final_content[:" not in source
    assert "content_chars={}" in source
