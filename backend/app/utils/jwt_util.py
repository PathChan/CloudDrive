import jwt
from datetime import datetime, timedelta, timezone
from typing import Optional
from app.config import settings

ALGORITHM = "HS256"


def create_token(user_id: int, username: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "user_id": user_id,
        "username": username,
        "iat": now,
        "exp": now + timedelta(hours=settings.jwt_expiration_hours),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def verify_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        return None


def get_user_id_from_token(token: str) -> Optional[int]:
    payload = verify_token(token)
    if payload is None:
        return None
    return payload.get("user_id")


def get_username_from_token(token: str) -> Optional[str]:
    payload = verify_token(token)
    if payload is None:
        return None
    return payload.get("username")