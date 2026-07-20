import logging
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional
from app.services import auth_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    username: str
    password: str
    email: str
    invite_code: str


class LoginRequest(BaseModel):
    email: str
    password: str


@router.post("/register")
def register(body: RegisterRequest):
    logger.info(f"[注册] 收到请求: username={body.username}, email={body.email}")
    try:
        token, user = auth_service.register(body.username, body.password, body.email)
        user_map = {"id": user.id, "username": user.username, "email": user.email, "role": user.role}
        logger.info(f"[注册] 成功: username={body.username}, email={body.email}")
        return {"token": token, "message": "注册成功", "user": user_map}
    except ValueError as e:
        logger.warning(f"[注册] 失败: username={body.username}, reason={e}")
        return {"error": str(e)}


@router.post("/login")
def login(body: LoginRequest):
    logger.info(f"[登录] 收到请求: email={body.email}")
    try:
        token, user = auth_service.login(body.email, body.password)
        user_map = {"id": user.id, "username": user.username, "email": user.email, "role": user.role}
        logger.info(f"[登录] 成功: email={body.email}, user_id={user.id}, role={user.role}")
        return {"token": token, "message": "登录成功", "user": user_map}
    except ValueError as e:
        logger.warning(f"[登录] 失败: email={body.email}, reason={e}")
        return {"error": str(e)}
