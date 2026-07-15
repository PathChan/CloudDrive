from fastapi import APIRouter
from fastapi.responses import RedirectResponse, JSONResponse
from msal import ConfidentialClientApplication
import httpx
from app.config import settings
from app.services import auth_service
from app.utils.jwt_util import create_token

router = APIRouter(prefix="/api/auth/microsoft", tags=["auth"])


def _get_msal_app() -> ConfidentialClientApplication:
    return ConfidentialClientApplication(
        client_id=settings.microsoft_client_id,
        authority=f"https://login.microsoftonline.com/{settings.microsoft_tenant_id}",
        client_credential=settings.microsoft_client_secret,
    )


@router.get("/login")
async def microsoft_login():
    """跳转到微软登录页"""
    msal_app = _get_msal_app()
    auth_url = msal_app.get_authorization_request_url(
        scopes=["User.Read"],
        redirect_uri=settings.microsoft_redirect_uri,
    )
    return RedirectResponse(auth_url)


@router.get("/callback")
async def microsoft_callback(code: str):
    """微软登录回调处理"""
    msal_app = _get_msal_app()

    # 用授权码换取 token
    result = msal_app.acquire_token_by_authorization_code(
        code=code,
        scopes=["User.Read"],
        redirect_uri=settings.microsoft_redirect_uri,
    )

    if "error" in result:
        return JSONResponse(
            {"error": result.get("error_description", "Unknown error")},
            status_code=400,
        )

    # 获取微软用户信息
    access_token = result["access_token"]
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://graph.microsoft.com/v1.0/me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        ms_user = resp.json()

    email = ms_user.get("mail") or ms_user.get("userPrincipalName", "")
    if not email:
        return JSONResponse({"error": "无法获取用户邮箱信息"}, status_code=400)

    name = ms_user.get("displayName", email.split("@")[0] if "@" in email else "User")

    # 查找或创建本地用户
    user = auth_service.find_or_create_user(email=email, username=name)

    # 签发 JWT（与现有登录逻辑一致）
    token = create_token(user_id=user["id"], username=user["username"])

    # 重定向回前端，携带 token
    frontend_url = settings.frontend_url.rstrip("/")
    return RedirectResponse(f"{frontend_url}/?token={token}")