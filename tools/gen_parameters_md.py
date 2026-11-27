"""Writes PARAMETERS.md: every official endpoint/channel/field and where it lives in the code."""
from pathlib import Path
import sys; sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bitunix_agent.exchange.spec_generated import SPEC
from bitunix_agent.exchange.ws_spec import WS_CHANNELS, WS_OPS
from bitunix_agent.exchange.errors import ERROR_CODES

L = ["# Parameter coverage", "", "Generated from the official docs by `tools/gen_parameters_md.py`. Every row is enforced by `tests/test_spec_vs_docs.py`.", "",
     f"REST endpoints: **{len(SPEC)}** · WebSocket channels: **{len(WS_CHANNELS)}** · error codes: **{len(ERROR_CODES)}**", ""]
groups = {}
for k, v in SPEC.items():
    groups.setdefault(v["doc_url"].split("/futures/")[1].split("/")[0], []).append((k, v))
for g, eps in groups.items():
    L += [f"## {g.upper()}", ""]
    for k, v in eps:
        L += [f"### `{k}` — {v['method']} `{v['path']}`", f"{v['rate_limit']['per_sec']} req/s per {v['rate_limit']['scope']} · {'public' if v['public'] else 'signed'} · tool `api_{k}` · [docs]({v['doc_url']})", ""]
        if v["params"]:
            L += ["| Parameter | Type | Required | Enum | Description |", "|---|---|---|---|---|"]
            def rows(ps, pre=""):
                for p in ps:
                    req = "yes" if p["required"] else ("hedge-mode" if p["doc_required"] else "no")
                    L.append(f"| `{pre}{p['name']}` | {p['type']} | {req} | {', '.join(p.get('enum', []))} | {p['description'].replace('|', '/')[:160]} |")
                    rows(p.get("children", []), pre + p["name"] + ".")
            rows(v["params"])
            L.append("")
        if v["rules"]:
            L += ["Rules: " + "; ".join(f"`{r}`" for r in v["rules"]), ""]
        if v["response"]:
            L += ["Response: " + ", ".join(f"`{r['name']}`" for r in v["response"]), ""]
        for n in v["notes"]:
            L.append(f"> {n}")
        L.append("")
L += ["## WEBSOCKET", "", "Operations: " + ", ".join(f"`{k}`" for k in WS_OPS), ""]
for k, v in WS_CHANNELS.items():
    ch = v["channels"] if isinstance(v["channels"], str) else ", ".join(v["channels"])
    L += [f"### `{k}` ({v['url']}) — {ch}", "Push fields: " + ", ".join(f"`{f}`" for f in v["push"]), ""]
    if v.get("notes"):
        L += [f"> {v['notes']}", ""]
L += ["## ERROR CODES", "", "| Code | Message |", "|---|---|"] + [f"| {c} | {m} |" for c, m in ERROR_CODES.items()]
Path("PARAMETERS.md").write_text("\n".join(L) + "\n")
print("PARAMETERS.md", len(L), "lines")
