"""Re-download the official Bitunix futures docs and rebuild docs_snapshot/.

    python tools/fetch_docs.py && python tools/gen_spec.py && pytest

If Bitunix changes a parameter, the spec tests fail and tell you exactly which field changed.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import httpx
import lxml.html as LH

ROOT = Path(__file__).resolve().parents[1]
SNAP = ROOT / "docs_snapshot"


def to_text(html: str) -> str:
    d = LH.fromstring(html)
    m = (d.xpath("//main") or d.xpath('//div[contains(@class,"theme-default-content")]') or [d.body])[0]
    for t in m.xpath(".//table"):
        rows = [" | ".join(" ".join(c.text_content().split()) for c in tr.xpath("./td|./th")) for tr in t.xpath(".//tr")]
        e = LH.Element("pre")
        e.text = "\n[TABLE]\n" + "\n".join(rows) + "\n[/TABLE]\n"
        t.getparent().replace(t, e)
    for s in m.xpath(".//nav|.//aside|.//script|.//style"):
        s.getparent().remove(s)
    return re.sub(r"\n\s*\n+", "\n", m.text_content())


def parse(stem: str, t: str) -> dict:
    d = {"file": stem}
    m = re.search(r"Rate Limit:\s*([^\n]*?)Description", t)
    d["rate"] = m.group(1).strip() if m else None
    m = re.search(r"HTTP Request\s*\u200b?\s*(GET|POST)\s+(\S+?)(?:Request|$)", t)
    if m:
        d["method"], d["path"] = m.group(1), m.group(2)
    parts = re.split(r"\[TABLE\]\n", t)
    d["tables"] = []
    for i, p in enumerate(parts[1:]):
        body = p.split("\n[/TABLE]")[0]
        rows = [r.split(" | ") for r in body.strip().split("\n")]
        d["tables"].append({"before": parts[i][-80:].replace("\n", " "), "header": rows[0], "rows": rows[1:]})
    return d


def main():
    urls = (SNAP / "urls.txt").read_text().split()
    out_txt = SNAP / "official_txt"
    out_txt.mkdir(parents=True, exist_ok=True)
    tables = {}
    with httpx.Client(timeout=30, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"}) as c:
        for u in urls:
            stem = u.split("/futures/")[1].replace(".html", "").replace("/", "__").replace("%20", "_")
            txt = to_text(c.get(u).text)
            (out_txt / f"{stem}.txt").write_text(txt)
            tables[stem] = parse(stem, txt)
            print("ok", stem)
    (SNAP / "doc_tables.json").write_text(json.dumps(tables, indent=1))


if __name__ == "__main__":
    main()
