# ☁️ How to Host on PythonAnywhere

Hosting this system on PythonAnywhere requires running **two things**:
1. The **Telegram Bot** (runs constantly in the background).
2. The **FastAPI REST API** (runs as a Web App to listen for internet traffic).

*Note: To run a background bot 24/7 on PythonAnywhere, you need a paid account (Hacker tier or above) because free accounts do not support "Always-on Tasks".*

---

## Step 1: Upload Your Files
1. Log in to your PythonAnywhere dashboard.
2. Go to the **Files** tab.
3. Open the `mysite/` folder (or create a new folder named `nexus_bot`).
4. Upload all the files from your local `nexus_bot` directory to this folder.

---

## Step 2: Set Up the Virtual Environment
1. Go to the **Consoles** tab and open a new **Bash** console.
2. Run these commands to create an environment and install packages:
   ```bash
   cd ~/nexus_bot
   mkvirtualenv --python=/usr/bin/python3.10 nexus_venv
   pip install -r requirements.txt
   ```
3. Make sure your `.env` file is fully configured with your `BOT_TOKEN`, `ADMIN_IDS`, `SUPPLIER_CHAT_ID`, and `API_KEY`.

---

## Step 3: Run the Telegram Bot (Always-on Task)
Because the bot needs to continuously "listen" to Telegram, it runs in the background.

1. Go to the **Tasks** tab in PythonAnywhere.
2. Scroll down to **Always-on tasks**.
3. In the command box, type:
   ```bash
   /home/YOUR_USERNAME/.virtualenvs/nexus_venv/bin/python /home/YOUR_USERNAME/nexus_bot/main.py
   ```
   *(Replace `YOUR_USERNAME` with your actual PythonAnywhere username)*
4. Click **Create**. The state will change to `Starting` and then `Running`. Your Telegram bot is now online!

---

## Step 4: Host the REST API (Web App)
If you want to use the REST API (`/orders/`) from the internet, you must set it up in the **Web** tab.

1. Go to the **Web** tab and click **Add a new web app**.
2. Click **Next**, then select **Manual Configuration** (do NOT choose FastAPI/Django).
3. Select **Python 3.10**.
4. Once the app is created, scroll down to the **Virtualenv** section.
5. Enter the path to your virtual environment:
   `/home/YOUR_USERNAME/.virtualenvs/nexus_venv`
6. Scroll down to the **Code** section.
   - Set **Source code** to: `/home/YOUR_USERNAME/nexus_bot`
   - Set **Working directory** to: `/home/YOUR_USERNAME/nexus_bot`
7. Click on the **WSGI configuration file** link (it looks like `/var/www/yourusername_pythonanywhere_com_wsgi.py`).

### Editing the WSGI File
Delete everything in that file and paste this exact code:

```python
import sys
import os

# Add your project directory to the sys.path
project_home = '/home/YOUR_USERNAME/nexus_bot'
if project_home not in sys.path:
    sys.path = [project_home] + sys.path

# Load environment variables
from dotenv import load_dotenv
load_dotenv(os.path.join(project_home, '.env'))

# Import the FastAPI app
from api.app import app

# Wrap the ASGI app (FastAPI) in a WSGI adapter so PythonAnywhere can run it
from a2wsgi import ASGIMiddleware
application = ASGIMiddleware(app)
```
*(Make sure to change `YOUR_USERNAME`!)*

8. Save the file.
9. *Important:* Because PythonAnywhere uses WSGI, you need to install the `a2wsgi` adapter. Go back to your Bash console and run:
   ```bash
   workon nexus_venv
   pip install a2wsgi
   ```
10. Go back to the **Web** tab and click the big green **Reload** button.

---

### 🎉 You're Done!

- Your **Telegram bot** is running in the background via the Always-on task.
- Your **API** is live at `https://yourusername.pythonanywhere.com/orders/`. 

Check out `API_DOCS.md` for instructions on how to send requests to your new API!
