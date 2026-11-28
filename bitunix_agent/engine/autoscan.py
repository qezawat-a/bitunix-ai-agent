"""Code-side signal scan + status report (no LLM). Every rule is a setting you can change from chat with `set NAME=value`.

Settings (read live on every run, so a `set` applies at the next scan):
  AUTO_SCAN=false                  turn the scan (and its real orders) on/off
  SCAN_SYMBOLS=PORTALUSDT          comma list
  SCAN_TIMEFRAMES=1m,3m,5m,15m     comma list (any Bitunix kline interval)
  SCAN_EVERY_SEC=15
  SCAN_STRATEGIES=ema,macd,rsi,stoch,vwap
  SCAN_MIN_STRATEGIES=2            strategies that must agree inside one timeframe
  SCAN_MIN_TF_CONF=60              a timeframe counts only at >= this confidence %
  SCAN_MIN_AGREE_TF=2              timeframes that must agree on the direction
  SCAN_MIN_OVERALL=70              average confidence % of the agreeing timeframes
  SCAN_BLOCK_OPPOSING=false        true = no trade if any counted timeframe points the other way
  SCAN_TREND_TF=                   e.g. 4h: skip a trade against that timeframe's trend (empty = off)
  SCAN_LEVERAGE=30  SCAN_MARGIN_MODE=CROSS  SCAN_MARGIN_PCT=25 (of available balance)
  SCAN_SL_ATR=0.5  SCAN_ATR_TF=5m  SCAN_TP_LADDER=1.5:25,2.5:25,3.5:50 (R multiple:percent of qty; empty = none)
  SCAN_MAX_POSITIONS=1  SCAN_COOLDOWN_SEC=60
  REPORT_EVERY_SEC=0               status report interval (0 = off)
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def env(name: str, default: str = "") -> str:
    v = os.getenv(name)
    return default if v is None else v.split("#", 1)[0].strip()


def env_float(name: str, default: float) -> float:
    try:
        return float(env(name, str(default)).replace("%", "").replace("x", "") or default)
    except ValueError:
        return default


def env_bool(name: str, default: bool) -> bool:
    v = env(name, "")
    return default if v == "" else v.lower() in ("1", "true", "yes", "on")


def env_list(name: str, default: str) -> list[str]:
    return [x.strip() for x in env(name, default).replace(";", ",").split(",") if x.strip()]


@dataclass
class ScanConfig:
    symbols: list[str] = field(default_factory=lambda: [s.upper() for s in env_list("SCAN_SYMBOLS", "PORTALUSDT")])
    timeframes: list[str] = field(default_factory=lambda: env_list("SCAN_TIMEFRAMES", "1m,3m,5m,15m"))
    strategies: list[str] = field(default_factory=lambda: [s.lower() for s in env_list("SCAN_STRATEGIES", "ema,macd,rsi,stoch,vwap")])
    min_strategies: int = field(default_factory=lambda: int(env_float("SCAN_MIN_STRATEGIES", 2)))
    min_tf_conf: float = field(default_factory=lambda: env_float("SCAN_MIN_TF_CONF", 60))
    min_agree_tf: int = field(default_factory=lambda: int(env_float("SCAN_MIN_AGREE_TF", 2)))
    min_overall: float = field(default_factory=lambda: env_float("SCAN_MIN_OVERALL", 70))
    block_opposing: bool = field(default_factory=lambda: env_bool("SCAN_BLOCK_OPPOSING", False))
    trend_tf: str = field(default_factory=lambda: env("SCAN_TREND_TF", ""))
    leverage: int = field(default_factory=lambda: int(env_float("SCAN_LEVERAGE", 30)))
    margin_mode: str = field(default_factory=lambda: env("SCAN_MARGIN_MODE", "CROSS").upper())
    margin_pct: float = field(default_factory=lambda: env_float("SCAN_MARGIN_PCT", 25))
    sl_atr: float = field(default_factory=lambda: env_float("SCAN_SL_ATR", 0.5))
    atr_tf: str = field(default_factory=lambda: env("SCAN_ATR_TF", "5m"))
    tp_ladder: list[tuple[float, float]] = field(default_factory=lambda: parse_ladder(env("SCAN_TP_LADDER", "1.5:25,2.5:25,3.5:50")))
    max_positions: int = field(default_factory=lambda: int(env_float("SCAN_MAX_POSITIONS", 1)))
    cooldown_sec: float = field(default_factory=lambda: env_float("SCAN_COOLDOWN_SEC", 60))


def parse_ladder(s: str) -> list[tuple[float, float]]:
    out = []
    for part in s.replace(";", ",").split(","):
        if ":" in part:
            try:
                r, pct = part.split(":", 1)
                out.append((float(r), float(pct.replace("%", ""))))
            except ValueError:
                continue
    return out


# ---------------------------------------------------------------- strategies: each returns (direction, confidence%) or None
def _clip(x: float, lo: float = 50, hi: float = 95) -> float:
    return max(lo, min(hi, x))


def s_ema(t: dict):
    last, e20, e50 = t.get("last_close"), t.get("ema20"), t.get("ema50")
    if not (last and e20 and e50):
        return None
    adx = t.get("adx14") or 20
    if last > e20 > e50:
        return "LONG", _clip(50 + adx)
    if last < e20 < e50:
        return "SHORT", _clip(50 + adx)
    return None


def s_macd(t: dict):
    m, sig, hist, atr = t.get("macd"), t.get("macd_signal"), t.get("macd_hist"), t.get("atr14")
    if m is None or sig is None or hist is None:
        return None
    strength = abs(hist) / atr * 100 if atr else 10
    if hist > 0 and m > sig:
        return "LONG", _clip(55 + strength)
    if hist < 0 and m < sig:
        return "SHORT", _clip(55 + strength)
    return None


def s_rsi(t: dict):
    r = t.get("rsi14")
    if r is None:
        return None
    if 55 <= r <= 80:
        return "LONG", _clip(50 + (r - 50))
    if 20 <= r <= 45:
        return "SHORT", _clip(50 + (50 - r))
    return None


def s_stoch(t: dict):
    k, d = t.get("stoch_rsi_k"), t.get("stoch_rsi_d")
    if k is None or d is None:
        return None
    if k > d and k < 85:
        return "LONG", _clip(60 + (k - d) / 2)
    if k < d and k > 15:
        return "SHORT", _clip(60 + (d - k) / 2)
    return None


def s_vwap(t: dict):
    last, vw, mid = t.get("last_close"), t.get("vwap_window"), t.get("bb_mid")
    if not (last and vw and mid):
        return None
    if last > vw and last > mid:
        return "LONG", _clip(60 + (last / vw - 1) * 1000)
    if last < vw and last < mid:
        return "SHORT", _clip(60 + (1 - last / vw) * 1000)
    return None


STRATEGIES = {"ema": s_ema, "macd": s_macd, "rsi": s_rsi, "stoch": s_stoch, "vwap": s_vwap}


def evaluate_tf(t: dict, cfg: ScanConfig) -> dict:
    """One timeframe: run the strategies, pick the majority direction, confidence = mean of the agreeing strategies."""
    if not t or t.get("error"):
        return {"direction": None, "confidence": 0.0, "votes": {}, "error": (t or {}).get("error", "no data")}
    votes = {}
    for name in cfg.strategies:
        fn = STRATEGIES.get(name)
        v = fn(t) if fn else None
        if v:
            votes[name] = v
    longs = [c for d, c in votes.values() if d == "LONG"]
    shorts = [c for d, c in votes.values() if d == "SHORT"]
    side, confs = ("LONG", longs) if len(longs) > len(shorts) else ("SHORT", shorts) if len(shorts) > len(longs) else (None, [])
    if side is None or len(confs) < cfg.min_strategies:
        return {"direction": None, "confidence": 0.0, "votes": {k: v[0] for k, v in votes.items()}}
    return {"direction": side, "confidence": round(sum(confs) / len(confs), 1), "agree": len(confs),
            "votes": {k: v[0] for k, v in votes.items()}}


def evaluate(tfs: dict[str, dict], cfg: ScanConfig, trend: str | None = None) -> dict:
    """All timeframes -> signal or not, with the reason."""
    per = {iv: evaluate_tf(t, cfg) for iv, t in tfs.items()}
    counted = {iv: r for iv, r in per.items() if r["direction"] and r["confidence"] >= cfg.min_tf_conf}
    longs = [r["confidence"] for r in counted.values() if r["direction"] == "LONG"]
    shorts = [r["confidence"] for r in counted.values() if r["direction"] == "SHORT"]
    side, confs = ("LONG", longs) if len(longs) > len(shorts) else ("SHORT", shorts) if len(shorts) > len(longs) else (None, [])
    overall = round(sum(confs) / len(confs), 1) if confs else 0.0
    out = {"timeframes": per, "direction": side, "overall": overall, "agree_tf": len(confs), "signal": False}
    if side is None:
        out["reason"] = "no direction agreement"
    elif len(confs) < cfg.min_agree_tf:
        out["reason"] = f"{len(confs)} timeframe(s) agree, need {cfg.min_agree_tf}"
    elif overall < cfg.min_overall:
        out["reason"] = f"overall {overall}% < {cfg.min_overall}%"
    elif cfg.block_opposing and (shorts if side == "LONG" else longs):
        out["reason"] = "a counted timeframe points the other way"
    elif trend and ((side == "LONG" and trend == "down") or (side == "SHORT" and trend == "up")):
        out["reason"] = f"{cfg.trend_tf} trend is {trend}"
    else:
        out["signal"] = True
        out["reason"] = f"{len(confs)} timeframes agree {side} at {overall}%"
    return out


def describe_settings() -> str:
    c = ScanConfig()
    ladder = ",".join(f"{r:g}:{p:g}" for r, p in c.tp_ladder) or "off"
    return (f"AUTO_SCAN={env_bool('AUTO_SCAN', False)}  every {env_float('SCAN_EVERY_SEC', 15):g}s\n"
            f"SCAN_SYMBOLS={','.join(c.symbols)}\nSCAN_TIMEFRAMES={','.join(c.timeframes)}\n"
            f"SCAN_STRATEGIES={','.join(c.strategies)}  SCAN_MIN_STRATEGIES={c.min_strategies}\n"
            f"SCAN_MIN_TF_CONF={c.min_tf_conf:g}  SCAN_MIN_AGREE_TF={c.min_agree_tf}  SCAN_MIN_OVERALL={c.min_overall:g}\n"
            f"SCAN_BLOCK_OPPOSING={c.block_opposing}  SCAN_TREND_TF={c.trend_tf or 'off'}\n"
            f"SCAN_LEVERAGE={c.leverage}  SCAN_MARGIN_MODE={c.margin_mode}  SCAN_MARGIN_PCT={c.margin_pct:g}\n"
            f"SCAN_SL_ATR={c.sl_atr:g} (ATR {c.atr_tf})  SCAN_TP_LADDER={ladder}\n"
            f"SCAN_MAX_POSITIONS={c.max_positions}  SCAN_COOLDOWN_SEC={c.cooldown_sec:g}\n"
            f"REPORT_EVERY_SEC={env_float('REPORT_EVERY_SEC', 0):g}\n"
            f"AUTO_GUARD={env_bool('AUTO_GUARD', True)}  BE at ROI {env_float('AUTO_GUARD_BE_ROI', 15):g}%  "
            f"trailing at ROI {env_float('AUTO_GUARD_TRAIL_ROI', 20):g}% / {env_float('AUTO_GUARD_TRAIL_CALLBACK', 1.0):g}%\n"
            "Change any of them with: set NAME=value")
