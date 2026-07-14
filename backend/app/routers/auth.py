from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional
from app.services import auth_service

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
    try:
        token = auth_service.register(body.username, body.password, body.email)
        return {"token": token, "message": "注册成功"}
    except ValueError as e:
        return {"error": str(e)}


@router.post("/login")
def login(body: LoginRequest):
    try:
        token = auth_service.login(body.email, body.password)
        user = auth_service.get_user_by_token(token)
        user_map = {"id": user.id, "username": user.username, "email": user.email} if user else {}
        return {"token": token, "message": "登录成功", "user": user_map}
    except ValueError as e:
        return {"error": str(e)}