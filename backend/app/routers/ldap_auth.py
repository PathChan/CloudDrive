from fastapi import APIRouter
from pydantic import BaseModel
from ldap3 import Server, Connection, ALL, SUBTREE, NTLM
from app.config import settings
from app.services import auth_service
from app.utils.jwt_util import create_token

router = APIRouter(prefix="/api/auth/ldap", tags=["auth"])

if not settings.ldap_enabled:
    @router.post("/login")
    async def ldap_disabled():
        return {"error": "LDAP 登录未启用"}


class LdapLoginRequest(BaseModel):
    username: str
    password: str


@router.post("/login")
async def ldap_login(req: LdapLoginRequest):
    """LDAP 登录：验证用户名密码 → 查找/创建本地用户 → 返回 JWT"""
    username = req.username.strip()
    password = req.password.strip()

    if not username or not password:
        return {"error": "用户名和密码不能为空"}

    server = Server(settings.ldap_server, get_info=ALL)

    # Step 1: 用管理员账户绑定，搜索用户 DN
    try:
        conn = Connection(
            server,
            user=settings.ldap_bind_dn,
            password=settings.ldap_bind_password,
            authentication=NTLM if "NTLM" in (settings.ldap_bind_dn or "").upper() else None,
            auto_bind=True,
        )
    except Exception as e:
        return {"error": f"LDAP 服务器连接失败: {str(e)}"}

    # Step 2: 搜索用户
    search_filter = settings.ldap_user_filter.format(username)
    conn.search(
        search_base=settings.ldap_base_dn,
        search_filter=search_filter,
        search_scope=SUBTREE,
        attributes=["mail", "displayName", "userPrincipalName", "sAMAccountName"],
    )

    if not conn.entries:
        conn.unbind()
        return {"error": "用户不存在或无权登录"}

    entry = conn.entries[0]
    user_dn = entry.entry_dn
    conn.unbind()

    # Step 3: 用用户的 DN + 密码验证身份
    try:
        user_conn = Connection(server, user=user_dn, password=password, auto_bind=True)
        user_conn.unbind()
    except Exception:
        return {"error": "密码错误"}

    # Step 4: 提取用户信息
    email = ""
    if hasattr(entry, "mail") and entry.mail:
        email = str(entry.mail)
    elif hasattr(entry, "userPrincipalName") and entry.userPrincipalName:
        email = str(entry.userPrincipalName)
    else:
        email = f"{username}@novocorp.net"

    display_name = ""
    if hasattr(entry, "displayName") and entry.displayName:
        display_name = str(entry.displayName)
    else:
        display_name = username

    # Step 5: 查找或创建本地用户
    user = auth_service.find_or_create_user(email=email, username=display_name)

    # Step 6: 签发 JWT
    token = create_token(user_id=user["id"], username=user["username"])

    return {"token": token, "user": {"id": user["id"], "username": user["username"], "email": user["email"]}}
