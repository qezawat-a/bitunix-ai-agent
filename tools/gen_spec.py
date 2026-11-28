"""
Regenerates bitunix_agent/exchange/spec_generated.py from the official Bitunix
futures documentation snapshot (docs_snapshot/doc_tables.json).

    python tools/fetch_docs.py      # re-download the official pages
    python tools/gen_spec.py        # rebuild the spec from them
    pytest tests/test_spec_vs_docs.py

Every request and response field of every REST endpoint comes straight from the
official tables. Enums, conditional rules and limits that the tables only
describe in prose are added in OVERRIDES below, and every override is checked
by the tests against the doc text.
"""
from __future__ import annotations

import json
import pprint
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TABLES = json.loads((ROOT / "docs_snapshot" / "doc_tables.json").read_text())

BASE_DOC = "https://www.bitunix.com/api-docs/futures/"

# file stem -> (endpoint key, public?)
ENDPOINTS = {
    # ---------------- TP/SL
    "tp_sl__cancel_tp_sl_order": ("cancel_tpsl_order", False),
    "tp_sl__get_history_tp_sl_order": ("get_history_tpsl_orders", False),
    "tp_sl__get_pending_tp_sl_order": ("get_pending_tpsl_orders", False),
    "tp_sl__modify_position_tp_sl_order": ("modify_position_tpsl_order", False),
    "tp_sl__modify_tp_sl_order": ("modify_tpsl_order", False),
    "tp_sl__place_position_tp_sl_order": ("place_position_tpsl_order", False),
    "tp_sl__place_tp_sl_order": ("place_tpsl_order", False),
    # ---------------- Account
    "account__get_leverage_and_margin_mode": ("get_leverage_margin_mode", False),
    "account__adjust_position_margin": ("adjust_position_margin", False),
    "account__change_leverage": ("change_leverage", False),
    "account__change_margin_mode": ("change_margin_mode", False),
    "account__change_position_mode": ("change_position_mode", False),
    "account__get_trading_settings": ("get_trading_settings", False),
    "account__get_single_account": ("get_single_account", False),
    # ---------------- Market (public)
    "market__get_kline": ("get_kline", True),
    "market__get_funding_rate": ("get_funding_rate", True),
    "market__get_funding_rate_history": ("get_funding_rate_history", True),
    "market__get_funding_rate_batch": ("get_funding_rate_batch", True),
    "market__get_depth": ("get_depth", True),
    "market__get_trading_pairs": ("get_trading_pairs", True),
    "market__get_tickers": ("get_tickers", True),
    # ---------------- Position
    "position__get_position_tiers": ("get_position_tiers", True),
    "position__get_pending_positions": ("get_pending_positions", False),
    "position__get_history_positions": ("get_history_positions", False),
    # ---------------- Trade
    "trade__flash_close_position": ("flash_close_position", False),
    "trade__close_all_position": ("close_all_position", False),
    "trade__cancel_all_orders": ("cancel_all_orders", False),
    "trade__cancel_orders": ("cancel_orders", False),
    "trade__batch_order": ("batch_order", False),
    "trade__get_pending_orders": ("get_pending_orders", False),
    "trade__get_order_detail": ("get_order_detail", False),
    "trade__get_history_orders": ("get_history_orders", False),
    "trade__get_history_trades": ("get_history_trades", False),
    "trade__place_order": ("place_order", False),
    "trade__modify_order": ("modify_order", False),
}

