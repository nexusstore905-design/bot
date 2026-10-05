# Store API Integration Guide

This guide explains how to connect an online store or other trusted backend to the Nexus Telegram bot API.

## 1. What the integration does

The store sends an order to the bot API. The API checks the Telegram customer, product, supplier route, and order limits; saves the order; and sends the order to the supplier group configured for that product. The supplier completes or rejects the order in Telegram. The store can then ask the API for the order's current status.

Each product uses its own supplier route. For example, if product `8100` is assigned to Supplier A and product `3850` to Supplier B in the bot, an order for `8100` goes to A and an order for `3850` goes to B. The store selects the product by its `product_id`; the API follows the bot's configured route. A global supplier chat is used as a fallback when a product has no individual supplier configured.

The API currently creates one unit per request. It does not accept quantity, price, discount, or payment fields. Payment collection and reconciliation remain the store's responsibility.

## 2. Before connecting

You will need:

1. A running Telegram bot and its database.
2. The API service running against the **same database** as the bot.
3. An active API store and its API key.
4. The Telegram user ID for each customer and the bot's product ID for each item you plan to sell.
5. A supplier route configured for each product, or a configured global fallback supplier.

### Create an API store and key

In the bot, open **Admin → Advanced settings → API stores → Add Store**. Give the connected platform a name and save the generated key immediately; the full key is shown only when the store is created. The API store can be disabled and its daily order limit can be changed from the admin controls.

Use a separate per-store key for each connected platform. A key created for one API store can only check orders created through that store. The optional master `API_KEY` can access orders across stores, so keep it for trusted server-side administration and do not distribute it to a storefront.

### Find product IDs

Use the product IDs shown in the bot's product administration/database. The API does not currently provide a product catalog or price endpoint. Keep a mapping in your store, for example:

| Store item/SKU | Bot `product_id` | Bot product name | Supplier route configured in bot |
|---|---:|---|---|
| `pubg-8100` | `8100` | Product name from bot | Supplier A |
| `pubg-3850` | `3850` | Product name from bot | Supplier B |

The sample IDs above are illustrative; confirm the actual IDs in your bot before using them.

## 3. Run and expose the API

The Flask application object is `app` in `api/flask_app.py`. The API process must have the same `BOT_TOKEN` and `DATABASE_URL` configuration as the bot. If `DATABASE_URL` is omitted, the project defaults to its local SQLite database; separate machines or containers must not accidentally use separate default database files. Relative SQLite URLs such as `sqlite+aiosqlite:///./nexus_bot.db` are resolved from the project directory so a WSGI process and the bot task do not silently create different databases because they started in different working directories. If the app and bot run from separate project copies, configure both to use the same absolute database path or shared database service.

For local development from the project directory:

```powershell
flask --app api.flask_app:app run --host 127.0.0.1 --port 5000
```

The local base URL is then `http://127.0.0.1:5000`. To call from another machine on a private development network, bind to the appropriate interface and use that machine's address. The Flask development server is for development only. For a live store, configure a production WSGI host to load `api.flask_app:app` (the bot admin panel currently points to the PythonAnywhere Web tab), use HTTPS, and ensure the API service and bot share the intended database.

**Run the Telegram bot as a separate persistent process as well.** The 10-minute supplier timeout worker starts from `main.py`; the Flask WSGI app does not run that worker. On PythonAnywhere, configure an Always-on task using the project's virtual-environment Python and the full path to `main.py`. Both the web app and this bot task must use the same project files, `BOT_TOKEN`, and `DATABASE_URL`. Reloading only the Web app does not restart the timeout worker. On bot startup, its task log should show `Supplier expiry worker started`.

Set environment variables in the API host's protected configuration. Do not put secrets in source control or in a public web page:

```text
BOT_TOKEN=<the bot token>
DATABASE_URL=<the same database URL used by the bot>
API_KEY=
API_CORS_ORIGINS=
```

`API_KEY` is optional when using per-store keys only. If you configure it, use a long random secret and restrict its use to trusted backend services. Restart the API process after changing its environment.

After deployment, check:

```text
GET https://YOUR_API_HOST/
GET https://YOUR_API_HOST/health
```

The root endpoint returns the service name and route names. `/health` checks database connectivity and does not require an API key. It returns HTTP `200` when the database is reachable and `503` when it is not.

Use the origin root as `BASE_URL`, without adding `/orders/` to it. For example:

```text
BASE_URL=https://api.example.com
```

## 4. Authentication and key handling

Every order endpoint request must include the key in the `X-API-Key` HTTP header:

