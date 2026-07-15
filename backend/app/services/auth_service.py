import hashlib
import hmac
import uuid
from typing import Optional
from app.config import settings
from app.database import get_connection
from app.models import User
from app.utils.jwt_util import create_token, get_user_id_from_token


def _hmac_sha256(s: str) -> str:
    return hmac.new(
        settings.user_secret_key.encode(),
        s.encode(),
        hashlib.sha256,
    ).hexdigest()


def _user_row_to_model(row: tuple, columns: list[str]) -> User:
    d = dict(zip(columns, row))
    return User(id=d["id"], username=d["username"], email=d["email"])


def register(username: str, password: str, email: str) -> str:
    if not username or not username.strip():
        raise ValueError("用户名不能为空")
    if not password or not password.strip():
        raise ValueError("密码不能为空")
    if not email or not email.strip():
        raise ValueError("邮箱不能为空")
    if len(password) < 6:
        raise ValueError("密码长度至少6位")

    conn = get_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM user WHERE username = %s OR email = %s", (username.strip(), email.strip()))
        if cursor.fetchone():
            raise ValueError("用户名或邮箱已存在")

        hashed_password = _hmac_sha256(password)
        cursor.execute(
            "INSERT INTO user (username, password, email) VALUES (%s, %s, %s)",
            (username.strip(), hashed_password, email.strip()),
        )
        conn.commit()

        cursor.execute("SELECT * FROM user WHERE username = %s", (username.strip(),))
        row = cursor.fetchone()
        if row is None:
            raise ValueError("注册失败")
        user = User(id=row["id"], username=row["username"], email=row["email"])
        return create_token(user.id, user.username)
    finally:
        conn.close()


def login(email: str, password: str) -> str:
    if not email or not email.strip():
        raise ValueError("邮箱不能为空")
    if not password or not password.strip():
        raise ValueError("密码不能为空")

    conn = get_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        hashed_password = _hmac_sha256(password)
        login_input = email.strip()
        cursor.execute(
            "SELECT * FROM user WHERE (email = %s OR username = %s) AND password = %s",
            (login_input, login_input, hashed_password),
        )
        row = cursor.fetchone()
        if row is None:
            raise ValueError("邮箱或密码错误")
        user = User(id=row["id"], username=row["username"], email=row["email"])
        return create_token(user.id, user.username)
    finally:
        conn.close()


def get_user_by_token(token: str) -> Optional[User]:
    user_id = get_user_id_from_token(token)
    if user_id is None:
        return None
    conn = get_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM user WHERE id = %s", (user_id,))
        row = cursor.fetchone()
        if row is None:
            return None
        return User(id=row["id"], username=row["username"], email=row["email"])
    finally:
        conn.close()


def find_or_create_user(email: str, username: str) -> dict:
    """查找微软 SSO 用户，不存在则自动创建"""
    conn = get_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        # 先通过邮箱查找
        cursor.execute("SELECT * FROM user WHERE email = %s", (email,))
        row = cursor.fetchone()
        if row:
            return {"id": row["id"], "username": row["username"], "email": row["email"]}

        # 创建新用户，使用随机密码（无法通过密码登录）
        random_pwd = _hmac_sha256(str(uuid.uuid4()))

        # 检查用户名是否已被占用
        cursor.execute("SELECT id FROM user WHERE username = %s", (username,))
        if cursor.fetchone():
            username = f"{username}_{uuid.uuid4().hex[:6]}"

        cursor.execute(
            "INSERT INTO user (username, password, email) VALUES (%s, %s, %s)",
            (username, random_pwd, email),
        )
        conn.commit()
        return {"id": cursor.lastrowid, "username": username, "email": email}
    finally:
        conn.close()