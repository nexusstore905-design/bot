# Nexus Store REST API

The API creates a fulfillment order and sends it to the configured supplier. It does **not** set product prices, collect payment, or verify payment. An external website must collect and verify payment before it calls `POST /orders/`.

Use HTTPS when the API is reachable over the internet.

## Check API health

`GET /health` checks that the Flask API can reach its database. It does not require an API key and returns `503` if the database cannot be reached.

## Authentication

Send an API key in the `X-API-Key` header. The admin panel can create separate API store keys. A key created for one store can view only orders created with that same key; it cannot view Telegram orders or another store's orders.

An optional master key can be set as `API_KEY` in `.env`. It has access to all order statuses and is not quota-limited. There is no built-in default master key: leave it blank to use per-store keys only. If you set it, use a long random secret and keep it private.

Per-store daily limits count valid order submissions. Checking an order's status does not use the order quota. Set `API_CORS_ORIGINS` to a comma-separated list of exact browser origins if browser-based clients need CORS access; by default, cross-origin browser access is disabled.

## Create an order

`POST /orders/` accepts JSON:

| Field | Type | Description |
|---|---|---|
| `telegram_user_id` | integer | User must already exist in the bot database. |
| `product_id` | integer | Active product ID. |
| `player_id` | string | 5–16 digits, starting with `5`. |

Example:

```bash
curl -X POST "https://your-domain.example/orders/" \
  -H "X-API-Key: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"telegram_user_id": 6072311425, "product_id": 2, "player_id": "5123456789"}'
```

Successful response (`200 OK`):

```json
{
  "order_id": "NX482910",
  "status": "pending",
  "product_name": "325 UC",
  "player_id": "5123456789",
  "created_at": "2026-09-30T12:30:00+00:00",
  "supplier_notified": true
}
```

The product catalogue in `nexus_bot` has no price field. The API response therefore does not include a price. This endpoint submits an order for fulfillment; it does not represent payment confirmation. If `supplier_notified` is `false`, the order is saved with `failed` status and needs manual follow-up. Do not blindly resubmit it, since that can create a second order.

## Check order status

`GET /orders/{order_id}` returns the order status. A per-store key can only check its own API orders. A master key can check all orders.

```bash
curl "https://your-domain.example/orders/NX482910" \
  -H "X-API-Key: YOUR_API_KEY"
```

Response:

```json
{
  "order_id": "NX482910",
  "status": "pending"
}
```

Possible statuses are `pending`, `processing`, `completed`, `failed`, and `cancelled`.

## Errors

- `401`: missing or invalid API key.
- `403`: the API store key has been disabled.
- `404`: order not found or not visible to this API store.
- `429`: per-user or per-store order limit reached.
- `503`: no supplier is configured for the selected product.
- `422`: invalid request field, including an invalid player ID.
