# Runbook

Day-to-day operations for the PythonAnywhere deployment.

## Deploy a new version

1. The change is pushed to `main` on GitHub. The **CI** check (GitHub → Actions) should be green ✅. Don't deploy a red ❌.
2. In a PythonAnywhere **Bash console**:
   ```bash
   cd "/home/maazhassan34/TELEGRAM BOT/nexus_bot" && git fetch && git reset --hard origin/main && pip install -r requirements.txt
   ```
   (Activate the bot's virtualenv first if it uses one.)
3. **Tasks** tab → **Restart** the Always-on task (`main.py`).
4. **Web** tab → **Reload**.
5. Check `https://maazhassan34.pythonanywhere.com/health` shows `"database": "ok"` and, after ~30 seconds, `"bot": "ok"`.

Database migrations run automatically on step 3 or 4 (whichever starts first). Before
upgrading, a copy is saved as `nexus_bot.db.pre-schema-vN.bak`.

## Roll back

```bash
cd "/home/maazhassan34/TELEGRAM BOT/nexus_bot" && git log --oneline -5
git reset --hard <previous-commit>
```
Then restart the task and reload the web app. If the newer version already migrated the
database, the older code still works with the extra columns; only restore a backup if data
itself is wrong (below).

## Backups

- The bot task writes one consistent snapshot per day to `backups/nexus_bot-YYYYMMDD-HHMMSS.db`
  and keeps the newest `BACKUP_KEEP_DAYS` (default 7). Owners get a Telegram alert if a backup fails.
- Download one occasionally (PythonAnywhere **Files** tab) and keep it off the server.

### Restore a backup

1. **Tasks** → stop the Always-on task. **Web** → disable or leave the app (it won't write without the bot for retries, but stop traffic if you can).
2. In a Bash console:
   ```bash
   cd "/home/maazhassan34/TELEGRAM BOT/nexus_bot"
   cp nexus_bot.db nexus_bot.db.before-restore
   cp backups/nexus_bot-YYYYMMDD-HHMMSS.db nexus_bot.db
   ```
3. Start the task and reload the web app.

## Rotate secrets

| Secret | How |
|---|---|
| A store's API key | Bot → `/admin` → ⚙️ Advanced → 🏪 API stores → store → ♻️ Rotate key. Old key stops immediately. |
| A store's webhook secret | Same store screen → 🔔 Webhook → send `off`, then send the URL again. A new secret is shown once. |
| Master `API_KEY` | Edit `.env` on the server, then reload the web app. |
| `BOT_TOKEN` | @BotFather → `/revoke`, put the new token in `.env`, restart the task **and** reload the web app. |

## Common problems

| Symptom | Check |
|---|---|
| `/health` → `"bot": "stale"` or `"unknown"` | The Always-on task stopped or crashed. Open its log on the Tasks tab, then Restart. |
| API returns 500 with an `error_id` | Web tab → error log, search for that `error_id`. |
| Orders stuck "Retrying delivery" | The bot was removed from a supplier group or lost permission. Re-add it, then Find order → 📨 Resend, or 🔀 Reassign to another group. |
| Many orders auto-cancel | Suppliers aren't answering within `SUPPLIER_TIMEOUT_MINUTES`. Check Dashboard → 🤝 Supplier stats. |
| Customers say the bot is "off" | ⚙️ Advanced → maintenance toggle may be on. |
| API returns 429 | A store exceeded `API_RATE_LIMIT_PER_MINUTE` or its daily limit. The response says which. |

## Adding team members

- Owner: add the Telegram ID to `ADMIN_IDS` in `.env`.
- Staff: add the ID to `STAFF_IDS`.
- Restart the task and reload the web app. They send `/admin` to open the panel.
