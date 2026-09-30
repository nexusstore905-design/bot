"""Run the FastAPI service as a process separate from the Telegram bot."""

import asyncio

import uvicorn

from config.settings import API_HOST, API_PORT
from database.database import init_db


def main() -> None:
    asyncio.run(init_db())
    uvicorn.run("api.app:app", host=API_HOST, port=API_PORT)


if __name__ == "__main__":
    main()