```http
X-API-Key: YOUR_STORE_API_KEY
```

Do not put the key in a URL, query string, HTML page, browser JavaScript bundle, mobile app, or source repository. Browser users can inspect those values. For a browser storefront, call your own backend first; have that backend add `X-API-Key` and call this API over HTTPS.

If a key is exposed, disable the API store in the bot admin controls and issue a replacement through the store/key management workflow. Treat API keys like passwords.

## 5. Create an order

### Endpoint

```http
POST /orders/
Content-Type: application/json
X-API-Key: YOUR_STORE_API_KEY
```

### Request fields

| Field | Type | Required | Meaning and validation |
|---|---|---:|---|
| `telegram_user_id` | integer | Yes | Telegram numeric ID of a user who has started the bot at least once. |
| `product_id` | integer | Yes | ID of an active product in the bot. This product's configured supplier route is used. |
| `player_id` | string | Yes | PUBG player ID: digits only, starts with `5`, and 5–16 digits long. Send as a string to preserve it exactly. |

The JSON body must be an object. Quantity is always one. The API does not verify whether your customer paid; submit orders only according to your store's payment policy.

### cURL example

```bash
curl -X POST "https://YOUR_API_HOST/orders/" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_STORE_API_KEY" \
  -d '{
    "telegram_user_id": 123456789,
    "product_id": 8100,
    "player_id": "5123456789"
  }'
```

Replace the example user/product/player IDs with real values. Do not include the key directly in a command that will be saved in shell history on a shared machine.

### Node.js backend example

This example uses Node's built-in `fetch`. Store the key in the backend environment, such as `process.env.NEXUS_STORE_API_KEY`.

```js
const baseUrl = process.env.NEXUS_API_BASE_URL;
const apiKey = process.env.NEXUS_STORE_API_KEY;

async function createBotOrder({ telegramUserId, productId, playerId }) {
  const response = await fetch(`${baseUrl}/orders/`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-API-Key": apiKey,
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

Call this from your server after your store has decided the order is ready to submit. Save the returned `order_id` alongside your store's order record.

### Successful response

HTTP `200` returns a JSON object similar to:

```json
{
  "order_id": "NX-ABC123",
  "status": "pending",
  "product_name": "Product name from bot",
  "player_id": "5123456789",
  "created_at": "2026-10-02T10:15:00+00:00",
  "supplier_notified": true
}
```

The exact order ID, product name, timestamp, and initial status will differ. The response does not include a price, quantity, or payment result. `supplier_notified: true` means the API delivered the Telegram supplier message; it does not mean the supplier has completed the order.

Important: supplier delivery failure is currently returned as HTTP `200` with `status: "failed"` and `supplier_notified: false`. The order record still exists, and the bot alerts its admins for manual follow-up. Check both HTTP status and the JSON `status`/`supplier_notified` fields.

## 6. Check order status

### Endpoint

```http
GET /orders/{order_id}
X-API-Key: YOUR_STORE_API_KEY
```

Example:

```bash
curl "https://YOUR_API_HOST/orders/NX-ABC123" \
  -H "X-API-Key: YOUR_STORE_API_KEY"
