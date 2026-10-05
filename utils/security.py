import hashlib
import hmac
import secrets

API_KEY_PREFIX_LENGTH = 12


def generate_api_key() -> str:
    return f"nxs_{secrets.token_hex(24)}"


def hash_api_key(api_key: str) -> str:
    # Keys are 192 random bits, so a fast hash is enough; no salt or KDF needed.
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def api_key_prefix(api_key: str) -> str:
    return api_key[:API_KEY_PREFIX_LENGTH]


def generate_webhook_secret() -> str:
    return f"whsec_{secrets.token_hex(24)}"


def sign_webhook(secret: str, timestamp: int, body: bytes) -> str:
    message = f"{timestamp}.".encode() + body
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
