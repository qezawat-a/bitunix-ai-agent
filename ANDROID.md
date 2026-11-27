# Android app integration

Run the agent on a server (VPS, Railway, home PC) and point your app at it. All keys stay on the server; the phone only holds `API_TOKEN`.

## Endpoints

| Method | Path | Body / query | Returns |
|---|---|---|---|
| GET | `/health` | – | `{ok, dry_run, ws, keys}` |
| POST | `/v1/chat` | `{"chat_id":"phone-1","message":"long BTC 50 USDT 10x"}` | `{"chat_id","reply"}` |
| GET | `/v1/overview` | – | account, positions, open orders, TP/SL, risk |
| GET | `/v1/market/{symbol}` | – | price, mark, funding, spread, book imbalance, rules |
| GET | `/v1/analysis/{symbol}?interval=1h` | – | indicators + trend |
| GET | `/v1/tools` | – | every tool with JSON schema (`mutating` flag) |
| POST | `/v1/tools/{name}?chat_id=phone-1` | tool args JSON | tool result (may be `PENDING_CONFIRMATION`) |
| POST | `/v1/confirm/{id}?chat_id=phone-1` | – | executes a pending action |
| GET | `/v1/alerts`, `/v1/tasks`, `/v1/journal?limit=50` | – | lists |
| WS | `/v1/stream?token=API_TOKEN` | send `{"chat_id","message"}` to chat | pushes `notification`, `event`, `reply` |

Header for every HTTP call: `Authorization: Bearer <API_TOKEN>`. Tool schemas: `GET /v1/tools` (JSON Schema for every tool). Status: `GET /health`.

## Confirmations in the UI
When a trade tool returns
```json
{"status":"PENDING_CONFIRMATION","confirmation_id":"a1b2c3","summary":"open_position {...}"}
```
show a ticket with Confirm / Cancel. Confirm → `POST /v1/confirm/a1b2c3`. In chat mode the agent asks in words and the user can simply reply "yes".

## Kotlin (OkHttp + kotlinx.serialization)

```kotlin
@Serializable data class ChatIn(val chat_id: String, val message: String)
@Serializable data class ChatOut(val chat_id: String, val reply: String)

class AgentApi(private val base: String, private val token: String) {
    private val http = OkHttpClient.Builder().readTimeout(120, TimeUnit.SECONDS).build()
    private val json = Json { ignoreUnknownKeys = true }
    private val media = "application/json".toMediaType()

    suspend fun chat(text: String, chatId: String = "phone-1"): String = withContext(Dispatchers.IO) {
        val req = Request.Builder().url("$base/v1/chat")
            .header("Authorization", "Bearer $token")
            .post(json.encodeToString(ChatIn(chatId, text)).toRequestBody(media)).build()
        http.newCall(req).execute().use { json.decodeFromString<ChatOut>(it.body!!.string()).reply }
    }

    suspend fun tool(name: String, argsJson: String): String = withContext(Dispatchers.IO) {
        val req = Request.Builder().url("$base/v1/tools/$name?chat_id=phone-1")
            .header("Authorization", "Bearer $token")
            .post(argsJson.toRequestBody(media)).build()
        http.newCall(req).execute().use { it.body!!.string() }
    }

    fun stream(onMessage: (String) -> Unit): WebSocket =
        http.newWebSocket(Request.Builder().url("${base.replace("http", "ws")}/v1/stream?token=$token").build(),
            object : WebSocketListener() {
                override fun onMessage(webSocket: WebSocket, text: String) = onMessage(text)
            })
}
```

Examples:
```kotlin
api.chat("analyze BTC 4h and suggest a trade")
api.tool("open_position", """{"symbol":"BTCUSDT","side":"LONG","margin_usdt":50,"leverage":10,"stop_loss":58000}""")
api.tool("close_position", """{"symbol":"BTCUSDT","percent":50}""")
api.tool("api_get_pending_positions", "{}")            // any official endpoint, same parameter names as the docs
```

Stream messages:
```json
{"type":"event","kind":"order","ch":"order","data":{"orderStatus":"FILLED", ...}}
{"type":"event","kind":"position","ch":"position","data":{"event":"OPEN", ...}}
{"type":"event","kind":"tpsl","ch":"tpsl","data":{"event":"CLOSE","status":"FILLED","meaning":"Trigger succeeded ..."}}
{"type":"notification","chat_id":"tg:123","text":"🔔 Alert ..."}
{"type":"reply","chat_id":"app:phone-1","text":"..."}
```

Use HTTPS in production (Caddy or nginx in front, or Railway's domain) and a long random `API_TOKEN`.