```

Response:

```json
{
  "order_id": "NX-ABC123",
  "status": "processing"
}
```

Store API keys can only retrieve orders placed using that store's key. The master key, if configured, can retrieve orders from any store. An unknown order or an order outside the calling store's scope returns `404`.

### Status values

| Status | Meaning for the store |
|---|---|
| `pending` | The order is waiting for supplier action, or no supplier fulfillment has reached a final result yet. |
| `processing` | At least one supplier fulfillment has finished, while other fulfillment work is still outstanding. |
| `completed` | All supplier fulfillment work was marked complete. |
| `failed` | Supplier delivery or fulfillment failed. Admin/supplier follow-up may be needed. |
| `cancelled` | The bot cancelled an order after it remained unresolved past its configured timeout. |

The API does not send a store webhook or callback. Your backend should poll the status endpoint at a reasonable interval, stop when the status is terminal (`completed`, `failed`, or `cancelled`), and apply your own customer notification/refund process. Do not poll continuously at very short intervals.

## 7. HTTP errors and what to do

Errors use a JSON body like `{"detail":"..."}`.

| HTTP | Typical cause | Recommended handling |
|---:|---|---|
| `400` | Missing/invalid field, invalid or inactive product, or Telegram user has not started the bot. | Fix the data or have the customer start the bot, then submit a corrected order. |
| `401` | Missing/invalid API key, or API store is no longer available. | Check the secret and header; do not retry with a key embedded in a URL. |
| `403` | The API store has been disabled by the bot admin. | Ask the bot admin to enable the store. |
| `404` | Order ID is unknown or not visible to this API store key. | Check the saved ID and which store key created the order. |
| `429` | Store daily order limit or the Telegram user's daily limit was reached. | Show an appropriate message or wait for the next daily reset; ask the admin to review limits if needed. |
| `503` | No supplier is configured for this product, or the health check cannot reach the database. | Check product/global supplier configuration or database availability before retrying. |

Supplier Telegram delivery failure is an exception to the HTTP error table: it is represented as a `200` response with a failed order status, as described above.

## 8. Quotas and order accounting

- Each API store has an independent daily order limit. In the admin store settings, `0` means unlimited.
- Telegram users may also have an individual daily limit. A user with no limit configured is unlimited; an explicit user limit of `0` blocks ordering.
- The bot resets daily counters using UTC midnight.
- The counters are reserved when the valid order is accepted and saved. Supplier completion does not undo that count.
- The optional master API key bypasses the per-store quota and per-store order scoping, but user-level order limits still apply.

## 9. Browser/CORS setup

Server-to-server store integrations do not need CORS. If a browser must call the API directly, set `API_CORS_ORIGINS` to a comma-separated list of exact origins, including scheme and port where applicable, for example:

```text
API_CORS_ORIGINS=https://store.example.com,https://admin.store.example.com
```

The API allows `GET`, `POST`, and `OPTIONS`, and the `Content-Type` and `X-API-Key` headers for listed origins. Restart the API after changing this setting. CORS only controls browser access; it does not protect a key placed in frontend code. Keep the key on your server and proxy storefront requests through your backend.

## 10. Reliability notes and current API limits

### Avoid duplicate orders after a timeout

The API currently has no idempotency key or external store order reference. If your `POST /orders/` call times out, the API may already have created the order. Do not blindly submit the same purchase again, because that can create a duplicate recharge request. First reconcile with the bot admin using the customer, product, player ID, and approximate time. Save the returned `order_id` as soon as a response arrives.

For stronger duplicate protection, a future API improvement would accept a unique store order ID or `Idempotency-Key` and return the original bot order on safe retries.

### Product catalog and price

There is no API endpoint to list products, prices, stock, or supplier routes. Manage the product-to-ID mapping in your store and keep it synchronized with the bot admin configuration. Your store controls the customer price; the bot API does not quote or charge the customer.

### One item per request

One API request creates one bot order for one product and quantity one. To sell a multi-item basket, submit one request per bot product and record all returned order IDs against the store checkout. If the basket spans different supplier routes, the bot routes each product order according to its own configuration.

### No webhook

The API has no webhook registration endpoint. Poll `GET /orders/{order_id}` from your backend or have an admin review the order in the bot.

## 11. Suggested store-side flow

1. Customer chooses an item and enters their Telegram ID and PUBG player ID.
2. Store validates its own checkout/payment rules and maps the item to the bot's `product_id`.
3. Store backend calls `POST /orders/` with the customer Telegram ID, product ID, and player ID.
4. Store saves the `order_id` returned by the API and shows the customer that fulfillment is pending.
5. Store backend periodically calls `GET /orders/{order_id}`.
6. On `completed`, mark the store order fulfilled. On `failed` or `cancelled`, route it to support/admin handling under your refund or retry policy.

## 12. Quick troubleshooting checklist

- `401`: Is `X-API-Key` present, copied correctly, and associated with an active API store?
- `403`: Did an admin disable this API store?
- `400 User not found`: Has the customer opened the Telegram bot and pressed Start? Is the numeric Telegram ID correct?
- `400 Invalid or inactive product ID`: Is the product ID correct and active?
- `400 Invalid player_id`: Is it a string of 5–16 digits beginning with `5`?
- `429`: Has the store or customer reached a daily order limit?
- `503 No supplier is configured`: Is a supplier assigned to this product or is the global fallback configured?
- `/health` returns `503`: Is the API using the same reachable database configuration as the bot?
- HTTP `200` but `supplier_notified: false`: The order was recorded but Telegram delivery failed; check bot logs, bot token, supplier chat, and bot membership/permissions, and follow up with an admin before creating another order.

## Endpoint summary

| Method | Path | Authentication | Purpose |
|---|---|---|---|
| `GET` | `/` | None | Service status and route names |
| `GET` | `/health` | None | Database connectivity check |
| `POST` | `/orders/` | `X-API-Key` | Create one product order |
| `GET` | `/orders/{order_id}` | `X-API-Key` | Read order status |
