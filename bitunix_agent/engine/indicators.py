"""Dependency-free technical indicators on float lists (oldest -> newest)."""
from __future__ import annotations

import math
from typing import Sequence


def sma(x: Sequence[float], n: int) -> list[float | None]:
    out, s = [], 0.0
    for i, v in enumerate(x):
        s += v
        if i >= n:
            s -= x[i - n]
        out.append(s / n if i >= n - 1 else None)
    return out


def ema(x: Sequence[float], n: int) -> list[float | None]:
    out: list[float | None] = []
    k = 2 / (n + 1)
    prev = None
    for i, v in enumerate(x):
        if i < n - 1:
            out.append(None)
            continue
        if prev is None:
            prev = sum(x[i - n + 1:i + 1]) / n
        else:
            prev = v * k + prev * (1 - k)
        out.append(prev)
    return out


def rsi(x: Sequence[float], n: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(x)
    if len(x) <= n:
        return out
    gains = [max(x[i] - x[i - 1], 0) for i in range(1, len(x))]
    losses = [max(x[i - 1] - x[i], 0) for i in range(1, len(x))]
    ag, al = sum(gains[:n]) / n, sum(losses[:n]) / n
    out[n] = 100 - 100 / (1 + ag / al) if al else 100.0
    for i in range(n + 1, len(x)):
        ag = (ag * (n - 1) + gains[i - 1]) / n
        al = (al * (n - 1) + losses[i - 1]) / n
        out[i] = 100 - 100 / (1 + ag / al) if al else 100.0
    return out


def macd(x: Sequence[float], fast=12, slow=26, signal=9):
    ef, es = ema(x, fast), ema(x, slow)
    line = [a - b if a is not None and b is not None else None for a, b in zip(ef, es)]
    valid = [v for v in line if v is not None]
    sig_valid = ema(valid, signal) if len(valid) >= signal else [None] * len(valid)
    sig = [None] * (len(line) - len(valid)) + sig_valid
    hist = [a - b if a is not None and b is not None else None for a, b in zip(line, sig)]
    return line, sig, hist


def true_range(h, l, c):
    return [h[0] - l[0]] + [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])) for i in range(1, len(c))]


def atr(h, l, c, n: int = 14) -> list[float | None]:
    tr = true_range(h, l, c)
    out: list[float | None] = [None] * len(tr)
    if len(tr) < n:
        return out
    a = sum(tr[:n]) / n
    out[n - 1] = a
    for i in range(n, len(tr)):
        a = (a * (n - 1) + tr[i]) / n
        out[i] = a
    return out


def bollinger(x: Sequence[float], n: int = 20, k: float = 2.0):
    mid = sma(x, n)
    up, lo = [], []
    for i in range(len(x)):
        if mid[i] is None:
            up.append(None); lo.append(None); continue
        w = x[i - n + 1:i + 1]
        sd = math.sqrt(sum((v - mid[i]) ** 2 for v in w) / n)
        up.append(mid[i] + k * sd); lo.append(mid[i] - k * sd)
    return up, mid, lo


def vwap(h, l, c, v) -> list[float | None]:
    out, pv, vv = [], 0.0, 0.0
    for i in range(len(c)):
        tp = (h[i] + l[i] + c[i]) / 3
        pv += tp * v[i]; vv += v[i]
        out.append(pv / vv if vv else None)
    return out


def stoch_rsi(x, n=14, k=3, d=3):
    r = rsi(x, n)
    raw = []
    for i in range(len(r)):
        w = [v for v in r[max(0, i - n + 1):i + 1] if v is not None]
        if r[i] is None or len(w) < n:
            raw.append(None); continue
        lo, hi = min(w), max(w)
        raw.append(0.0 if hi == lo else (r[i] - lo) / (hi - lo) * 100)
    valid = [v for v in raw if v is not None]
    kk = sma(valid, k) if len(valid) >= k else []
    kv = [v for v in kk if v is not None]
    dd = sma(kv, d) if len(kv) >= d else []
    return (kk[-1] if kk else None), (dd[-1] if dd else None)


def adx(h, l, c, n: int = 14) -> float | None:
    if len(c) < 2 * n + 1:
        return None
    pdm = [0.0] + [max(h[i] - h[i - 1], 0) if (h[i] - h[i - 1]) > (l[i - 1] - l[i]) else 0 for i in range(1, len(c))]
    ndm = [0.0] + [max(l[i - 1] - l[i], 0) if (l[i - 1] - l[i]) > (h[i] - h[i - 1]) else 0 for i in range(1, len(c))]
    tr = true_range(h, l, c)
    def wilder(arr):
        s = sum(arr[1:n + 1]); out = [s]
        for v in arr[n + 1:]:
            s = s - s / n + v; out.append(s)
        return out
    trs, p, m = wilder(tr), wilder(pdm), wilder(ndm)
    dx = []
    for a, b, t in zip(p, m, trs):
        pdi, ndi = 100 * a / t if t else 0, 100 * b / t if t else 0
        dx.append(100 * abs(pdi - ndi) / (pdi + ndi) if pdi + ndi else 0)
    if len(dx) < n:
        return None
    a = sum(dx[:n]) / n
    for v in dx[n:]:
        a = (a * (n - 1) + v) / n
    return a


def swing_levels(h, l, lookback: int = 5, count: int = 3):
    """Recent swing highs/lows as simple support/resistance."""
    highs, lows = [], []
    for i in range(lookback, len(h) - lookback):
        if h[i] == max(h[i - lookback:i + lookback + 1]):
            highs.append(h[i])
        if l[i] == min(l[i - lookback:i + lookback + 1]):
            lows.append(l[i])
    return highs[-count:], lows[-count:]


def summarize(candles: list[dict]) -> dict:
    """Full indicator snapshot from REST get_kline output."""
    o = [float(c["open"]) for c in candles]
    h = [float(c["high"]) for c in candles]
    l = [float(c["low"]) for c in candles]
    c = [float(x["close"]) for x in candles]
    v = [float(x.get("baseVol") or 0) for x in candles]
    if len(c) < 30:
        return {"error": f"need >= 30 candles, got {len(c)}"}
    last = c[-1]
    e20, e50, e200 = ema(c, 20)[-1], ema(c, 50)[-1] if len(c) >= 50 else None, ema(c, 200)[-1] if len(c) >= 200 else None
    m, s, hist = macd(c)
    up, mid, lo = bollinger(c)
    a = atr(h, l, c)[-1]
    k, d = stoch_rsi(c)
    rh, rl = swing_levels(h, l)
    trend = "up" if e50 and last > e20 > e50 else "down" if e50 and last < e20 < e50 else "range"
    def r(x, p=6):
        return None if x is None else round(x, p)
    return {
        "last_close": last, "candles": len(c), "change_pct_window": r((last / o[0] - 1) * 100, 3),
        "ema20": r(e20), "ema50": r(e50), "ema200": r(e200), "rsi14": r(rsi(c)[-1], 2),
        "macd": r(m[-1]), "macd_signal": r(s[-1]), "macd_hist": r(hist[-1]),
        "bb_upper": r(up[-1]), "bb_mid": r(mid[-1]), "bb_lower": r(lo[-1]),
        "atr14": r(a), "atr_pct": r(a / last * 100 if a else None, 3),
        "stoch_rsi_k": r(k, 2), "stoch_rsi_d": r(d, 2), "adx14": r(adx(h, l, c), 2),
        "vwap_window": r(vwap(h, l, c, v)[-1]),
        "recent_swing_highs": [r(x) for x in rh], "recent_swing_lows": [r(x) for x in rl],
        "trend": trend,
    }
