"""API key generation and one-way hashing."""

import hashlib
import secrets


def generate_api_key() -> str:
    """Generate a high-entropy live gateway API key."""
    return f"gw_live_{secrets.token_urlsafe(32)}"


def hash_api_key(api_key: str) -> str:
    """Return the SHA-256 hex digest used for storage and lookup."""
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def api_key_prefix(api_key: str) -> str:
    """Return a short prefix safe for identifying a key in admin output."""
    return api_key[:16]
