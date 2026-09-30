import bcrypt


def hash_pin(plain_pin: str) -> str:
    """Hash a PIN using bcrypt. Never store plain text."""
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(plain_pin.encode(), salt).decode()


def verify_pin(plain_pin: str, hashed: str) -> bool:
    """Verify a plain PIN against a stored bcrypt hash."""
    try:
        return bcrypt.checkpw(plain_pin.encode(), hashed.encode())
    except Exception:
        return False
