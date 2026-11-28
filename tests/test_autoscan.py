import os

from bitunix_agent.engine import autoscan
from bitunix_agent.engine.autoscan import ScanConfig, evaluate, parse_ladder

BULL = {"last_close": 1.05, "ema20": 1.03, "ema50": 1.0, "adx14": 30, "macd": 0.01, "macd_signal": 0.005, "macd_hist": 0.005,
        "atr14": 0.01, "rsi14": 62, "stoch_rsi_k": 70, "stoch_rsi_d": 60, "vwap_window": 1.02, "bb_mid": 1.03}
BEAR = {"last_close": 0.95, "ema20": 0.97, "ema50": 1.0, "adx14": 30, "macd": -0.01, "macd_signal": -0.005, "macd_hist": -0.005,
        "atr14": 0.01, "rsi14": 38, "stoch_rsi_k": 30, "stoch_rsi_d": 40, "vwap_window": 0.98, "bb_mid": 0.97}


def test_defaults(monkeypatch):
    for k in list(os.environ):
        if k.startswith("SCAN_"):
            monkeypatch.delenv(k)
    c = ScanConfig()
    assert c.timeframes == ["1m", "3m", "5m", "15m"] and c.min_tf_conf == 60 and c.min_agree_tf == 2 and c.min_overall == 70


def test_signal_long():
    ev = evaluate({"1m": BULL, "3m": BULL, "5m": BULL, "15m": {}}, ScanConfig())
    assert ev["signal"] and ev["direction"] == "LONG" and ev["agree_tf"] == 3


def test_no_signal_when_split(monkeypatch):
    monkeypatch.setenv("SCAN_MIN_AGREE_TF", "3")
    ev = evaluate({"1m": BULL, "3m": BULL, "5m": BEAR, "15m": BEAR}, ScanConfig())
    assert not ev["signal"]


def test_settings_change_live(monkeypatch):
    monkeypatch.setenv("SCAN_TIMEFRAMES", "1m,5m")
    monkeypatch.setenv("SCAN_MIN_OVERALL", "99")
    c = ScanConfig()
    assert c.timeframes == ["1m", "5m"]
    assert not evaluate({"1m": BULL, "5m": BULL}, c)["signal"]


def test_trend_filter(monkeypatch):
    monkeypatch.setenv("SCAN_TREND_TF", "4h")
    ev = evaluate({"1m": BULL, "3m": BULL}, ScanConfig(), trend="down")
    assert not ev["signal"] and "trend" in ev["reason"]


def test_ladder_and_describe():
    assert parse_ladder("1.5:25, 2.5:25,3.5:50%") == [(1.5, 25), (2.5, 25), (3.5, 50)]
    assert "SCAN_TIMEFRAMES" in autoscan.describe_settings()
