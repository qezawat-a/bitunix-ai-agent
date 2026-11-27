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


@pytest.mark.asyncio
async def test_rest_falls_back_to_direct_when_proxy_answers_404(monkeypatch):
    import httpx
    from bitunix_agent.exchange.rest import BitunixREST
    calls = []

    def handler(req):
        calls.append(req)
        if len(calls) == 1:
            return httpx.Response(404, text="Not Found")  # the proxy (e.g. J-Rock's own port) answered
        return httpx.Response(200, json={"code": 0, "data": [{"symbol": "BTCUSDT", "lastPrice": "1"}]})

    c = BitunixREST(transport=httpx.MockTransport(handler))
    c._proxied = True  # pretend a proxy (BITUNIX_PROXY/HTTPS_PROXY) is configured
    data = await c.call("get_tickers", {"symbols": "BTCUSDT"})
    assert c._direct and len(calls) == 2 and data
    await c.close()


def test_bitunix_proxy_on_own_port_is_ignored(monkeypatch):
    from bitunix_agent.config import _bitunix_proxy
    monkeypatch.setenv("BITUNIX_PROXY", "http://127.0.0.1:8080")
    monkeypatch.delenv("API_PORT", raising=False)
    assert _bitunix_proxy() is None
    monkeypatch.setenv("BITUNIX_PROXY", "127.0.0.1:10809")
    assert _bitunix_proxy() == "http://127.0.0.1:10809"


@pytest.mark.asyncio
async def test_rest_region_block_message():
    import httpx
    from bitunix_agent.exchange.rest import BitunixREST
    from bitunix_agent.exchange.errors import BitunixAPIError
    c = BitunixREST(transport=httpx.MockTransport(lambda r: httpx.Response(403, text="<html>blocked</html>")))
    c._proxied = False
    with pytest.raises(BitunixAPIError) as ei:
        await c.call("get_tickers", {"symbols": "BTCUSDT"})
    assert "region" in str(ei.value)
    await c.close()


def test_quiet_tasks():
    from bitunix_agent.engine.monitor import _is_quiet
    assert _is_quiet("SILENT", None)
    assert _is_quiet("REST down. Price 0.1335 +57%", "REST down. Price 0.1401 +61%")
    assert not _is_quiet("LONG signal on INITUSDT, order placed", "REST down. Price 0.1335")
    assert not _is_quiet("REST down", None)
