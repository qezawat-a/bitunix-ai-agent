# Parameter coverage

Generated from the official docs by `tools/gen_parameters_md.py`. Every row is enforced by `tests/test_spec_vs_docs.py`.

REST endpoints: **35** · WebSocket channels: **10** · error codes: **79**

## TP_SL

### `cancel_tpsl_order` — POST `/api/v1/futures/tpsl/cancel_order`
10 req/s per uid · signed · tool `api_cancel_tpsl_order` · [docs](https://www.bitunix.com/api-docs/futures/tp_sl/cancel_tp_sl_order.html)

| Parameter | Type | Required | Enum | Description |
|---|---|---|---|---|
| `symbol` | string | yes |  | coin Pair |
| `orderId` | string | yes |  | TP/SL Order ID |

Response: `orderId`

> Confirm on the tpsl WebSocket channel.

### `get_history_tpsl_orders` — GET `/api/v1/futures/tpsl/get_history_orders`
10 req/s per uid · signed · tool `api_get_history_tpsl_orders` · [docs](https://www.bitunix.com/api-docs/futures/tp_sl/get_history_tp_sl_order.html)

| Parameter | Type | Required | Enum | Description |
|---|---|---|---|---|
| `symbol` | string | no |  | Trading pair |
| `side` | int | no |  | order side |
| `positionMode` | int | no |  | order position mode |
| `startTime` | int | no |  | Start timestampUnix timestamp in milliseconds format, e.g. 1597026383085 |
| `endTime` | int | no |  | Start timestampUnix timestamp in milliseconds format, e.g. 1597026683085 |
| `skip` | int | no |  | skip order count default: 0 |
| `limit` | int | no |  | Number of queries: Maximum: 100, default: 10 |

Rules: `{'rule': 'max', 'field': 'limit', 'value': 100}`

Response: `orderList`, `>id`, `>positionId`, `>symbol`, `>base`, `>quote`, `>tpPrice`, `>tpStopType`, `>slPrice`, `>slStopType`, `>tpOrderType`, `>tpOrderPrice`, `>slOrderType`, `>slOrderPrice`, `>tpQty`, `>slQty`, `>status`, `>ctime`, `>triggerTime`, `total`

> The official response example is copy-pasted from positions; the response table (orderList/total) is authoritative.

### `get_pending_tpsl_orders` — GET `/api/v1/futures/tpsl/get_pending_orders`
10 req/s per uid · signed · tool `api_get_pending_tpsl_orders` · [docs](https://www.bitunix.com/api-docs/futures/tp_sl/get_pending_tp_sl_order.html)

| Parameter | Type | Required | Enum | Description |
|---|---|---|---|---|
| `symbol` | string | no |  | Trading pair |
| `positionId` | string | no |  | position id |
| `side` | int | no |  | order side |
| `positionMode` | int | no |  | order position mode |
| `skip` | int | no |  | skip order count default: 0 |
| `limit` | int | no |  | Number of queries: Maximum: 100, default: 10 |

Rules: `{'rule': 'max', 'field': 'limit', 'value': 100}`

Response: `id`, `positionId`, `symbol`, `base`, `quote`, `tpPrice`, `tpStopType`, `slPrice`, `slStopType`, `tpOrderType`, `tpOrderPrice`, `slOrderType`, `slOrderPrice`, `tpQty`, `slQty`

> side and positionMode are int32 per the official table; their numeric codes are not documented.

### `modify_position_tpsl_order` — POST `/api/v1/futures/tpsl/position/modify_order`
20 req/s per uid · signed · tool `api_modify_position_tpsl_order` · [docs](https://www.bitunix.com/api-docs/futures/tp_sl/modify_position_tp_sl_order.html)

| Parameter | Type | Required | Enum | Description |
|---|---|---|---|---|
| `symbol` | string | yes |  | Trading pair |
| `positionId` | string | yes |  | Position ID associated with take-profit and stop-loss |
| `tpPrice` | string | no |  | Take-profit trigger priceAt least one of tpPrice or slPrice is required. |
| `tpStopType` | string | no | MARK_PRICE, LAST_PRICE | Take-profit trigger typeLAST_PRICEMARK_PRICE Default is market price. |
| `slPrice` | string | no |  | Stop-loss trigger priceAt least one of tpPrice or slPrice is required. |
| `slStopType` | string | no | MARK_PRICE, LAST_PRICE | Stop-loss trigger typeLAST_PRICEMARK_PRICE Default is market price. |

Rules: `{'rule': 'any_of', 'fields': ['tpPrice', 'slPrice']}`

Response: `orderId`


### `modify_tpsl_order` — POST `/api/v1/futures/tpsl/modify_order`
20 req/s per uid · signed · tool `api_modify_tpsl_order` · [docs](https://www.bitunix.com/api-docs/futures/tp_sl/modify_tp_sl_order.html)

| Parameter | Type | Required | Enum | Description |
|---|---|---|---|---|
| `orderId` | string | yes |  | TP/SL Order ID |
| `tpPrice` | string | no |  | Take-profit trigger priceAt least one of tpPrice or slPrice is required. |
| `tpStopType` | string | no | MARK_PRICE, LAST_PRICE | Take-profit trigger typeLAST_PRICEMARK_PRICE Default is market price. |
| `slPrice` | string | no |  | Stop-loss trigger priceAt least one of tpPrice or slPrice is required. |
| `slStopType` | string | no | MARK_PRICE, LAST_PRICE | Stop-loss trigger typeLAST_PRICEMARK_PRICE Default is market price. |
| `tpOrderType` | string | no | LIMIT, MARKET | Take-profit order typeLIMITMARKET Default is market. |
| `tpOrderPrice` | string | no |  | Take-profit order price |
| `slOrderType` | string | no | LIMIT, MARKET | Stop-loss order typeLIMITMARKET Default is market. |
| `slOrderPrice` | string | no |  | Stop-loss order price |
| `tpQty` | string | no |  | Take-profit order quantity(base coin)At least one of tpQty or slQty is required. |
| `slQty` | string | no |  | Stop-loss order quantity(base coin)At least one of tpQty or slQty is required. |

Rules: `{'rule': 'any_of', 'fields': ['tpPrice', 'slPrice']}`; `{'rule': 'any_of', 'fields': ['tpQty', 'slQty']}`; `{'rule': 'required_if', 'field': 'tpOrderPrice', 'when': {'tpOrderType': 'LIMIT'}}`; `{'rule': 'required_if', 'field': 'slOrderPrice', 'when': {'slOrderType': 'LIMIT'}}`

Response: `orderId`


### `place_position_tpsl_order` — POST `/api/v1/futures/tpsl/position/place_order`
20 req/s per uid · signed · tool `api_place_position_tpsl_order` · [docs](https://www.bitunix.com/api-docs/futures/tp_sl/place_position_tp_sl_order.html)

| Parameter | Type | Required | Enum | Description |
|---|---|---|---|---|
| `symbol` | string | yes |  | Trading pair |
| `positionId` | string | yes |  | Position ID associated with take-profit and stop-loss |
| `tpPrice` | string | no |  | Take-profit trigger priceAt least one of tpPrice or slPrice is required. |
| `tpStopType` | string | no | MARK_PRICE, LAST_PRICE | Take-profit trigger typeLAST_PRICEMARK_PRICE Default is market price. |
| `slPrice` | string | no |  | Stop-loss trigger priceAt least one of tpPrice or slPrice is required. |
| `slStopType` | string | no | MARK_PRICE, LAST_PRICE | Stop-loss trigger typeLAST_PRICEMARK_PRICE Default is market price. |

Rules: `{'rule': 'any_of', 'fields': ['tpPrice', 'slPrice']}`

Response: `orderId`

> Closes the whole position at market when triggered. Only one position TP/SL per position.

### `place_tpsl_order` — POST `/api/v1/futures/tpsl/place_order`
20 req/s per uid · signed · tool `api_place_tpsl_order` · [docs](https://www.bitunix.com/api-docs/futures/tp_sl/place_tp_sl_order.html)

| Parameter | Type | Required | Enum | Description |
|---|---|---|---|---|
| `symbol` | string | yes |  | Trading pair |
| `positionId` | string | yes |  | Position ID associated with take-profit and stop-loss |
| `tpPrice` | string | no |  | Take-profit trigger priceAt least one of tpPrice or slPrice is required. |
| `tpStopType` | string | no | MARK_PRICE, LAST_PRICE | Take-profit trigger typeLAST_PRICEMARK_PRICE Default is market price. |
| `slPrice` | string | no |  | Stop-loss trigger priceAt least one of tpPrice or slPrice is required. |
| `slStopType` | string | no | MARK_PRICE, LAST_PRICE | Stop-loss trigger typeLAST_PRICEMARK_PRICE Default is market price. |
| `tpOrderType` | string | no | LIMIT, MARKET | Take-profit order typeLIMITMARKET Default is market. |
| `tpOrderPrice` | string | no |  | Take-profit order price |
| `slOrderType` | string | no | LIMIT, MARKET | Stop-loss order typeLIMITMARKET Default is market. |
| `slOrderPrice` | string | no |  | Stop-loss order price |
| `tpQty` | string | no |  | Take-profit order quantity(base coin)At least one of tpQty or slQty is required. |
| `slQty` | string | no |  | Stop-loss order quantity(base coin)At least one of tpQty or slQty is required. |

Rules: `{'rule': 'any_of', 'fields': ['tpPrice', 'slPrice']}`; `{'rule': 'any_of', 'fields': ['tpQty', 'slQty']}`; `{'rule': 'required_if', 'field': 'tpOrderPrice', 'when': {'tpOrderType': 'LIMIT'}}`; `{'rule': 'required_if', 'field': 'slOrderPrice', 'when': {'slOrderType': 'LIMIT'}}`

Response: `orderId`


## ACCOUNT

### `get_leverage_margin_mode` — GET `/api/v1/futures/account/get_leverage_margin_mode`
10 req/s per uid · signed · tool `api_get_leverage_margin_mode` · [docs](https://www.bitunix.com/api-docs/futures/account/get_leverage_and_margin_mode.html)

| Parameter | Type | Required | Enum | Description |
|---|---|---|---|---|
| `symbol` | string | yes |  | Trading pair |
| `marginCoin` | string | yes |  | Margin coin |

Response: `symbol`, `marginCoin`, `leverage`, `marginMode`


### `adjust_position_margin` — POST `/api/v1/futures/account/adjust_position_margin`
5 req/s per uid · signed · tool `api_adjust_position_margin` · [docs](https://www.bitunix.com/api-docs/futures/account/adjust_position_margin.html)

| Parameter | Type | Required | Enum | Description |
|---|---|---|---|---|
| `symbol` | string | yes |  | Trading pair |
| `marginCoin` | string | yes |  | Margin coin |
| `amount` | string | yes |  | Margin amount, positive means increase, and negative means decrease |
| `side` | string | no | LONG, SHORT | Position side LONG SHORT Either side or positionId required |
| `positionId` | string | no |  | Position id Either side or positionId required |

Rules: `{'rule': 'one_of', 'fields': ['side', 'positionId']}`

> Isolated margin mode only. amount > 0 adds margin, amount < 0 removes margin.

### `change_leverage` — POST `/api/v1/futures/account/change_leverage`
10 req/s per uid · signed · tool `api_change_leverage` · [docs](https://www.bitunix.com/api-docs/futures/account/change_leverage.html)

| Parameter | Type | Required | Enum | Description |
|---|---|---|---|---|
| `marginCoin` | string | yes |  | Margin coin |
| `symbol` | string | yes |  | Trading pair |
| `leverage` | int | yes |  | Leverage |

Response: `marginCoin`, `symbol`, `leverage`


### `change_margin_mode` — POST `/api/v1/futures/account/change_margin_mode`
10 req/s per uid · signed · tool `api_change_margin_mode` · [docs](https://www.bitunix.com/api-docs/futures/account/change_margin_mode.html)

| Parameter | Type | Required | Enum | Description |
|---|---|---|---|---|
| `marginMode` | string | yes | ISOLATION, CROSS | Margin Mode ISOLATION CROSS |
| `symbol` | string | yes |  | Trading pair |
| `marginCoin` | string | yes |  | Margin coin |

Response: `marginMode`, `symbol`, `marginCoin`

> Fails while the user has an open position or order on the symbol (error 20006).

### `change_position_mode` — POST `/api/v1/futures/account/change_position_mode`
10 req/s per uid · signed · tool `api_change_position_mode` · [docs](https://www.bitunix.com/api-docs/futures/account/change_position_mode.html)

| Parameter | Type | Required | Enum | Description |
|---|---|---|---|---|
| `positionMode` | string | yes | ONE_WAY, HEDGE | Position Mode ONE_WAY HEDGE |

Response: `positionMode`

> Fails while any position or order exists (error 20009 / 200014).

### `get_trading_settings` — GET `/api/v1/futures/account/trading_settings`
20 req/s per uid · signed · tool `api_get_trading_settings` · [docs](https://www.bitunix.com/api-docs/futures/account/get_trading_settings.html)

| Parameter | Type | Required | Enum | Description |
|---|---|---|---|---|
| `symbols` | string | no |  | Trading pair symbols, comma-separated (e.g. BTCUSDT,ETHUSDT). Case-insensitive. Max 50 symbols. If omitted, returns all user trading settings. |

Rules: `{'rule': 'max_csv', 'field': 'symbols', 'value': 50}`

Response: `code`, `msg`, `data`, `>symbol`, `>marginCoin`, `>marginMode`, `>leverage`, `>longLeverage`, `>shortLeverage`


### `get_single_account` — GET `/api/v1/futures/account`
10 req/s per uid · signed · tool `api_get_single_account` · [docs](https://www.bitunix.com/api-docs/futures/account/get_single_account.html)

| Parameter | Type | Required | Enum | Description |
|---|---|---|---|---|
| `marginCoin` | string | yes |  | Margin coin |

Response: `marginCoin`, `available`, `frozen`, `margin`, `transfer`, `positionMode`, `crossUnrealizedPNL`, `isolationUnrealizedPNL`, `bonus`


## MARKET

### `get_kline` — GET `/api/v1/futures/market/kline`
10 req/s per ip · public · tool `api_get_kline` · [docs](https://www.bitunix.com/api-docs/futures/market/get_kline.html)

| Parameter | Type | Required | Enum | Description |
|---|---|---|---|---|
| `symbol` | string | yes |  | Trading pair, based on the symbolName, i.e. BTCUSDT |
| `endTime` | int | no |  | Exclusive end bound. Query limit klines before this time. Unix timestamp in milliseconds, e.g. 1672410000000. Best practice: align to the interval open time. If |
| `interval` | string | yes | 1m, 5m, 15m, 30m, 1h, 2h, 4h, 6h, 8h, 12h, 1d, 3d, 1w, 1M | kline interval such as 1m 5m 15m 30m 1h 2h 4h 6h 8h 12h 1d 3d 1w 1M |
| `limit` | int | no |  | Number of klines to return, counting backward from endTime. Default: 100, maximum: 200 |
| `type` | string | no | LAST_PRICE, MARK_PRICE | Kline type, values: LAST_PRICE, MARK_PRICE; default: LAST_PRICE |

Rules: `{'rule': 'max', 'field': 'limit', 'value': 200}`

Response: `open`, `high`, `low`, `close`, `quoteVol`, `baseVol`, `time`

> startTime was removed (changelog 2026-10-27). Query = endTime + limit counting backward; endTime candle excluded.
> time is the candle OPEN time; only closed candles are returned (changelog 2026-10-08).
> Live API returns newest first and time as a string; the client normalizes to oldest-first ints.

### `get_funding_rate` — GET `/api/v1/futures/market/funding_rate`
10 req/s per ip · public · tool `api_get_funding_rate` · [docs](https://www.bitunix.com/api-docs/futures/market/get_funding_rate.html)

| Parameter | Type | Required | Enum | Description |
|---|---|---|---|---|
| `symbol` | string | yes |  | Trading pair, based on the symbolName, i.e. BTCUSDT |

Response: `symbol`, `markPrice`, `lastPrice`, `indexPrice`, `fundingRate`, `nextFundingTime`, `fundingInterval`, `maxFundingRate`, `minFundingRate`

> Live API returns a single object (the doc example shows a list); normalized to an object.

### `get_funding_rate_history` — GET `/api/v1/futures/market/get_funding_rate_history`
10 req/s per ip · public · tool `api_get_funding_rate_history` · [docs](https://www.bitunix.com/api-docs/futures/market/get_funding_rate_history.html)

| Parameter | Type | Required | Enum | Description |
|---|---|---|---|---|
| `symbol` | string | yes |  | Trading pair, based on the symbolName, i.e. BTCUSDT |
| `startTime` | int | no |  | Start timestamp(Funding settle time)Unix timestamp in milliseconds format, e.g. 1597026383085 |
| `endTime` | int | no |  | End timestamp(Funding settle time)Unix timestamp in milliseconds format, e.g. 1597026383085 |
| `limit` | int | no |  | Default: 100,Maximum: 200 |

Rules: `{'rule': 'max', 'field': 'limit', 'value': 200}`

Response: `markPrice`, `fundingRate`, `fundingTime`


### `get_funding_rate_batch` — GET `/api/v1/futures/market/funding_rate/batch`
10 req/s per ip · public · tool `api_get_funding_rate_batch` · [docs](https://www.bitunix.com/api-docs/futures/market/get_funding_rate_batch.html)

Response: `symbol`, `markPrice`, `lastPrice`, `indexPrice`, `fundingRate`, `nextFundingTime`, `fundingInterval`, `maxFundingRate`, `minFundingRate`


### `get_depth` — GET `/api/v1/futures/market/depth`
10 req/s per ip · public · tool `api_get_depth` · [docs](https://www.bitunix.com/api-docs/futures/market/get_depth.html)

| Parameter | Type | Required | Enum | Description |
|---|---|---|---|---|
| `symbol` | string | yes |  | Trading pair, based on the symbolName, i.e. BTCUSDT |
| `limit` | string | no | 1, 5, 15, 50, max | Fixed gear enumeration value: 1/5/15/50/max, passing max returns the maximum gear of the trading pairWhen the actual depth does not meet the limit, return accor |

Response: `asks.index[0]`, `asks.index[1]`, `bids.index[0]`, `bids.index[1]`


### `get_trading_pairs` — GET `/api/v1/futures/market/trading_pairs`
10 req/s per ip · public · tool `api_get_trading_pairs` · [docs](https://www.bitunix.com/api-docs/futures/market/get_trading_pairs.html)

| Parameter | Type | Required | Enum | Description |
|---|---|---|---|---|
| `symbols` | string | no |  | Trading pairs, based on the symbolName, i.e. BTCUSDT,ETHUSDT,XRPUSDT |

Response: `symbol`, `base`, `quote`, `minTradeVolume`, `minBuyPriceOffset`, `maxSellPriceOffset`, `maxLimitOrderVolume`, `maxMarketOrderVolume`, `basePrecision`, `quotePrecision`, `maxLeverage`, `minLeverage`, `defaultLeverage`, `defaultMarginMode`, `priceProtectScope`, `symbolStatus`, `isApiSupported`, `maxFundingRate`, `minFundingRate`, `launchTime`, `delistTime`

> defaultMarginMode is returned as a code string ('1'/'2') by the live API.

### `get_tickers` — GET `/api/v1/futures/market/tickers`
10 req/s per ip · public · tool `api_get_tickers` · [docs](https://www.bitunix.com/api-docs/futures/market/get_tickers.html)

| Parameter | Type | Required | Enum | Description |
|---|---|---|---|---|
| `symbols` | string | no |  | Trading pairs, based on the symbolName, i.e. BTCUSDT,ETHUSDT,XRPUSDT |

Response: `symbol`, `markPrice`, `lastPrice`, `open`, `last`, `quoteVol`, `baseVol`, `high`, `low`


## POSITION

### `get_position_tiers` — GET `/api/v1/futures/position/get_position_tiers`
10 req/s per ip · public · tool `api_get_position_tiers` · [docs](https://www.bitunix.com/api-docs/futures/position/get_position_tiers.html)

| Parameter | Type | Required | Enum | Description |
|---|---|---|---|---|
| `symbol` | string | yes |  | Trading pair |

Response: `symbol`, `level`, `startValue`, `endValue`, `leverage`, `maintenanceMarginRate`


### `get_pending_positions` — GET `/api/v1/futures/position/get_pending_positions`
10 req/s per uid · signed · tool `api_get_pending_positions` · [docs](https://www.bitunix.com/api-docs/futures/position/get_pending_positions.html)

| Parameter | Type | Required | Enum | Description |
|---|---|---|---|---|
| `symbol` | string | no |  | Trading pair |
| `positionId` | string | no |  | position id |
| `subAccountId` | int | no |  | With subAccountUid: returns positions for that sub-account only. Without subAccountUid:returns positions for the main account and all sub-accounts the current A |
| `includeSubAccounts` | bool | no |  | enable or disable sub-account query |

Response: `positionId`, `symbol`, `qty`, `entryValue`, `side`, `marginMode`, `positionMode`, `leverage`, `fee`, `funding`, `realizedPNL`, `margin`, `unrealizedPNL`, `liqPrice`, `marginRate`, `avgOpenPrice`, `ctime`, `mtime`, `subAccountId`


### `get_history_positions` — GET `/api/v1/futures/position/get_history_positions`
10 req/s per uid · signed · tool `api_get_history_positions` · [docs](https://www.bitunix.com/api-docs/futures/position/get_history_positions.html)

| Parameter | Type | Required | Enum | Description |
|---|---|---|---|---|
| `symbol` | string | no |  | Trading pair |
| `positionId` | string | no |  | position id |
| `startTime` | int | no |  | Start timestamp(position create time)Unix timestamp in milliseconds format, e.g. 1597026383085 |
| `endTime` | int | no |  | Start timestamp(position create time)Unix timestamp in milliseconds format, e.g. 1597026683085 |
| `skip` | int | no |  | skip order count default: 0 |
| `limit` | int | no |  | Number of queries: Maximum: 100, default: 10 |
| `subAccountId` | int | no |  | With subAccountUid: returns historical positions for that sub-account only.Without subAccountUid: returns historical positions for the main account. |

Rules: `{'rule': 'max', 'field': 'limit', 'value': 100}`

Response: `positionList`, `>positionId`, `>symbol`, `>maxQty`, `>entryPrice`, `>closePrice`, `>liqQty`, `>side`, `>marginMode`, `>positionMode`, `>leverage`, `>fee`, `>funding`, `>realizedPNL`, `>liqPrice`, `>ctime`, `>mtime`, `>subAccountId`, `total`


## TRADE

### `flash_close_position` — POST `/api/v1/futures/trade/flash_close_position`
5 req/s per uid · signed · tool `api_flash_close_position` · [docs](https://www.bitunix.com/api-docs/futures/trade/flash_close_position.html)

| Parameter | Type | Required | Enum | Description |
|---|---|---|---|---|
| `positionId` | string | yes |  | position id |

Response: `positionId`


### `close_all_position` — POST `/api/v1/futures/trade/close_all_position`
1 req/s per uid · signed · tool `api_close_all_position` · [docs](https://www.bitunix.com/api-docs/futures/trade/close_all_position.html)

| Parameter | Type | Required | Enum | Description |
|---|---|---|---|---|
| `symbol` | string | no |  | Trading pair |


### `cancel_all_orders` — POST `/api/v1/futures/trade/cancel_all_orders`
20 req/s per uid · signed · tool `api_cancel_all_orders` · [docs](https://www.bitunix.com/api-docs/futures/trade/cancel_all_orders.html)

| Parameter | Type | Required | Enum | Description |
|---|---|---|---|---|
| `symbol` | string | no |  | Trading pair |

Response: `successList`, `>id`, `>clientId`, `failureList`, `>id`, `>clientId`, `>errorMsg`, `>errorCode`

> Response list items use 'orderId' in the official example although the table says 'id'. Both are handled.

### `cancel_orders` — POST `/api/v1/futures/trade/cancel_orders`
10 req/s per uid · signed · tool `api_cancel_orders` · [docs](https://www.bitunix.com/api-docs/futures/trade/cancel_orders.html)

| Parameter | Type | Required | Enum | Description |
|---|---|---|---|---|
| `symbol` | string | yes |  | Trading pair |
| `orderList` | list | yes |  | order parameter list |
| `orderList.orderId` | string | no |  | Order IDEither orderId or clientId is required. If both are entered, orderId prevails. |
| `orderList.clientId` | string | no |  | Customize order IDEither orderId or clientId is required. If both are entered, orderId prevails. |

Rules: `{'rule': 'one_of', 'fields': ['orderList.orderId', 'orderList.clientId']}`

Response: `successList`, `>id`, `>clientId`, `failureList`, `>id`, `>clientId`, `>errorMsg`, `>errorCode`

> If both orderId and clientId are given, orderId prevails.
> Confirm cancellation on the order WebSocket channel.

### `batch_order` — POST `/api/v1/futures/trade/batch_order`
2 req/s per uid · signed · tool `api_batch_order` · [docs](https://www.bitunix.com/api-docs/futures/trade/batch_order.html)

| Parameter | Type | Required | Enum | Description |
|---|---|---|---|---|
| `symbol` | string | yes |  | Trading pair |
| `orderList` | list | yes |  | Order list, maximum length: 5 |
| `orderList.qty` | string | yes |  | Amount (base coin) |
| `orderList.price` | string | no |  | Price of the order.Required if the order type is LIMIT |
| `orderList.side` | string | yes | BUY, SELL | Order directionbuy: BUYsell: SELL |
| `orderList.tradeSide` | string | hedge-mode | OPEN, CLOSE | DirectionOnly required in hedge-modeOpen and Close Notes:For open long, side fill in"BUY"; tradeSide should be "OPEN"For open short, side fill in "SELL"; tradeS |
| `orderList.positionId` | string | no |  | Position IDOnly required when "tradeSide" is "CLOSE" |
| `orderList.orderType` | string | yes | LIMIT, MARKET | Order typeLIMIT: limit ordersMARKET: market orders |
| `orderList.effect` | string | no | IOC, FOK, GTC, POST_ONLY | Order expiration date.Required if the orderType is limitIOC: Immediate or cancelFOK: Fill or killGTC: Good till canceled(default value)POST_ONLY: POST only |
| `orderList.clientId` | string | no |  | Customize order ID |
| `orderList.reduceOnly` | bool | no |  | Whether or not to just reduce the position |
| `orderList.tpPrice` | string | no |  | take profit trigger price |
| `orderList.tpStopType` | string | no | MARK_PRICE, LAST_PRICE | take profit trigger type MARK_PRICE LAST_PRICE |
| `orderList.tpOrderType` | string | no | LIMIT, MARKET | take profit trigger place order type LIMIT MARKET |
| `orderList.tpOrderPrice` | string | no |  | take profit trigger place order price LIMIT MARKET required if tpOrderType is LIMIT |
| `orderList.slPrice` | string | no |  | stop loss trigger price |
| `orderList.slStopType` | string | no | MARK_PRICE, LAST_PRICE | stop loss trigger type MARK_PRICE LAST_PRICE |
| `orderList.slOrderType` | string | no | LIMIT, MARKET | stop loss trigger place order type LIMIT MARKET |
| `orderList.slOrderPrice` | string | no |  | stop loss trigger place order price LIMIT MARKET required if slOrderType is LIMIT |

Rules: `{'rule': 'required_if', 'field': 'orderList.price', 'when': {'orderList.orderType': 'LIMIT'}}`; `{'rule': 'required_if', 'field': 'orderList.tpOrderPrice', 'when': {'orderList.tpOrderType': 'LIMIT'}}`; `{'rule': 'required_if', 'field': 'orderList.slOrderPrice', 'when': {'orderList.slOrderType': 'LIMIT'}}`; `{'rule': 'hedge_required', 'field': 'orderList.tradeSide'}`; `{'rule': 'required_if', 'field': 'orderList.positionId', 'when': {'orderList.tradeSide': 'CLOSE'}}`; `{'rule': 'max_len', 'field': 'orderList', 'value': 5}`

Response: `successList`, `>id`, `>clientId`, `failureList`, `>clientId`, `>errorMsg`, `>errorCode`

> orderList maximum length is 5 (error 20016 when exceeded).

### `get_pending_orders` — GET `/api/v1/futures/trade/get_pending_orders`
10 req/s per uid · signed · tool `api_get_pending_orders` · [docs](https://www.bitunix.com/api-docs/futures/trade/get_pending_orders.html)

| Parameter | Type | Required | Enum | Description |
|---|---|---|---|---|
| `symbol` | string | no |  | Trading pair |
| `orderId` | string | no |  | order id |
| `clientId` | string | no |  | client id |
| `status` | string | no | NEW, PART_FILLED | Order status NEW or PART_FILLED |
| `startTime` | int | no |  | Start timestampUnix timestamp in milliseconds format, e.g. 1597026383085 |
| `endTime` | int | no |  | Start timestampUnix timestamp in milliseconds format, e.g. 1597026683085 |
| `skip` | int | no |  | skip order count default: 0 |
| `limit` | int | no |  | Number of queries: Maximum: 100, default: 10 |

Rules: `{'rule': 'max', 'field': 'limit', 'value': 100}`

Response: `orderList`, `>orderId`, `>symbol`, `>qty`, `>tradeQty`, `>positionMode`, `>marginMode`, `>leverage`, `>price`, `>side`, `>orderType`, `>effect`, `>clientId`, `>reduceOnly`, `>status`, `>fee`, `>realizedPNL`, `>tpPrice`, `>tpStopType`, `>tpOrderType`, `>tpOrderPrice`, `>slPrice`, `>slStopType`, `>slOrderType`, `>slOrderPrice`, `>ctime`, `>mtime`, `total`


### `get_order_detail` — GET `/api/v1/futures/trade/get_order_detail`
10 req/s per uid · signed · tool `api_get_order_detail` · [docs](https://www.bitunix.com/api-docs/futures/trade/get_order_detail.html)

| Parameter | Type | Required | Enum | Description |
|---|---|---|---|---|
| `orderId` | string | no |  | order idAt least one of orderId or clientId is required. |
| `clientId` | string | no |  | client idAt least one of orderId or clientId is required. |

Rules: `{'rule': 'one_of', 'fields': ['orderId', 'clientId']}`

Response: `orderId`, `symbol`, `qty`, `tradeQty`, `positionMode`, `marginMode`, `leverage`, `price`, `side`, `orderType`, `effect`, `clientId`, `reduceOnly`, `status`, `fee`, `realizedPNL`, `tpPrice`, `tpStopType`, `tpOrderType`, `tpOrderPrice`, `slPrice`, `slStopType`, `slOrderType`, `slOrderPrice`, `ctime`, `mtime`


### `get_history_orders` — GET `/api/v1/futures/trade/get_history_orders`
10 req/s per uid · signed · tool `api_get_history_orders` · [docs](https://www.bitunix.com/api-docs/futures/trade/get_history_orders.html)

| Parameter | Type | Required | Enum | Description |
|---|---|---|---|---|
| `symbol` | string | no |  | Trading pair |
| `orderId` | string | no |  | order id |
| `clientId` | string | no |  | client id |
| `status` | string | no | FILLED, CANCELED, PART_FILLED_CANCELED, EXPIRED | Order statusFILLED CANCELEDPART_FILLED_CANCELEDEXPIRED |
| `type` | string | no | LIMIT, MARKET | Order typeLIMIT MARKET default all |
| `startTime` | int | no |  | Start timestampUnix timestamp in milliseconds format, e.g. 1597026383085 |
| `endTime` | int | no |  | Start timestampUnix timestamp in milliseconds format, e.g. 1597026683085 |
| `skip` | int | no |  | skip order count default: 0 |
| `limit` | int | no |  | Number of queries: Maximum: 100, default: 10 |
| `subAccountId` | int | no |  | With subAccountUid: returns historical orders for that sub-account only.Without subAccountUid: returns historical orders for the main account. |
| `queryCanceled` | bool | no |  | Whether to query canceled orders only. default: falsetrue: return canceled orders only, query range limited to the last 3 daysfalse: exclude canceled orders, qu |

Rules: `{'rule': 'max', 'field': 'limit', 'value': 100}`

Response: `orderList`, `>orderId`, `>symbol`, `>qty`, `>tradeQty`, `>positionMode`, `>marginMode`, `>leverage`, `>price`, `>side`, `>orderType`, `>effect`, `>clientId`, `>reduceOnly`, `>status`, `>fee`, `>realizedPNL`, `>tpPrice`, `>tpStopType`, `>tpOrderType`, `>tpOrderPrice`, `>slPrice`, `>slStopType`, `>slOrderType`, `>slOrderPrice`, `>ctime`, `>mtime`, `>subAccountId`, `total`

> queryCanceled=true returns only canceled orders (last 3 days); false excludes them (last 90 days).

### `get_history_trades` — GET `/api/v1/futures/trade/get_history_trades`
10 req/s per uid · signed · tool `api_get_history_trades` · [docs](https://www.bitunix.com/api-docs/futures/trade/get_history_trades.html)

| Parameter | Type | Required | Enum | Description |
|---|---|---|---|---|
| `symbol` | string | no |  | Trading pair |
| `orderId` | string | no |  | order id |
| `positionId` | string | no |  | position id |
| `startTime` | int | no |  | Start timestampUnix timestamp in milliseconds format, e.g. 1597026383085 |
| `endTime` | int | no |  | Start timestampUnix timestamp in milliseconds format, e.g. 1597026683085 |
| `skip` | int | no |  | skip order count default: 0 |
| `limit` | int | no |  | Number of queries: Maximum: 100, default: 10 |

Rules: `{'rule': 'max', 'field': 'limit', 'value': 100}`

Response: `tradeList`, `>tradeId`, `>orderId`, `>symbol`, `>qty`, `>positionMode`, `>marginMode`, `>leverage`, `>price`, `>side`, `>orderType`, `>effect`, `>clientId`, `>reduceOnly`, `>fee`, `>realizedPNL`, `>ctime`, `>roleType`, `total`


### `place_order` — POST `/api/v1/futures/trade/place_order`
20 req/s per uid · signed · tool `api_place_order` · [docs](https://www.bitunix.com/api-docs/futures/trade/place_order.html)

| Parameter | Type | Required | Enum | Description |
|---|---|---|---|---|
| `symbol` | string | yes |  | Trading pair |
| `qty` | string | yes |  | Amount (base coin) |
| `price` | string | no |  | Price of the order.Required if the order type is LIMIT |
| `side` | string | yes | BUY, SELL | Order directionbuy: BUYsell: SELL |
| `tradeSide` | string | hedge-mode | OPEN, CLOSE | DirectionOnly required in hedge-modeOpen and Close Notes:For open long, side fill in"BUY"; tradeSide should be "OPEN"For open short, side fill in "SELL"; tradeS |
| `positionId` | string | no |  | Position IDOnly required when "tradeSide" is "CLOSE" |
| `orderType` | string | yes | LIMIT, MARKET | Order typeLIMIT: limit ordersMARKET: market orders |
| `effect` | string | no | IOC, FOK, GTC, POST_ONLY | Order expiration date.Required if the orderType is limitIOC: Immediate or cancelFOK: Fill or killGTC: Good till canceled(default value)POST_ONLY: POST only |
| `clientId` | string | no |  | Customize order ID |
| `reduceOnly` | bool | no |  | Whether or not to just reduce the position |
| `tpPrice` | string | no |  | take profit trigger price |
| `tpStopType` | string | no | MARK_PRICE, LAST_PRICE | take profit trigger type MARK_PRICE LAST_PRICE |
| `tpOrderType` | string | no | LIMIT, MARKET | take profit trigger place order type LIMIT MARKET |
| `tpOrderPrice` | string | no |  | take profit trigger place order price LIMIT MARKET required if tpOrderType is LIMIT |
| `slPrice` | string | no |  | stop loss trigger price |
| `slStopType` | string | no | MARK_PRICE, LAST_PRICE | stop loss trigger type MARK_PRICE LAST_PRICE |
| `slOrderType` | string | no | LIMIT, MARKET | stop loss trigger place order type LIMIT MARKET |
| `slOrderPrice` | string | no |  | stop loss trigger place order price LIMIT MARKET required if slOrderType is LIMIT |

Rules: `{'rule': 'required_if', 'field': 'price', 'when': {'orderType': 'LIMIT'}}`; `{'rule': 'required_if', 'field': 'tpOrderPrice', 'when': {'tpOrderType': 'LIMIT'}}`; `{'rule': 'required_if', 'field': 'slOrderPrice', 'when': {'slOrderType': 'LIMIT'}}`; `{'rule': 'hedge_required', 'field': 'tradeSide'}`; `{'rule': 'required_if', 'field': 'positionId', 'when': {'tradeSide': 'CLOSE'}}`

Response: `orderId`, `clientId`

> HEDGE mode: open long = BUY+OPEN, open short = SELL+OPEN, close long = BUY+CLOSE(+positionId), close short = SELL+CLOSE(+positionId) (exactly as the official table states).
> ONE_WAY mode: tradeSide is not needed; close by sending the opposite side with reduceOnly=true.
> Official example uses tpStopType 'MARK'; the table enum is MARK_PRICE/LAST_PRICE. The client sends MARK_PRICE/LAST_PRICE and accepts MARK/LAST as aliases.

### `modify_order` — POST `/api/v1/futures/trade/modify_order`
20 req/s per uid · signed · tool `api_modify_order` · [docs](https://www.bitunix.com/api-docs/futures/trade/modify_order.html)

| Parameter | Type | Required | Enum | Description |
|---|---|---|---|---|
| `orderId` | string | no |  | Order IDEither orderId or clientId is required. If both are entered, orderId prevails. |
| `clientId` | string | no |  | Customize order IDEither orderId or clientId is required. If both are entered, orderId prevails. |
| `qty` | string | yes |  | Amount (base coin) |
| `price` | string | yes |  | Price of the order.Required if the order type is LIMIT |
| `tpPrice` | string | no |  | take profit trigger price |
| `tpStopType` | string | no | MARK_PRICE, LAST_PRICE | take profit trigger type MARK_PRICE LAST_PRICE |
| `tpOrderType` | string | no | LIMIT, MARKET | take profit trigger place order type LIMIT MARKET |
| `tpOrderPrice` | string | no |  | take profit trigger place order price LIMIT MARKET required if tpOrderType is LIMIT |
| `slPrice` | string | no |  | stop loss trigger price |
| `slStopType` | string | no | MARK_PRICE, LAST_PRICE | stop loss trigger type MARK_PRICE LAST_PRICE |
| `slOrderType` | string | no | LIMIT, MARKET | stop loss trigger place order type LIMIT MARKET |
| `slOrderPrice` | string | no |  | stop loss trigger place order price LIMIT MARKET required if slOrderType is LIMIT |
| `symbol` | string | no |  | Trading pair. Not in the official table but present in the official request example; sent only when given. |

Rules: `{'rule': 'one_of', 'fields': ['orderId', 'clientId']}`; `{'rule': 'required_if', 'field': 'tpOrderPrice', 'when': {'tpOrderType': 'LIMIT'}}`; `{'rule': 'required_if', 'field': 'slOrderPrice', 'when': {'slOrderType': 'LIMIT'}}`

Response: `orderId`, `clientId`

> A successful response does not guarantee the modification; confirm on the order WebSocket channel.

## WEBSOCKET

Operations: `ping`, `subscribe`, `unsubscribe`, `login`

### `depth` (public) — depth_books, depth_book1, depth_book5, depth_book15
Push fields: `ch`, `symbol`, `ts`, `data.a`, `data.b`

> depth_books: full snapshot first, then only changed levels; book1/5/15: full levels every push.

### `tickers` (public) — tickers
Push fields: `ch`, `ts`, `data[].s`, `data[].o`, `data[].h`, `data[].l`, `data[].la`, `data[].b`, `data[].q`, `data[].r`, `data[].bd`, `data[].ak`, `data[].bv`, `data[].av`

### `ticker` (public) — ticker
Push fields: `ch`, `symbol`, `ts`, `data.s`, `data.o`, `data.h`, `data.l`, `data.la`, `data.b`, `data.q`, `data.r`

### `price` (public) — price
Push fields: `ch`, `symbol`, `ts`, `data.mp`, `data.ip`, `data.fr`, `data.ft`, `data.nft`

### `kline` (public) — <market|mark>_kline_<1min|3min|5min|15min|30min|60min|2h|4h|6h|8h|12h|1day|3day|1week|1month>
Push fields: `ch`, `symbol`, `ts`, `data.o`, `data.h`, `data.l`, `data.c`, `data.b`, `data.q`

> Pushed every 500 ms; snapshot after subscribe. To switch interval, unsubscribe the old kline channel first.

### `trade` (public) — trade
Push fields: `ch`, `symbol`, `ts`, `data[].p`, `data[].v`, `data[].s`, `data[].t`

### `balance` (private) — balance
Push fields: `ch`, `ts`, `data.coin`, `data.available`, `data.frozen`, `data.isolationFrozen`, `data.crossFrozen`, `data.margin`, `data.isolationMargin`, `data.crossMargin`, `data.expMoney`

### `position` (private) — position
Push fields: `ch`, `ts`, `data.event`, `data.positionId`, `data.marginMode`, `data.positionMode`, `data.side`, `data.leverage`, `data.margin`, `data.ctime`, `data.qty`, `data.symbol`, `data.realizedPNL`, `data.unrealizedPNL`, `data.funding`, `data.fee`

### `order` (private) — order
Push fields: `ch`, `ts`, `data.event`, `data.orderId`, `data.symbol`, `data.positionType`, `data.positionMode`, `data.side`, `data.effect`, `data.type`, `data.qty`, `data.price`, `data.ctime`, `data.mtime`, `data.leverage`, `data.orderStatus`, `data.fee`, `data.averagePrice`, `data.dealAmount`, `data.clientId`, `data.tpStopType`, `data.tpPrice`, `data.tpOrderType`, `data.tpOrderPrice`, `data.slStopType`, `data.slPrice`, `data.slOrderType`, `data.slOrderPrice`

### `tpsl` (private) — tpsl
Push fields: `ch`, `ts`, `data.event`, `data.positionId`, `data.orderId`, `data.symbol`, `data.leverage`, `data.side`, `data.positionMode`, `data.status`, `data.ctime`, `data.type`, `data.tpQty`, `data.slQty`, `data.tpStopType`, `data.tpPrice`, `data.tpOrderType`, `data.tpOrderPrice`, `data.slStopType`, `data.slPrice`, `data.slOrderType`, `data.slOrderPrice`

> Pushed automatically after login. data is always an object. Read event together with status; CLOSE+FILLED = child order placed, not filled.

## ERROR CODES

| Code | Message |
|---|---|
| 0 | Success |
| 403 | This API key does not support this operation |
| 10001 | Network Error |
| 10002 | Parameter Error |
| 10003 | api-key can't be empty |
| 10004 | The current ip is not in the apikey ip whitelist |
| 10005 | Too many requests, please try again later |
| 10006 | Request too frequently |
| 10007 | Sign signature error |
| 10008 | {value} does not comply with the rule, optional [correctValue] |
| 10009 | Another order operation is in progress, please try again shortly |
| 10010 | Nonce's length must be less than 64 |
| 10011 | The request timestamp must be within 60,000 ms of the server time. |
| 20001 | Market not exists |
| 20002 | The current positions amount has exceeded the maximum open limit, please adjust the risk limit |
| 20003 | Insufficient balance |
| 20004 | Insufficient Trader |
| 20005 | Invalid leverage |
| 20006 | You can't change leverage or margin mode as there are open orders |
| 20007 | Order not found, please try it later |
| 20008 | Insufficient amount |
| 20009 | Position exists, so positions mode cannot be updated |
| 20010 | Activation failed, the available balance in the futures account does not meet the conditions for activation of the coupon |
| 20011 | Account not allowed to trade |
| 20012 | This futures does not allow trading |
| 20013 | Function disabled due tp pending account deletion request |
| 20014 | Account deleted |
| 20015 | This futures is not supported |
| 20016 | Batch order exceeded the max size |
| 30001 | Failed to order. Please adjust the order price or the leverage as the order price dealt may immediately liquidate. |
| 30002 | Price below liquidated price |
| 30003 | Price above liquidated price |
| 30004 | Position not exist |
| 30005 | The trigger price is closer to the current price and may be triggered immediately |
| 30006 | Please select TP or SL |
| 30007 | TP trigger price is greater than average entry price |
| 30008 | TP trigger price is less than average entry price |
| 30009 | SL trigger price is less than average entry price |
| 30010 | SL trigger price is greater than average entry price |
| 30011 | Abnormal order status |
| 30012 | Already added to favorite |
| 30013 | Exceeded the maximum order quantity |
| 30014 | Max Buy Order Price |
| 30015 | Mini Sell Order Price |
| 30016 | The qty should be larger than |
| 30017 | The qty cannot be less than the minimum qty |
| 30018 | Order failed. No position opened. Cancel [Reduce-only] settings and retry later |
| 30019 | Order failed. A [Reduce-only] order can not be in the same direction as the open position |
| 30020 | Trigger price for TP should be higher than mark price: |
| 30021 | Trigger price for TP should be lower than mark price: |
| 30022 | Trigger price for SL should be higher than mark price: |
| 30023 | Trigger price fo SL should be lower than mark price: |
| 30024 | Trigger price for SL should be lower than liq price: |
| 30025 | Trigger price for SL should be higher than liq price: |
| 30026 | TP price must be greater than last price: |
| 30027 | TP price must be greater than mark price: |
| 30028 | SL price must be less than last price: |
| 30029 | SL price must be less than mark price: |
| 30030 | SL price must be greater than last price: |
| 30031 | SL price must be greater than mark price: |
| 30032 | TP price must be less than last price: |
| 30033 | TP price must be less than mark price: |
| 30034 | TP price must be less than mark price: |
| 30035 | SL price must be greater than trigger price: |
| 30036 | TP price must be greater than trigger price: |
| 30037 | TP price must be greater than trigger price: |
| 30038 | TP/SL amount must be less than the size of the position. |
| 30039 | The order qty can't be greater than the max order qty: |
| 30040 | Futures trading is prohibited, please contact customer service. |
| 30041 | Trigger price must be greater than 0 |
| 30042 | Client ID duplicate |
| 40001 | Please cancel open orders and close all positions before canceling lead trading |
| 40002 | Lead amount hast to be over the limits |
| 40003 | Lead order amount exceeds the limits. |
| 40004 | Please do not repeat the operation |
| 40005 | Action is not available for the current user type. |
| 40006 | Sub-account reaches the limit. |
| 40007 | Share settlement is being processed,lease try again later |
| 40008 | After the transfer, the account balance will be less than the order amount, please enter again. |
