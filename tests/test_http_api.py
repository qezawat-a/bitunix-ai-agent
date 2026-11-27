import httpx
from starlette.testclient import TestClient

from bitunix_agent.agent.core import Agent
from bitunix_agent.config import Settings
from bitunix_agent.interfaces.http_api import build_app
from tests.fake_exchange import FakeExchange


def test_http_api_end_to_end():
    fx = FakeExchange("HEDGE")
    a = Agent(Settings(db_path=":memory:", api_token="tok", dry_run=False, confirm_trades="always",
                       bitunix_api_key="K", bitunix_secret_key="S", llm_api_key=""))
    a.rest._http = httpx.AsyncClient(transport=httpx.MockTransport(fx))
    H = {"Authorization": "Bearer tok"}
    with TestClient(build_app(a)) as c:
        assert c.get("/v1/overview").status_code == 401
        assert c.get("/v1/market/BTCUSDT", headers=H).json()["last"] == "60000"
        size = c.post("/v1/tools/calc_position_size", json={"symbol": "BTC", "margin_usdt": 60, "leverage": 10}, headers=H).json()
        assert size["qty"] == "0.01"
        pend = c.post("/v1/tools/open_position", json={"symbol": "BTC", "side": "LONG", "qty": 0.01}, headers=H).json()
        assert pend["status"] == "PENDING_CONFIRMATION"
        done = c.post(f"/v1/confirm/{pend['confirmation_id']}", headers=H).json()
        assert done["ok"] is True
        assert len(c.get("/v1/tools", headers=H).json()) >= 75
        assert c.get("/v1/journal", headers=H).json()[0]["action"] == "open_position"
        chat = c.post("/v1/chat", json={"chat_id": "x"}, headers=H).json()
        assert chat["error"] == "missing_field"
        assert c.get("/health").json()["ok"] is True
