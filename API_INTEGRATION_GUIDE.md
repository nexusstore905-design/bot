# Store API Integration Guide

This guide explains how to connect an online store or other trusted backend to the Nexus Telegram bot API.

## 1. What the integration does

The store sends an order to the bot API. The API checks the API key, the Telegram customer, the product, the supplier route, and the order limits; saves the order; and delivers it to the supplier group configured for that product. The supplier completes or rejects the order in Telegram. The store learns the result from a signed **webhook** (recommended) or by polling the status endpoint.

Each product uses its own supplier route. A global supplier chat is used as a fallback when a product has no individual supplier configured.

One request creates one unit of one product. Payment collection remains the store's responsibility.

## 2. Before connecting

You will need:

1. The Telegram bot running as an always-on task, and the API web app, both using the **same database**.
2. An active API store and its API key.
3. Each customer's numeric Telegram user ID. The customer must have opened the bot and **signed in with an access code** at least once.
4. The bot's product ID for each item you sell (`GET /products/` lists them).

### Create an API store and key

In the bot, open **Admin → Advanced → API stores → Add Store**. The full key is shown **once**; only a hash is stored, so it cannot be displayed again. If it is lost or exposed, open the store and use **♻️ Rotate key** — the old key stops working immediately.

From the store screen an admin can also:

- disable/enable the store and set a daily order limit,
- set a **webhook** URL (see section 7),
- restrict which customers the store may order for (**👥 Customers**). A store with no linked customers may order for any signed-in member.

Use a separate key for each connected platform. A store key can only read orders created with that store's key. The optional master `API_KEY` can read orders across stores; keep it for trusted server-side administration.

## 3. Run and expose the API

The Flask application object is `app` in `api/flask_app.py`. Configure a WSGI host (PythonAnywhere Web tab) to load it over HTTPS. The web app applies any pending database migrations when it starts, so it never depends on the bot task having been restarted first.

For local development from the project directory:

```powershell
flask --app api.flask_app:app run --host 127.0.0.1 --port 5000
```

**The Telegram bot must also run as a separate persistent process** (`main.py`, PythonAnywhere Always-on task). It handles supplier delivery retries, supplier timeouts, and webhook delivery. Use the same `BOT_TOKEN` and `DATABASE_URL` for both.

Environment variables:

```text
BOT_TOKEN=<the bot token>
DATABASE_URL=<optional; defaults to nexus_bot.db in the project folder>
API_KEY=
API_CORS_ORIGINS=
CURRENCY=USDT
SUPPLIER_TIMEOUT_MINUTES=10
```

### Health check

```text
GET /health
GET /health?require_bot=1
```

`/health` needs no key. It returns `503` if the database is unreachable. Otherwise it returns `200` with `"bot": "ok" | "stale" | "unknown"`, based on a heartbeat the bot writes every 30 seconds. Point an uptime monitor at `/health?require_bot=1` to get a `503` when the bot task has stopped.

## 4. Authentication

Every endpoint except `/` and `/health` requires the key in the `X-API-Key` header:

```http
X-API-Key: YOUR_STORE_API_KEY
```

Never put the key in a URL, browser JavaScript, or a mobile app. Browser storefronts should call their own backend, which adds the key.

## 5. List products

```http
GET /products/
X-API-Key: YOUR_STORE_API_KEY
```

```json
{
  "products": [
    {"product_id": 3, "category": "PUBG UC Top Up", "name": "660 UC", "price": 9.99, "currency": "USDT"}
  ]
}
```

`price` is `null` unless the admin has enabled price display and set a price for that product.

## 6. Create an order

```http
POST /orders/
Content-Type: application/json
X-API-Key: YOUR_STORE_API_KEY
Idempotency-Key: your-checkout-id-123
```

| Field | Type | Required | Validation |
|---|---|---:|---|
| `telegram_user_id` | integer | Yes | A customer who signed in to the bot with an access code and is not revoked. If the store has linked customers, it must be one of them. |
| `product_id` | integer | Yes | An active product. |
| `player_id` | string | Yes | 3–20 characters, no spaces. Send as a string. |

### Safe retries with `Idempotency-Key`

Send a unique `Idempotency-Key` header (1–64 printable ASCII characters, e.g. your checkout ID) with every order. If the request times out, **retry with the same key**: you get the original order back with `"idempotent_replay": true` and no duplicate is created. Reusing a key with a different customer, product, or player ID returns `409`.

### Example

```bash
curl -X POST "https://YOUR_API_HOST/orders/" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $NEXUS_STORE_API_KEY" \
  -H "Idempotency-Key: checkout-123" \
  -d '{"telegram_user_id": 123456789, "product_id": 3, "player_id": "5123456789"}'
```

### Node.js backend example

```js
async function createBotOrder({ checkoutId, telegramUserId, productId, playerId }) {
  const response = await fetch(`${process.env.NEXUS_API_BASE_URL}/orders/`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-API-Key": process.env.NEXUS_STORE_API_KEY,
      "Idempotency-Key": checkoutId,
    },
    body: JSON.stringify({
      telegram_user_id: telegramUserId,
      product_id: productId,
      player_id: String(playerId),
    }),
  });
  const result = await response.json();
  if (!response.ok) {
    throw new Error(`Nexus API ${response.status}: ${result.detail ?? "Request failed"}`);
  }
  return result;
}
```

