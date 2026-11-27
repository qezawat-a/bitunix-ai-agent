"""Risk guard rails. They never just say no: every violation comes back with a concrete adjusted plan."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from decimal import Decimal

from ..config import Settings


@dataclass
class RiskVerdict:
    ok: bool
    warnings: list[str] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)
    adjusted: dict = field(default_factory=dict)  # a compliant version of the same trade

    def to_dict(self):
        return {"ok": self.ok, "warnings": self.warnings, "violations": self.violations, "adjusted_plan": self.adjusted}


class RiskManager:
    def __init__(self, settings: Settings):
        self.s = settings
        self.kill_switch = False
        self._day = time.strftime("%Y-%m-%d")
        self.realized_today = Decimal(0)

    def record_realized(self, pnl: Decimal):
        today = time.strftime("%Y-%m-%d")
        if today != self._day:
            self._day, self.realized_today = today, Decimal(0)
        self.realized_today += pnl

    def check_open(self, *, symbol: str, qty: Decimal, price: Decimal, leverage: int, stop_loss: Decimal | None,
                   equity: Decimal | None, side: str) -> RiskVerdict:
        v = RiskVerdict(ok=True)
        adj: dict = {}
        if self.kill_switch:
            v.violations.append("Kill switch is ON (new positions paused). Say 'disable kill switch' to resume.")
        if self.s.symbol_allowlist and symbol not in self.s.symbol_allowlist:
            v.violations.append(f"{symbol} is not in RISK_SYMBOL_ALLOWLIST {self.s.symbol_allowlist}.")
        if leverage > self.s.max_leverage:
            v.violations.append(f"Leverage {leverage}x > RISK_MAX_LEVERAGE {self.s.max_leverage}x.")
            adj["leverage"] = self.s.max_leverage
        notional = qty * price
        if self.s.max_position_notional and notional > Decimal(str(self.s.max_position_notional)):
            v.violations.append(f"Notional {notional:.2f} > RISK_MAX_POSITION_NOTIONAL {self.s.max_position_notional}.")
            adj["qty"] = str((Decimal(str(self.s.max_position_notional)) / price))
        if self.s.require_stop_loss and stop_loss is None:
            v.violations.append("RISK_REQUIRE_STOP_LOSS is on and no stop loss was given.")
            adj["needs_stop_loss"] = True
        if stop_loss is not None and equity and self.s.max_risk_per_trade_pct:
            loss = abs(price - stop_loss) * qty
            cap = equity * Decimal(str(self.s.max_risk_per_trade_pct)) / 100
            if loss > cap:
                v.violations.append(f"Loss at SL {loss:.2f} > {self.s.max_risk_per_trade_pct}% of equity ({cap:.2f}).")
                adj["qty"] = str(cap / abs(price - stop_loss))
        if self.s.daily_loss_limit and self.realized_today <= -Decimal(str(self.s.daily_loss_limit)):
            v.violations.append(f"Daily loss limit {self.s.daily_loss_limit} reached ({self.realized_today:.2f}).")
        if stop_loss is None:
            v.warnings.append("No stop loss on this trade.")
        if leverage >= 25:
            v.warnings.append(f"High leverage {leverage}x: a {100 / leverage:.2f}% move against you wipes the initial margin.")
        v.ok = not v.violations
        v.adjusted = adj
        return v

    def status(self) -> dict:
        return {"kill_switch": self.kill_switch, "max_leverage": self.s.max_leverage,
                "max_position_notional": self.s.max_position_notional, "max_risk_per_trade_pct": self.s.max_risk_per_trade_pct,
                "daily_loss_limit": self.s.daily_loss_limit, "realized_today": str(self.realized_today),
                "require_stop_loss": self.s.require_stop_loss, "symbol_allowlist": self.s.symbol_allowlist}
