# Nexus Store Bot

A Telegram ordering bot for invited members, with a REST API for partner stores.
Customers order top-ups in English or Urdu and track them live; each order is routed
to the supplier group for its product, and suppliers mark it done from Telegram.

## How it fits together

| Part | Runs as | What it does |
|---|---|---|
| `main.py` | PythonAnywhere **Always-on task** | Telegram bot, plus a background worker for delivery retries, supplier timeouts, store webhooks, a heartbeat, and daily backups |
| `api/flask_app.py` | PythonAnywhere **Web app** (WSGI) | REST API for partner stores (`/v1/products/`, `/v1/orders/`, `/health`) |
| `nexus_bot.db` | SQLite file | Shared by both; upgraded automatically on start-up |

```
api/            Flask REST API
bot/            Telegram handlers, keyboards, i18n (English/Urdu), background worker
  handlers/admin/   Owner and staff control panel (one module per area)
services/       Shared logic: supplier delivery, notifications, timeouts, webhooks
database/       Models, repositories, versioned migrations
utils/          UI building blocks, security helpers, exports, backups
tests/          pytest suite (runs on every push via GitHub Actions)
```

## Roles

- **Owners** (`ADMIN_IDS`): everything — products, supplier routing, access codes, API stores, limits, broadcast, payments, resets.
- **Staff** (`STAFF_IDS`): orders (find, resend, reassign, close), customer lookups, the dashboard, and support replies.
- **Customers**: sign in once with an invite code or one-tap invite link, then order, reorder, track, and contact support.
- **Suppliers**: tap ✅ DONE / ❌ ERROR on the order message in their group, or reply with a photo to send delivery proof.

## Local development

```bash
python -m venv .venv
.venv/Scripts/activate          # Windows; use `source .venv/bin/activate` elsewhere
pip install -r requirements-dev.txt
cp .env.example .env            # fill in BOT_TOKEN and ADMIN_IDS
pytest                          # 47 tests
ruff check .                    # lint
```

Do not run `main.py` with the production bot token while the live bot is running:
two pollers on one token steal each other's messages. Use a separate test bot from
@BotFather for local runs.

## Deploying

See [RUNBOOK.md](RUNBOOK.md) for deploys, backups and restores, key rotation, and troubleshooting.
Store integration details are in [API_INTEGRATION_GUIDE.md](API_INTEGRATION_GUIDE.md).

## Configuration

All settings come from `.env` (see [.env.example](.env.example)). Changing schema? Add a
numbered step to `database/migrations.py`; both processes apply it on start-up.
