import jwt
from datetime import datetime, timedelta, timezone
from typing import Optional
from app.config import settings

ALGORITHM = "HS256"

VALID_ROLES = {"admin", "user"}


def create_token(user_id: int, username: str, role: str = "user") -> str:
    if role not in VALID_ROLES:
        role = "user"
    now = datetime.now(timezone.utc)
    payload = {
        "user_id": user_id,
        "username": username,
        "role": role,
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


def get_role_from_token(token: str) -> str:
    """从 JWT 中获取角色，缺失或异常时默认返回 user"""
    payload = verify_token(token)
    if payload is None:
        return "user"
    role = payload.get("role", "user")
    if role not in VALID_ROLES:
        return "user"
    return role