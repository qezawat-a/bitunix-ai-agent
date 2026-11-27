import pytest
from bitunix_agent.agent.core import _is_content_filter


def test_content_filter_detected():
    e = RuntimeError('LLM HTTP 400: {"error":{"type":"content_filter_error","code":"content_filter"}}')
    assert _is_content_filter(e)
    assert _is_content_filter(RuntimeError("LLM HTTP 400: data_inspection_failed"))
    assert not _is_content_filter(RuntimeError("LLM HTTP 401: invalid api key"))


def test_slack_helpers():
    pytest.importorskip("slack_bolt")
    from bitunix_agent.interfaces.slack_bot import _asks_for_id, to_slack_markdown
    assert _asks_for_id("I need my member id")
    assert _asks_for_id("id")
    assert _asks_for_id("whoami")
    assert not _asks_for_id("analyze BTCUSDT")
    assert to_slack_markdown("**Bold** text\n## Head") == "*Bold* text\n*Head*"
