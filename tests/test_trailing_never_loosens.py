import asyncio
from decimal import Decimal as D

from bitunix_agent.engine.monitor import Monitor
from bitunix_agent.storage.db import DB


class FakeEngine:
    def __init__(self, mark, current_sl):
        self.mark, self.sl, self.calls = D(str(mark)), current_sl, []

    async def find_position(self, symbol, side=None, position_id=None):
        return {"positionId": "p1", "side": "LONG", "qty": "10"}

    async def mark_price(self, symbol):
        return self.mark

    async def current_stop(self, symbol, position_id):
        return None if self.sl is None else D(str(self.sl))

    async def set_tpsl(self, **kw):
        self.calls.append(kw["stop_loss"])
        return {"ok": True}


async def _tick(engine, callback):
    db = DB(":memory:")
    db.set_trailing("p1", "c", "PORTALUSDT", "LONG", callback)
    m = Monitor.__new__(Monitor)
    m.engine, m.db, m.poll = engine, db, 0.01
    task = asyncio.create_task(m._trailing_loop())
    await asyncio.sleep(0.05)
    task.cancel()


def test_trailing_does_not_loosen_breakeven_stop():
    e = FakeEngine(0.03924, 0.03862)          # breakeven SL already in place, 20% callback would put SL at 0.0314
    asyncio.run(_tick(e, 20))
    assert e.calls == []


def test_trailing_tightens_when_better():
    e = FakeEngine(0.0400, 0.03862)           # 1% callback -> 0.0396 > 0.03862
    asyncio.run(_tick(e, 1))
    assert e.calls and e.calls[0] > D("0.03862")