SIDE = ["BUY", "SELL"]
TRADE_SIDE = ["OPEN", "CLOSE"]
ORDER_TYPE = ["LIMIT", "MARKET"]
EFFECT = ["IOC", "FOK", "GTC", "POST_ONLY"]
STOP_TYPE = ["MARK_PRICE", "LAST_PRICE"]
MARGIN_MODE = ["ISOLATION", "CROSS"]
POSITION_MODE = ["ONE_WAY", "HEDGE"]
POSITION_SIDE = ["LONG", "SHORT"]
KLINE_INTERVAL = ["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "3d", "1w", "1M"]
KLINE_TYPE = ["LAST_PRICE", "MARK_PRICE"]
DEPTH_LIMIT = ["1", "5", "15", "50", "max"]

ORDER_TPSL_ENUMS = {
    "tpStopType": STOP_TYPE, "slStopType": STOP_TYPE,
    "tpOrderType": ORDER_TYPE, "slOrderType": ORDER_TYPE,
}
ORDER_ENUMS = {"side": SIDE, "tradeSide": TRADE_SIDE, "orderType": ORDER_TYPE, "effect": EFFECT, **ORDER_TPSL_ENUMS}
ORDER_RULES = [
    {"rule": "required_if", "field": "price", "when": {"orderType": "LIMIT"}},
    {"rule": "required_if", "field": "tpOrderPrice", "when": {"tpOrderType": "LIMIT"}},
    {"rule": "required_if", "field": "slOrderPrice", "when": {"slOrderType": "LIMIT"}},
    {"rule": "hedge_required", "field": "tradeSide"},
    {"rule": "required_if", "field": "positionId", "when": {"tradeSide": "CLOSE"}},
]

# key -> overrides
OVERRIDES: dict[str, dict] = {
    "place_order": {
        "enums": ORDER_ENUMS,
        "rules": ORDER_RULES,
        "effective_optional": ["tradeSide"],  # "Only required in hedge-mode"
        "notes": [
            "HEDGE mode: open long = BUY+OPEN, open short = SELL+OPEN, close long = BUY+CLOSE(+positionId), close short = SELL+CLOSE(+positionId) (exactly as the official table states).",
            "ONE_WAY mode: tradeSide is not needed; close by sending the opposite side with reduceOnly=true.",
            "Official example uses tpStopType 'MARK'; the table enum is MARK_PRICE/LAST_PRICE. The client sends MARK_PRICE/LAST_PRICE and accepts MARK/LAST as aliases.",
        ],
    },
    "batch_order": {
        "enums": {f"orderList.{k}": v for k, v in ORDER_ENUMS.items()},
        "rules": [{**r, "field": f"orderList.{r['field']}", **({"when": {f"orderList.{k}": v for k, v in r['when'].items()}} if "when" in r else {})} for r in ORDER_RULES]
        + [{"rule": "max_len", "field": "orderList", "value": 5}],
        "effective_optional": ["orderList.tradeSide"],
        "notes": ["orderList maximum length is 5 (error 20016 when exceeded)."],
    },
    "modify_order": {
        "enums": ORDER_TPSL_ENUMS,
        "rules": [
            {"rule": "one_of", "fields": ["orderId", "clientId"]},
            {"rule": "required_if", "field": "tpOrderPrice", "when": {"tpOrderType": "LIMIT"}},
            {"rule": "required_if", "field": "slOrderPrice", "when": {"slOrderType": "LIMIT"}},
        ],
        "extra_params": [
            {"name": "symbol", "type": "string", "doc_required": False, "description": "Trading pair. Not in the official table but present in the official request example; sent only when given."},
        ],
        "notes": ["A successful response does not guarantee the modification; confirm on the order WebSocket channel."],
    },
    "cancel_orders": {
        # The table lists orderId/clientId at top level, but they are fields of each orderList item
        "reparent": {"orderId": "orderList", "clientId": "orderList"},
        "rules": [{"rule": "one_of", "fields": ["orderList.orderId", "orderList.clientId"]}],
        "notes": ["If both orderId and clientId are given, orderId prevails.", "Confirm cancellation on the order WebSocket channel."],
    },
    "cancel_all_orders": {"notes": ["Response list items use 'orderId' in the official example although the table says 'id'. Both are handled."]},
    "adjust_position_margin": {
        "enums": {"side": POSITION_SIDE},
        "rules": [{"rule": "one_of", "fields": ["side", "positionId"]}],
        "notes": ["Isolated margin mode only. amount > 0 adds margin, amount < 0 removes margin."],
    },
    "change_margin_mode": {"enums": {"marginMode": MARGIN_MODE}, "notes": ["Fails while the user has an open position or order on the symbol (error 20006)."]},
    "change_position_mode": {"enums": {"positionMode": POSITION_MODE}, "notes": ["Fails while any position or order exists (error 20009 / 200014)."]},
    "get_trading_settings": {"rules": [{"rule": "max_csv", "field": "symbols", "value": 50}]},
    "get_order_detail": {"rules": [{"rule": "one_of", "fields": ["orderId", "clientId"]}]},
    "get_pending_orders": {"enums": {"status": ["NEW", "PART_FILLED"]}, "rules": [{"rule": "max", "field": "limit", "value": 100}]},
    "get_history_orders": {
        "enums": {"status": ["FILLED", "CANCELED", "PART_FILLED_CANCELED", "EXPIRED"], "type": ORDER_TYPE},
        "rules": [{"rule": "max", "field": "limit", "value": 100}],
        "notes": ["queryCanceled=true returns only canceled orders (last 3 days); false excludes them (last 90 days)."],
    },
    "get_history_trades": {"rules": [{"rule": "max", "field": "limit", "value": 100}]},
    "get_history_positions": {"rules": [{"rule": "max", "field": "limit", "value": 100}]},
    "get_pending_tpsl_orders": {"rules": [{"rule": "max", "field": "limit", "value": 100}],
                                "notes": ["side and positionMode are int32 per the official table; their numeric codes are not documented."]},
    "get_history_tpsl_orders": {"rules": [{"rule": "max", "field": "limit", "value": 100}],
                                "notes": ["The official response example is copy-pasted from positions; the response table (orderList/total) is authoritative."]},
    "place_tpsl_order": {
        "enums": ORDER_TPSL_ENUMS,
        "rules": [
            {"rule": "any_of", "fields": ["tpPrice", "slPrice"]},
            {"rule": "any_of", "fields": ["tpQty", "slQty"]},
            {"rule": "required_if", "field": "tpOrderPrice", "when": {"tpOrderType": "LIMIT"}},
            {"rule": "required_if", "field": "slOrderPrice", "when": {"slOrderType": "LIMIT"}},
        ],
    },
    "modify_tpsl_order": {
        "enums": ORDER_TPSL_ENUMS,
        "rules": [
            {"rule": "any_of", "fields": ["tpPrice", "slPrice"]},
            {"rule": "any_of", "fields": ["tpQty", "slQty"]},
            {"rule": "required_if", "field": "tpOrderPrice", "when": {"tpOrderType": "LIMIT"}},
            {"rule": "required_if", "field": "slOrderPrice", "when": {"slOrderType": "LIMIT"}},
        ],
    },
    "place_position_tpsl_order": {
        "enums": {"tpStopType": STOP_TYPE, "slStopType": STOP_TYPE},
        "rules": [{"rule": "any_of", "fields": ["tpPrice", "slPrice"]}],
        "notes": ["Closes the whole position at market when triggered. Only one position TP/SL per position."],
    },
    "modify_position_tpsl_order": {
        "enums": {"tpStopType": STOP_TYPE, "slStopType": STOP_TYPE},
        "rules": [{"rule": "any_of", "fields": ["tpPrice", "slPrice"]}],
    },
    "cancel_tpsl_order": {"notes": ["Confirm on the tpsl WebSocket channel."]},
    "get_kline": {
        "enums": {"interval": KLINE_INTERVAL, "type": KLINE_TYPE},
        "rules": [{"rule": "max", "field": "limit", "value": 200}],
        "notes": [
            "startTime was removed (changelog 2026-10-27). Query = endTime + limit counting backward; endTime candle excluded.",
            "time is the candle OPEN time; only closed candles are returned (changelog 2026-10-08).",
            "Live API returns newest first and time as a string; the client normalizes to oldest-first ints.",
            "3m is not in the REST interval list but the live API serves it (verified 2026-11-28) and the WS kline docs list 3min; added at the user's request.",
        ],
    },
    "get_depth": {"enums": {"limit": DEPTH_LIMIT}},
    "get_funding_rate": {"notes": ["Live API returns a single object (the doc example shows a list); normalized to an object."]},
    "get_funding_rate_history": {"rules": [{"rule": "max", "field": "limit", "value": 200}]},
    "get_trading_pairs": {"notes": ["defaultMarginMode is returned as a code string ('1'/'2') by the live API."]},
}

TYPE_MAP = {
    "string": "string", "String": "string", "int": "int", "int32": "int", "int64": "int",
    "boolean": "bool", "bool": "bool", "list": "list", "decimal": "string", "long": "int", "integer": "int",
}


def parse_rate(rate: str | None):
    if not rate:
        return None
    m = re.match(r"(\d+)\s*req/sec/(\w+)", rate)
    return {"per_sec": int(m.group(1)), "scope": m.group(2).lower()} if m else None


def split_tables(doc):
    req, resp = None, None
    for t in doc["tables"]:
        b = t["before"]
        if "Request Parameters" in b and req is None and "Response" not in b[-40:]:
            req = t
        elif "Response Parameters" in b:
            resp = t
    return req, resp


def build():
    spec = {}
    for stem, (key, public) in ENDPOINTS.items():
        doc = TABLES[stem]
        req, resp = split_tables(doc)
        ov = OVERRIDES.get(key, {})
        params = []
        parent = None
        for row in (req["rows"] if req else []):
            name, typ, required, *desc = row + [""] * (4 - len(row))
            nested = name.startswith(">")
            name = name.lstrip(">").strip()
            p = {"name": name, "type": TYPE_MAP.get(typ.strip(), typ.strip()),
                 "doc_required": required.strip().lower() == "true", "description": " ".join(desc).strip()}
            reparent = ov.get("reparent", {}).get(name)
            if nested or reparent:
                owner = reparent or parent
                for pp in params:
                    if pp["name"] == owner:
                        pp.setdefault("children", []).append(p)
                        break
            else:
                params.append(p)
                if p["type"] == "list":
                    parent = name
        for extra in ov.get("extra_params", []):
            params.append(dict(extra))
        # apply enums + effective required
        eff_opt = set(ov.get("effective_optional", []))
        enums = ov.get("enums", {})

        def apply(plist, prefix=""):
            for p in plist:
                full = prefix + p["name"]
                p["required"] = p["doc_required"] and full not in eff_opt
                if full in enums:
                    p["enum"] = enums[full]
                if p.get("children"):
                    apply(p["children"], full + ".")
        apply(params)
        response = []
        for row in (resp["rows"] if resp else []):
            if not row or not row[0] or row[0] == "N/A":
                continue
            response.append({"name": row[0], "type": TYPE_MAP.get(row[1].strip(), row[1].strip()) if len(row) > 1 else "", "description": row[2] if len(row) > 2 else ""})
        url_stem = stem.replace("__", "/")
        spec[key] = {
            "key": key,
            "title": doc["tables"] and stem,
            "method": doc["method"],
            "path": doc["path"],
            "public": public,
            "mutating": doc["method"] == "POST",
            "rate_limit": parse_rate(doc["rate"]),
            "doc_url": BASE_DOC + url_stem + ".html",
            "params": params,
            "response": response,
            "rules": ov.get("rules", []),
            "notes": ov.get("notes", []),
        }
    return spec


def main():
    spec = build()
    out = ROOT / "bitunix_agent" / "exchange" / "spec_generated.py"
    out.write_text(
        '"""AUTO-GENERATED by tools/gen_spec.py from the official Bitunix futures docs. Do not edit by hand."""\n'
        "# fmt: off\n"
        f"SPEC = {pprint.pformat(spec, width=140, sort_dicts=False)}\n"
    )
    print(f"wrote {out} with {len(spec)} endpoints")
    write_methods(spec)


PY_T = {"string": "Num | str", "int": "int", "bool": "bool", "list": "list[dict]"}


def write_methods(spec):
    """One explicit async method per endpoint, every official parameter in its signature."""
    lines = [
        '"""AUTO-GENERATED by tools/gen_spec.py. One method per official Bitunix futures endpoint."""',
        "# fmt: off",
        "from __future__ import annotations",
        "",
        "from decimal import Decimal",
        "from typing import Any, Optional, Union",
        "",
        "Num = Union[str, int, float, Decimal]",
        "",
        "",
        "class GeneratedEndpoints:",
        '    """Mixin: requires self.call(key, params)."""',
        "",
        "    async def call(self, key: str, params: dict | None = None) -> Any:  # pragma: no cover",
        "        raise NotImplementedError",
        "",
    ]
    for key, ep in spec.items():
        req = [p for p in ep["params"] if p["required"]]
        opt = [p for p in ep["params"] if not p["required"]]
        args = []
        for p in req:
            args.append(f"{p['name']}: {PY_T.get(p['type'], 'Any')}")
        for p in opt:
            args.append(f"{p['name']}: Optional[{PY_T.get(p['type'], 'Any')}] = None")
        sig = ", ".join(["self"] + (["*"] + args if args else []))
        lines.append(f"    async def {key}({sig}) -> Any:")
        doc = [f"{ep['method']} {ep['path']}  (rate limit {ep['rate_limit']['per_sec']}/s per {ep['rate_limit']['scope']})", f"Docs: {ep['doc_url']}", ""]
        def pdoc(plist, ind="    "):
            for p in plist:
                enum = f" one of {p['enum']}" if p.get("enum") else ""
                req_s = "required" if p["required"] else "optional"
                d = p["description"].replace('"""', "'")
                doc.append(f"{ind}{p['name']} ({p['type']}, {req_s}){enum}: {d}")
                if p.get("children"):
                    pdoc(p["children"], ind + "    ")
        if ep["params"]:
            doc.append("Parameters:")
            pdoc(ep["params"])
        for n in ep["notes"]:
            doc.append(f"Note: {n}")
        lines.append('        """' + ("\n        ".join(doc)).replace("\\", "/") + '\n        """')
        names = [p["name"] for p in ep["params"]]
        if names:
            lines.append("        params = {" + ", ".join(f"'{n}': {n}" for n in names) + "}")
            lines.append(f"        return await self.call('{key}', {{k: v for k, v in params.items() if v is not None}})")
        else:
            lines.append(f"        return await self.call('{key}', {{}})")
        lines.append("")
    out = ROOT / "bitunix_agent" / "exchange" / "endpoints_generated.py"
    out.write_text("\n".join(lines) + "\n")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
