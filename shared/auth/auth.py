"""HotSot Auth Module — JWT + API Key authentication."""

import os
import time
import hmac
import hashlib
from typing import Optional, Dict, Any
from dataclasses import dataclass


SECRET_KEY = os.getenv("AUTH_SECRET", "hotsot-dev-secret-change-in-prod")
API_KEY_SALT = os.getenv("API_KEY_SALT", "hotsot-salt")


@dataclass
class AuthContext:
    """Authenticated user context."""
    user_id: str
    kitchen_id: Optional[str] = None
    role: str = "user"
    is_premium: bool = False


def generate_api_key(kitchen_id: str) -> str:
    """Generate API key for kitchen integrations."""
    raw = f"{kitchen_id}:{API_KEY_SALT}:{time.time()}"
    return hmac.new(SECRET_KEY.encode(), raw.encode(), hashlib.sha256).hexdigest()[:40]


def verify_api_key(api_key: str, kitchen_id: str) -> bool:
    """Verify an API key (simplified for MVP — use proper key store in prod)."""
    expected = generate_api_key(kitchen_id)
    return hmac.compare_digest(api_key, expected)


def create_jwt_token(user_id: str, role: str = "user", kitchen_id: Optional[str] = None) -> str:
    """Create a simple JWT-like token (use PyJWT in production)."""
    import base64
    import json

    header = base64.urlsafe_b64encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode()).decode()
    payload_data = {
        "sub": user_id,
        "role": role,
        "kid": kitchen_id,
        "iat": int(time.time()),
        "exp": int(time.time()) + 86400,  # 24h
    }
    payload = base64.urlsafe_b64encode(json.dumps(payload_data).encode()).decode()
    signature = hmac.new(
        SECRET_KEY.encode(), f"{header}.{payload}".encode(), hashlib.sha256
    ).hexdigest()

    return f"{header}.{payload}.{signature}"


def decode_jwt_token(token: str) -> Optional[Dict[str, Any]]:
    """Decode and verify a JWT token."""
    import base64
    import json

    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None

        header, payload, signature = parts
        expected_sig = hmac.new(
            SECRET_KEY.encode(), f"{header}.{payload}".encode(), hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(signature, expected_sig):
            return None

        payload_data = json.loads(base64.urlsafe_b64decode(payload + "=="))
        if payload_data.get("exp", 0) < time.time():
            return None

        return payload_data
    except Exception:
        return None
