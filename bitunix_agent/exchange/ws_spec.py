"""Every WebSocket operation and push field from the official docs (websocket/*)."""
from __future__ import annotations

WS_OPS = {
    "ping": {"request": {"op": "string ('ping')", "ping": "int64 seconds Unix timestamp"},
             "response": {"op": "ping", "pong": "int64", "ping": "int64"}},
    "subscribe": {"request": {"op": "string ('subscribe')", "args": "array of {ch: channel name, symbol: instrument id (optional for private)}"}},
    "unsubscribe": {"request": {"op": "string ('unsubscribe')", "args": "array of {ch, symbol}"}},
    "login": {"request": {"op": "string ('login')", "args": "array of {apiKey, timestamp (int seconds), nonce, sign}"}},
}

WS_CHANNELS = {
    # ------------------------------------------------ public
    "depth": {
        "url": "public", "channels": ["depth_books", "depth_book1", "depth_book5", "depth_book15"],
        "doc": "https://www.bitunix.com/api-docs/futures/websocket/public/depth%20channel.html",
        "push": {"ch": "Channel name", "symbol": "Product ID", "ts": "Int64 time stamp",
                 "data.a": "List<[price, qty]> seller depth", "data.b": "List<[price, qty]> buyer depth"},
        "notes": "depth_books: full snapshot first, then only changed levels; book1/5/15: full levels every push.",
    },
    "tickers": {
        "url": "public", "channels": ["tickers"],
        "doc": "https://www.bitunix.com/api-docs/futures/websocket/public/Tickers%20Channel.html",
        "push": {"ch": "Channel name", "ts": "int64", "data[].s": "symbol", "data[].o": "Opening price", "data[].h": "Highest price",
                 "data[].l": "Lowest price", "data[].la": "Last price", "data[].b": "Trading volume of the coin",
                 "data[].q": "Trading volume of quote currency", "data[].r": "24-hour fluctuations",
                 "data[].bd": "Best Bid Price", "data[].ak": "Best Ask Price", "data[].bv": "Best Bid Volume", "data[].av": "Best Ask Volume"},
    },
    "ticker": {
        "url": "public", "channels": ["ticker"],
        "doc": "https://www.bitunix.com/api-docs/futures/websocket/public/Ticker%20Channel.html",
        "push": {"ch": "Channel name", "symbol": "Product ID", "ts": "int64", "data.s": "Symbol", "data.o": "Opening price",
                 "data.h": "Highest price", "data.l": "Lowest price", "data.la": "Last price", "data.b": "Trading volume of the coin",
                 "data.q": "Trading volume of quote currency", "data.r": "24-hour fluctuations"},
    },
    "price": {
        "url": "public", "channels": ["price"],
        "doc": "https://www.bitunix.com/api-docs/futures/websocket/public/MarketPrice%20Channel.html",
        "push": {"ch": "Channel name", "symbol": "Product ID", "ts": "int64", "data.mp": "Market (mark) price", "data.ip": "Index price",
                 "data.fr": "Funding rate", "data.ft": "Funding rate settlement time", "data.nft": "Next funding rate settlement time"},
    },
    "kline": {
        "url": "public", "channels": "<market|mark>_kline_<1min|3min|5min|15min|30min|60min|2h|4h|6h|8h|12h|1day|3day|1week|1month>",
        "doc": "https://www.bitunix.com/api-docs/futures/websocket/public/kline%20channel.html",
        "push": {"ch": "Channel name", "symbol": "Product ID", "ts": "int64", "data.o": "Opening price", "data.h": "Highest price",
                 "data.l": "Lowest price", "data.c": "Closing price", "data.b": "Trading volume of the coin", "data.q": "Trading volume of quote currency"},
        "notes": "Pushed every 500 ms; snapshot after subscribe. To switch interval, unsubscribe the old kline channel first.",
    },
    "trade": {
        "url": "public", "channels": ["trade"],
        "doc": "https://www.bitunix.com/api-docs/futures/websocket/public/Trade%20Channel.html",
        "push": {"ch": "Channel", "symbol": "Symbol", "ts": "Time stamp", "data[].p": "Filled price", "data[].v": "Filled amount",
                 "data[].s": "Filled side, sell/buy", "data[].t": "Time stamp"},
    },
    # ------------------------------------------------ private
    "balance": {
        "url": "private", "channels": ["balance"],
        "doc": "https://www.bitunix.com/api-docs/futures/websocket/private/Balance%20Channel.html",
        "push": {"ch": "balance", "ts": "Int64", "data.coin": "coin", "data.available": "available",
                 "data.frozen": "frozen = isolationFrozen + crossFrozen", "data.isolationFrozen": "isolated frozen",
                 "data.crossFrozen": "cross frozen", "data.margin": "Margin", "data.isolationMargin": "isolated margin",
                 "data.crossMargin": "cross margin", "data.expMoney": "Experience Money"},
    },
    "position": {
        "url": "private", "channels": ["position"],
        "doc": "https://www.bitunix.com/api-docs/futures/websocket/private/Position%20Channel.html",
        "push": {"ch": "position", "ts": "Int64", "data.event": "OPEN/UPDATE/CLOSE", "data.positionId": "position Id",
                 "data.marginMode": "ISOLATION/CROSS", "data.positionMode": "ONE_WAY/HEDGE", "data.side": "SHORT/LONG",
                 "data.leverage": "Leverage", "data.margin": "margin", "data.ctime": "Create time", "data.qty": "Open position size",
                 "data.symbol": "Symbol", "data.realizedPNL": "Realized PnL (excl. funding & fee)", "data.unrealizedPNL": "Unrealized PnL",
                 "data.funding": "total funding fee", "data.fee": "Deducted transaction fees"},
    },
    "order": {
        "url": "private", "channels": ["order"],
        "doc": "https://www.bitunix.com/api-docs/futures/websocket/private/Order%20Channel.html",
        "push": {"ch": "order", "ts": "gateway send time ms", "data.event": "CREATE/UPDATE/CLOSE", "data.orderId": "order Id",
                 "data.symbol": "Symbol", "data.positionType": "ISOLATION/CROSS", "data.positionMode": "ONE_WAY/HEDGE",
                 "data.side": "BUY/SELL", "data.effect": "IOC/FOK/GTC/POST_ONLY", "data.type": "LIMIT/MARKET",
                 "data.qty": "Amount (base coin)", "data.price": "Price", "data.ctime": "ISO-8601 ns string", "data.mtime": "ISO-8601 ns string",
                 "data.leverage": "Leverage", "data.orderStatus": "INIT/NEW/PART_FILLED/CANCELED/FILLED/PART_FILLED_CANCELED",
                 "data.fee": "fee", "data.averagePrice": "Average price", "data.dealAmount": "Deal amount", "data.clientId": "Client ID",
                 "data.tpStopType": "MARK_PRICE/LAST_PRICE", "data.tpPrice": "TP trigger", "data.tpOrderType": "LIMIT/MARKET",
                 "data.tpOrderPrice": "TP order price", "data.slStopType": "MARK_PRICE/LAST_PRICE", "data.slPrice": "SL trigger",
                 "data.slOrderType": "LIMIT/MARKET", "data.slOrderPrice": "SL order price"},
    },
    "tpsl": {
        "url": "private", "channels": ["tpsl"],
        "doc": "https://www.bitunix.com/api-docs/futures/websocket/private/Tp%20Sl%20Channel.html",
        "push": {"ch": "tpsl", "ts": "gateway send time ms", "data.event": "CREATE/UPDATE/CLOSE", "data.positionId": "position Id",
                 "data.orderId": "order Id", "data.symbol": "Symbol", "data.leverage": "Leverage", "data.side": "BUY/SELL",
                 "data.positionMode": "ONE_WAY/HEDGE", "data.status": "NEW/CANCELED/SYSTEM_CANCELED/FILLED/FAILED",
                 "data.ctime": "ISO-8601 ns string", "data.type": "LIMIT/MARKET", "data.tpQty": "TP qty (omitted if unused)",
                 "data.slQty": "SL qty (omitted if unused)", "data.tpStopType": "MARK_PRICE/LAST_PRICE", "data.tpPrice": "TP trigger",
                 "data.tpOrderType": "LIMIT/MARKET", "data.tpOrderPrice": "TP order price", "data.slStopType": "MARK_PRICE/LAST_PRICE",
                 "data.slPrice": "SL trigger", "data.slOrderType": "LIMIT/MARKET", "data.slOrderPrice": "SL order price"},
        "notes": "Pushed automatically after login. data is always an object. Read event together with status; CLOSE+FILLED = child order placed, not filled.",
    },
}
