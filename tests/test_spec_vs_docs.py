"""Proves the code matches the official docs: every endpoint, every request parameter, every response field,
every enum, every error code, every WebSocket channel and push field. Re-run after tools/fetch_docs.py."""
import json
import re
from pathlib import Path

import pytest

from bitunix_agent.exchange import constants as C
from bitunix_agent.exchange.errors import ERROR_CODES
from bitunix_agent.exchange.spec_generated import SPEC
from bitunix_agent.exchange.ws_spec import WS_CHANNELS

ROOT = Path(__file__).resolve().parents[1]
TXT = ROOT / "docs_snapshot" / "official_txt"
URLS = (ROOT / "docs_snapshot" / "urls.txt").read_text().split()


def tables(stem):
    t = (TXT / f"{stem}.txt").read_text()
    out = []
    for block in t.split("[TABLE]\n")[1:]:
        rows = [r.split(" | ") for r in block.split("\n[/TABLE]")[0].strip().split("\n")]
        out.append(rows)
    return t, out


def flat(params, prefix=""):
    names = {}
    for p in params:
        names[p["name"]] = p
        for c in p.get("children", []):
            names[c["name"]] = c
            names[f"{p['name']}.{c['name']}"] = c
    return names


REST_STEMS = [u.split("/futures/")[1].replace(".html", "").replace("/", "__") for u in URLS
              if any(x in u for x in ("/tp_sl/", "/account/", "/market/", "/position/", "/trade/"))]


def test_all_rest_doc_pages_are_covered():
    urls = {v["doc_url"] for v in SPEC.values()}
    rest_urls = [u for u in URLS if any(x in u for x in ("/tp_sl/", "/account/", "/market/", "/position/", "/trade/"))]
    assert len(rest_urls) == 35
    assert set(rest_urls) == urls


@pytest.mark.parametrize("stem", REST_STEMS)
def test_endpoint_matches_doc(stem):
    text, tbls = tables(stem)
    ep = next(v for v in SPEC.values() if v["doc_url"].endswith(stem.replace("__", "/") + ".html"))
    m = re.search(r"HTTP Request\s*\u200b?\s*(GET|POST)\s+(\S+?)(?:Request|$)", text)
    assert (ep["method"], ep["path"]) == (m.group(1), m.group(2))
    rl = re.search(r"Rate Limit:\s*(\d+) req/sec/(uid|ip)", text, re.I)
    assert ep["rate_limit"] == {"per_sec": int(rl.group(1)), "scope": rl.group(2).lower()}
    names = flat(ep["params"])
    req_tbl = tbls[0] if "Request Parameters" in text.split("[TABLE]")[0][-120:] else None
    if req_tbl:
        for row in req_tbl[1:]:
            name = row[0].lstrip(">").strip()
            assert name in names, f"{stem}: request param {name} missing from code"
            doc_req = row[2].strip().lower() == "true"
            assert names[name]["doc_required"] == doc_req, f"{stem}.{name} required flag differs"
    resp_names = {r["name"] for r in ep["response"]}
    resp_tbl = tbls[-1]
    if "Response Parameters" in text:
        for row in resp_tbl[1:]:
            if row[0] and row[0] != "N/A":
                assert row[0] in resp_names, f"{stem}: response field {row[0]} missing"


def test_enums_are_documented_values():
    corpus = " ".join(p.read_text() for p in TXT.glob("*.txt"))
    for ep in SPEC.values():
        for p in list(flat(ep["params"]).values()):
            for e in p.get("enum", []):
                assert e in corpus, f"enum value {e} not in docs"


def test_every_error_code():
    _, tbls = tables("ErrorCode__error_code")
    doc_codes = {int(r[0]): r[1] for r in tbls[0][1:]}
    assert doc_codes == {k: v for k, v in ERROR_CODES.items()}


WS_STEMS = {
    "depth": "websocket__public__depth_channel", "tickers": "websocket__public__Tickers_Channel",
    "ticker": "websocket__public__Ticker_Channel", "price": "websocket__public__MarketPrice_Channel",
    "kline": "websocket__public__kline_channel", "trade": "websocket__public__Trade_Channel",
    "balance": "websocket__private__Balance_Channel", "position": "websocket__private__Position_Channel",
    "order": "websocket__private__Order_Channel", "tpsl": "websocket__private__Tp_Sl_Channel",
}


@pytest.mark.parametrize("name,stem", list(WS_STEMS.items()))
def test_ws_push_fields(name, stem):
    text, tbls = tables(stem)
    push = [t for t in tbls if t[0][0] == "Parameter" and any(r[0].startswith(">") for r in t[1:])]
    fields = {k.split(".")[-1] for k in WS_CHANNELS[name]["push"]}
    for tbl in push:
        for row in tbl[1:]:
            if row[0].startswith(">") and row[0].lstrip("> ") not in ("ch", "symbol"):
                assert row[0].lstrip("> ").strip() in fields, f"{name}: push field {row[0]} missing"


def test_ws_kline_channels_and_limits():
    text, _ = tables("websocket__public__kline_channel")
    listed = re.findall(r"(?:market|mark)_kline_\w+?(?=,|\n| )", text)
    for ch in listed:
        assert ch in C.WS_KLINE_CHANNELS, ch
    assert len(C.WS_KLINE_CHANNELS) == 30
    prep, _ = tables("websocket__prepare__WebSocket")
    assert "300 channel" in prep and C.WS_MAX_CHANNELS_PER_CONNECTION == 300
    assert "5 messages per second" in prep and C.WS_MAX_MESSAGES_PER_SEC == 5
    assert C.WS_PUBLIC_URL in prep and C.WS_PRIVATE_URL in prep


def test_depth_channels():
    text, _ = tables("websocket__public__depth_channel")
    for ch in C.WS_DEPTH_CHANNELS:
        assert ch in text


def test_changelog_respected():
    text, _ = tables("log__change_log")
    assert "startTime is not supported" in text
    assert "startTime" not in flat(SPEC["get_kline"]["params"])
    assert "MARK_PRICE" in next(p for p in SPEC["get_kline"]["params"] if p["name"] == "type")["enum"]