### Successful response (HTTP 200)

```json
{
  "order_id": "NX48213907",
  "status": "pending",
  "product_id": 3,
  "product_name": "660 UC",
  "quantity": 1,
  "unit_price": 9.99,
  "currency": "USDT",
  "player_id": "5123456789",
  "created_at": "2026-10-06T10:15:00+00:00",
  "updated_at": "2026-10-06T10:15:00+00:00",
  "supplier_notified": true,
  "delivery": "sent",
  "idempotent_replay": false
}
```

`delivery` is one of:

| Value | Meaning |
|---|---|
| `sent` | The supplier group received the order. |
| `retrying` | Telegram delivery failed; the bot retries automatically (about 20 s and 60 s later). Do **not** resubmit. |
| `failed` | Delivery failed after all retries; the order becomes `failed` and admins are alerted. |

`unit_price` is the product price at order time, or `null` if none was set.

## 7. Webhooks (recommended)

When an admin sets a webhook URL for your store, the bot POSTs an event every time one of your orders changes status:

```http
POST https://your-store.example/hooks/nexus
Content-Type: application/json
X-Nexus-Event: order.status_changed
X-Nexus-Event-Id: 1842
X-Nexus-Signature: t=1791281700,v1=5f2c…
```

```json
{
  "event": "order.status_changed",
  "order_id": "NX48213907",
  "status": "completed",
  "previous_status": "pending",
  "occurred_at": "2026-10-06T10:18:42+00:00"
}
```

**Verify every request.** The signing secret is shown once, when the webhook is first set. Compute HMAC-SHA256 over `"<t>.<raw body>"` with the secret and compare it with `v1`; reject timestamps older than a few minutes.

```js
import crypto from "node:crypto";

function verifyNexusSignature(rawBody, header, secret) {
  const parts = Object.fromEntries(header.split(",").map((p) => p.split("=")));
  const expected = crypto.createHmac("sha256", secret).update(`${parts.t}.${rawBody}`).digest("hex");
  const fresh = Math.abs(Date.now() / 1000 - Number(parts.t)) < 300;
  return fresh && crypto.timingSafeEqual(Buffer.from(expected), Buffer.from(parts.v1));
}
```

Respond with any `2xx` status quickly. Failed deliveries are retried with exponential backoff (30 s, 1 min, 2 min, … up to 8 attempts). Events can arrive more than once; use `X-Nexus-Event-Id` or `order_id` + `status` to ignore duplicates.

## 8. Check order status

```http
GET /orders/{order_id}
X-API-Key: YOUR_STORE_API_KEY
```

Returns the same fields as order creation (without `idempotent_replay`). Store keys can only see their own orders; anything else returns `404`.

| Status | Meaning |
|---|---|
| `pending` | Waiting for the supplier. |
| `processing` | Part of the order is finished; other parts are outstanding. |
| `completed` | All supplier work was marked complete. |
| `failed` | Delivery or fulfillment failed, or only part of the order was completed. Admin follow-up needed. |
| `cancelled` | The supplier did not respond within the timeout (default 10 minutes **after delivery**) and nothing was completed. |

Without a webhook, poll every 30–60 seconds and stop at a terminal status (`completed`, `failed`, `cancelled`).

## 9. Errors

Errors use `{"detail": "..."}`. Unexpected server errors return `500` with `{"detail": "Internal server error", "error_id": "..."}`; quote the `error_id` to the bot admin.

| HTTP | Cause | What to do |
|---:|---|---|
| `400` | Invalid JSON/field, inactive product, unknown customer. | Fix the request. |
| `401` | Missing or invalid API key. | Check the key; it may have been rotated. |
| `403` | Store disabled; customer revoked or never signed in; store not allowed to order for this customer. | Ask the bot admin. |
| `404` | Order not found for this key. | Check the ID and key. |
| `409` | `Idempotency-Key` reused for a different order. | Use a new key for a new order. |
| `429` | Store or customer daily limit reached (UTC days). | Wait for the reset or ask the admin. |
| `503` | No supplier configured for the product, or database unavailable. | Ask the admin to configure routing. |

## 10. Quotas

- Each store has its own daily limit (`0` = unlimited). Each customer can also have one (no limit = unlimited, `0` = blocked).
- Counters reset at UTC midnight and are reserved when an order is accepted.
- The master key bypasses store quotas and scoping; customer limits still apply.

## 11. Browser/CORS

Server-to-server integrations need no CORS. For direct browser calls, set `API_CORS_ORIGINS` to exact origins, e.g. `https://store.example.com`. The `null` origin (local `file://` pages) is accepted only if listed explicitly. CORS does not protect a key placed in frontend code.

## Endpoint summary

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `GET` | `/` | None | Service info |
| `GET` | `/health` | None | Database and bot status |
| `GET` | `/products/` | `X-API-Key` | Active products |
| `POST` | `/orders/` | `X-API-Key` | Create one order (send `Idempotency-Key`) |
| `GET` | `/orders/{order_id}` | `X-API-Key` | Order status and details |
