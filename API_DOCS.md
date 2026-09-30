# 🚀 Nexus Store REST API Documentation

The Nexus Store REST API allows you to integrate your Telegram bot's ordering system with external websites, web panels, or other applications.

---

## 🔐 Authentication

All API requests require an **API Key** passed in the headers.
You can find or change your API Key in the `.env` file (variable `API_KEY`) or by tapping **🔑 API Settings** inside the bot's Admin Panel.

**Header Format:**
```http
X-API-Key: your-secret-api-key-here
```

---

## 📦 1. Create a New Order

Place a new order on behalf of a user. The order will be immediately sent to the Supplier Group in Telegram with the **DONE / ERROR** inline buttons.

- **Endpoint:** `POST /orders/`
- **Content-Type:** `application/json`

### Request Body

| Field | Type | Description |
|-------|------|-------------|
| `telegram_user_id` | `integer` | The Telegram ID of the user (they must have started the bot at least once so they exist in the DB). |
| `product_id` | `integer` | The ID of the product they are purchasing (viewable in Admin Panel -> Products). |
| `player_id` | `string` | The PUBG Player ID (3 to 16 characters). |

### 🟢 Example: cURL

```bash
curl -X POST "http://your-domain.com/orders/" \
     -H "X-API-Key: my_secret_key" \
     -H "Content-Type: application/json" \
     -d '{
           "telegram_user_id": 6072311425,
           "product_id": 2,
           "player_id": "5123456789"
         }'
```

### 🐍 Example: Python (`requests`)

```python
import requests

url = "http://your-domain.com/orders/"
headers = {
    "X-API-Key": "my_secret_key",
    "Content-Type": "application/json"
}
data = {
    "telegram_user_id": 6072311425,
    "product_id": 2,
    "player_id": "5123456789"
}

response = requests.post(url, json=data, headers=headers)
print(response.json())
```

### ✅ Successful Response (`200 OK`)

```json
{
  "order_id": "NX482910",
  "status": "pending",
  "product_name": "325 UC",
  "unit_price": 4.99,
  "player_id": "5123456789",
  "created_at": "2023-10-25T14:30:00.000Z"
}
```

---

## 🔍 2. Check Order Status

Retrieve the current status of an existing order.

- **Endpoint:** `GET /orders/{order_id}`
- **Parameters:** `order_id` (string) — The unique `NX...` order ID.

### 🟢 Example: cURL

```bash
curl -X GET "http://your-domain.com/orders/NX482910" \
     -H "X-API-Key: my_secret_key"
```

### 🐍 Example: Python (`requests`)

```python
import requests

url = "http://your-domain.com/orders/NX482910"
headers = {"X-API-Key": "my_secret_key"}

response = requests.get(url, headers=headers)
print(response.json())
```

### ✅ Successful Response (`200 OK`)

```json
{
  "order_id": "NX482910",
  "status": "completed"
}
```

*Note: Possible statuses are `pending`, `processing`, `completed`, `failed`, `cancelled`.*

---

## ❌ Error Responses

If something goes wrong, the API returns a JSON error detail.

**401 Unauthorized (Missing or Invalid API Key)**
```json
{
  "detail": "Invalid or missing API Key"
}
```

**400 Bad Request (User not found / Invalid Product)**
```json
{
  "detail": "User not found in system. They must use the bot at least once."
}
```

**422 Unprocessable Entity (Validation Error - e.g. Player ID too short)**
```json
{
  "detail": [
    {
      "loc": ["body", "player_id"],
      "msg": "ensure this value has at least 3 characters",
      "type": "value_error.any_str.min_length"
    }
  ]
}
```
