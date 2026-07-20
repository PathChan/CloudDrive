import logging
import json
import base64
import subprocess
import platform
from fastapi import APIRouter
from pydantic import BaseModel
from ldap3 import Server, Connection, ALL, SUBTREE, NTLM, SIMPLE
from app.config import settings
from app.services import auth_service
from app.utils.jwt_util import create_token

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth/ldap", tags=["auth"])

if not settings.ldap_enabled:
    @router.post("/login")
    async def ldap_disabled():
        return {"error": "LDAP 登录未启用"}


class LdapLoginRequest(BaseModel):
    username: str
    password: str


def _create_server():
    """创建 LDAP 连接"""
    return Server(
        settings.ldap_host,
        port=settings.ldap_port,
        use_ssl=settings.ldap_use_ssl,
        get_info=ALL,
    )


def _check_group(entry, target_group_dn: str) -> bool:
    """检查用户是否属于指定 group"""
    if not hasattr(entry, settings.ldap_group_attribute):
        return False
    try:
        groups = getattr(entry, settings.ldap_group_attribute)
        if not groups:
            return False
        target_lower = target_group_dn.lower()
        for g in groups:
            if hasattr(g, 'lower'):
                if g.lower() == target_lower:
                    return True
            elif str(g).lower() == target_lower:
                return True
    except Exception as e:
        logger.warning(f"检查 group 异常: {e}")
    return False


def check_ldap_connection():
    """启动时检测 LDAP 连通性，优先 ldap3，失败则尝试 ADSI"""
    if not settings.ldap_enabled:
        print(f"[SKIP] LDAP is disabled")
        return

    print(f"[LDAP] Checking connection to {settings.ldap_host}:{settings.ldap_port} ...")

    # 尝试 ldap3
    try:
        server = _create_server()
        conn = Connection(server, user=settings.ldap_bind_user,
                          password=settings.ldap_bind_password,
                          authentication=NTLM, auto_bind=True, read_only=True)
        conn.unbind()
        print(f"[OK] LDAP (ldap3) connected ({settings.ldap_host}:{settings.ldap_port})")
        return
    except Exception as e:
        error_type = type(e).__name__
        logger.warning(f"[LDAP] ldap3 连接失败: {error_type}: {e}")

        # DNS/连接类错误时尝试 ADSI
        error_str = str(e).lower()
        if any(kw in error_str for kw in ('invalid server address', 'socket', 'dns', 'getaddrinfo', 'unreachable', 'timeout')):
            if platform.system() == "Windows":
                print(f"[LDAP] ldap3 failed (DNS/network), trying ADSI fallback ...")
                adsi_ok = _check_adsi_connection()
                if adsi_ok:
                    print(f"[OK] LDAP (ADSI) connected ({settings.ldap_host}:{settings.ldap_port})")
                    return
                else:
                    print(f"[WARN] LDAP connection failed: ldap3={error_type}, ADSI=FAILED")
                    print(f"[WARN] Please check network/VPN or LDAP configuration")
                    return
            else:
                print(f"[WARN] LDAP connection failed: {error_type}: {e}")
                return
        print(f"[WARN] LDAP connection failed: {error_type}: {e}")


def _check_adsi_connection() -> bool:
    """用 PowerShell ADSI 检测 LDAP 是否可达（仅测试服务账号绑定）"""
    def _ps_escape(s: str) -> str:
        return s.replace("'", "''")

    safe_host = _ps_escape(settings.ldap_host)
    safe_bind_user = _ps_escape(settings.ldap_bind_user)
    safe_bind_pass = _ps_escape(settings.ldap_bind_password)
    safe_search_base = _ps_escape(settings.ldap_user_search_base)

    ps_script = f"""
$ErrorActionPreference = 'Stop'
$rootPath = "LDAP://{safe_host}/{safe_search_base}"
try {{
    $entry = New-Object DirectoryServices.DirectoryEntry($rootPath, '{safe_bind_user}', '{safe_bind_pass}')
    $null = $entry.NativeObject
    Write-Output 'OK'
}} catch {{
    Write-Output "FAIL: $($_.Exception.Message)"
}}
"""
    ps_bytes = ps_script.encode('utf-16-le')
    ps_b64 = base64.b64encode(ps_bytes).decode('ascii')

    try:
        result = subprocess.run(
            ['powershell', '-NoProfile', '-NonInteractive', '-EncodedCommand', ps_b64],
            capture_output=True, text=True, timeout=10,
        )
        output = (result.stdout or '').strip()
        logger.info(f"[LDAP启动检查-ADSI] PowerShell rc={result.returncode}, output={output[:200]}")
        return output.startswith('OK')
    except Exception as e:
        logger.warning(f"[LDAP启动检查-ADSI] 异常: {e}")
        return False


def _adsi_ldap_login(username: str, password: str) -> dict | None:
    """Windows ADSI 后备方案：通过 PowerShell DirectoryEntry 完成 LDAP 认证。
    当 ldap3 因 DNS 无法解析公司内网域名时，PowerShell ADSI 可通过 NetBIOS/WINS 等其他路径连接。
    
    返回 None 表示不适合使用 ADSI（非 Windows 环境），
    返回 {"error": "..."} 表示 ADSI 认证失败，
    返回 {"email": ..., "display_name": ..., "role": ...} 表示成功。
    """
    if platform.system() != "Windows":
        logger.info("[LDAP登录-ADSI] 非 Windows 环境，跳过 ADSI 后备")
        return None

    logger.info(f"[LDAP登录-ADSI] 启动 ADSI 后备认证: username={username}")

    # 转义 PowerShell 单引号字符串中的特殊字符
    def _ps_escape(s: str) -> str:
        return s.replace("'", "''")

    safe_password = _ps_escape(password)
    safe_bind_pass = _ps_escape(settings.ldap_bind_password)
    safe_host = _ps_escape(settings.ldap_host)
    safe_bind_user = _ps_escape(settings.ldap_bind_user)
    safe_search_base = _ps_escape(settings.ldap_user_search_base)
    safe_user_filter = _ps_escape(settings.ldap_user_filter.format(username=username))
    safe_username = _ps_escape(username)

    # 从 bind_user 中提取域名前缀（如 "CORP\stjvmssa" → "CORP"），用于用户密码验证
    domain_prefix = ""
    if "\\" in settings.ldap_bind_user:
        domain_prefix = _ps_escape(settings.ldap_bind_user.split("\\")[0])
    domain_user = f"{domain_prefix}\\{safe_username}" if domain_prefix else safe_username

    safe_user_group = _ps_escape(settings.ldap_user_group_dn or '')
    safe_admin_group = _ps_escape(settings.ldap_admin_group_dn or '')

    # 构建 PowerShell 脚本
    ps_script = f"""
$ErrorActionPreference = 'Stop'

$hostName   = '{safe_host}'
$bindUser   = '{safe_bind_user}'
$bindPass   = '{safe_bind_pass}'
$searchBase = '{safe_search_base}'
$userFilter = '{safe_user_filter}'
$username   = '{safe_username}'
$domainUser = '{domain_user}'
$password   = '{safe_password}'
$userGroup  = '{safe_user_group}'
$adminGroup = '{safe_admin_group}'
$emailAttrs = @({', '.join(f"'{a.strip()}'" for a in settings.ldap_email_attribute.split(','))})
$dispAttrs  = @({', '.join(f"'{a.strip()}'" for a in settings.ldap_display_name_attribute.split(','))})

try {{
    # Step 1: 用服务账号绑定并搜索用户
    $rootPath = "LDAP://$hostName/$searchBase"

    # 创建 DirectoryEntry 但不提前连接，避免连接状态冲突
    $de = New-Object System.DirectoryServices.DirectoryEntry($rootPath, $bindUser, $bindPass)

    $searcher = New-Object System.DirectoryServices.DirectorySearcher($de)
    $searcher.Filter = $userFilter
    $searcher.SearchScope = [System.DirectoryServices.SearchScope]::Subtree
    $searcher.PageSize = 1000
    $searcher.CacheResults = $false
    $searcher.ReferralChasing = [System.DirectoryServices.ReferralChasingOption]::None
    [void]$searcher.PropertiesToLoad.Add('mail')
    [void]$searcher.PropertiesToLoad.Add('displayName')
    [void]$searcher.PropertiesToLoad.Add('userPrincipalName')
    [void]$searcher.PropertiesToLoad.Add('distinguishedName')
    [void]$searcher.PropertiesToLoad.Add('memberOf')

    $result = $searcher.FindOne()
    if (-not $result) {{
        Write-Output 'ERROR:USER_NOT_FOUND:用户不存在或无权登录'
        exit 0
    }}

    $props = $result.Properties
    $userDN = $props['distinguishedname'][0].ToString()
    $memberOf = @($props['memberof'] | ForEach-Object {{ $_.ToString() }})

    # Step 2: 检查 group（admin 优先）
    $role = $null
    if ($adminGroup -and $memberOf -contains $adminGroup) {{
        $role = 'admin'
    }} elseif ($userGroup -and $memberOf -contains $userGroup) {{
        $role = 'user'
    }} else {{
        Write-Output 'ERROR:NO_GROUP:用户不属于授权组，无权登录'
        exit 0
    }}

    # Step 3: 验证用户密码
    try {{
        $userPath = "LDAP://$hostName/$userDN"
        $userEntry = New-Object DirectoryServices.DirectoryEntry($userPath, $domainUser, $password)
        $null = $userEntry.NativeObject
    }} catch {{
        Write-Output 'ERROR:WRONG_PASSWORD:密码错误'
        exit 0
    }}

    # Step 4: 提取用户信息
    $email = ''
    foreach ($a in $emailAttrs) {{
        if ($props[$a] -and $props[$a].Count -gt 0) {{
            $email = $props[$a][0].ToString()
            break
        }}
    }}
    if (-not $email) {{ $email = "$username@corp.novocorp.net" }}

    $displayName = ''
    foreach ($a in $dispAttrs) {{
        if ($props[$a] -and $props[$a].Count -gt 0) {{
            $displayName = $props[$a][0].ToString()
            break
        }}
    }}
    if (-not $displayName) {{ $displayName = $username }}

    # 输出 JSON
    $out = [ordered]@{{
        status = 'ok'
        email = $email
        display_name = $displayName
        role = $role
    }} | ConvertTo-Json -Compress
    Write-Output $out

}} catch {{
    $errMsg = $_.Exception.Message -replace "`n", ' ' -replace "`r", ''
    Write-Output "ERROR:EXCEPTION:$errMsg"
}}
"""

    # Base64 编码避免转义和编码问题
    ps_bytes = ps_script.encode('utf-16-le')
    ps_b64 = base64.b64encode(ps_bytes).decode('ascii')

    try:
        result = subprocess.run(
            ['powershell', '-NoProfile', '-NonInteractive', '-EncodedCommand', ps_b64],
            capture_output=True, text=True, timeout=15,
        )
        output = (result.stdout or '').strip()
        stderr = (result.stderr or '').strip()

        logger.info(f"[LDAP登录-ADSI] PowerShell rc={result.returncode}, stdout_len={len(output)}")
        if stderr:
            logger.warning(f"[LDAP登录-ADSI] PowerShell stderr: {stderr[:300]}")

        if not output:
            logger.error("[LDAP登录-ADSI] PowerShell 无输出")
            return {"error": "LDAP 认证异常：无响应"}

        # 错误响应
        if output.startswith('ERROR:'):
            parts = output.split(':', 2)
            error_code = parts[1] if len(parts) > 1 else 'UNKNOWN'
            error_msg = parts[2] if len(parts) > 2 else '未知错误'
            logger.warning(f"[LDAP登录-ADSI] 认证失败: code={error_code}, msg={error_msg}")
            return {"error": error_msg}

        # 成功响应
        try:
            data = json.loads(output)
            if data.get('status') == 'ok':
                logger.info(f"[LDAP登录-ADSI] 认证成功: role={data.get('role')}, email={data.get('email')}")
                return {
                    "email": data["email"],
                    "display_name": data["display_name"],
                    "role": data["role"],
                }
        except json.JSONDecodeError:
            logger.error(f"[LDAP登录-ADSI] JSON 解析失败: output={output[:300]}")

        return {"error": "LDAP ADSI 认证异常"}

    except subprocess.TimeoutExpired:
        logger.error("[LDAP登录-ADSI] PowerShell 超时（15秒）")
        return {"error": "LDAP 认证超时，请重试"}
    except FileNotFoundError:
        logger.error("[LDAP登录-ADSI] PowerShell 不可用")
        return None
    except Exception as e:
        logger.error(f"[LDAP登录-ADSI] 异常: error_type={type(e).__name__}, error={e}")
        return {"error": f"LDAP 认证异常"}


def _try_adsi_fallback(username: str, password: str):
    """尝试 ADSI 后备认证，成功返回 (email, display_name, role)，失败返回 None"""
    result = _adsi_ldap_login(username, password)
    if result is None or "error" in result:
        return None
    return result["email"], result["display_name"], result["role"]


@router.post("/login")
async def ldap_login(req: LdapLoginRequest):
    """LDAP 登录：优先 ldap3 直连 → 失败则尝试 Windows ADSI 后备"""
    username = req.username.strip()
    password = req.password.strip()

    logger.info(f"[LDAP登录] 收到请求: username={username}, host={settings.ldap_host}:{settings.ldap_port}, ssl={settings.ldap_use_ssl}")

    if not username or not password:
        logger.warning(f"[LDAP登录] 用户名或密码为空: username={username}")
        return {"error": "用户名和密码不能为空"}

    # === Step 1: 创建 LDAP 服务器对象 ===
    try:
        server = _create_server()
        logger.info(f"[LDAP登录] Step1-服务器对象创建成功: {settings.ldap_host}:{settings.ldap_port}")
    except Exception as e:
        logger.error(f"[LDAP登录] Step1-创建服务器对象失败: host={settings.ldap_host}, error={e}")
        return {"error": f"LDAP 服务器配置错误: {e}"}

    # === Step 2: 服务账号 NTLM 绑定 + 搜索用户 ===
    conn = None
    try:
        logger.info(f"[LDAP登录] Step2-开始服务账号NTLM绑定: bind_user={settings.ldap_bind_user}")
        conn = Connection(server, user=settings.ldap_bind_user,
                          password=settings.ldap_bind_password,
                          authentication=NTLM, auto_bind=True)
        logger.info(f"[LDAP登录] Step2-服务账号NTLM绑定成功: {conn.bound}")

    except Exception as e:
        logger.error(f"[LDAP登录] Step2-服务账号绑定失败: host={settings.ldap_host}, bind_user={settings.ldap_bind_user}, error_type={type(e).__name__}, error={e}")
        if conn:
            try:
                conn.unbind()
            except Exception:
                pass

        # 如果是 DNS/连接类错误，尝试 ADSI 后备
        error_str = str(e).lower()
        if any(kw in error_str for kw in ('invalid server address', 'socket', 'dns', 'getaddrinfo', 'unreachable', 'timeout')):
            logger.info(f"[LDAP登录] ldap3 连接失败，尝试 ADSI 后备...")
            adsi_result = _try_adsi_fallback(username, password)
            if adsi_result:
                email, display_name, role = adsi_result
                logger.info(f"[LDAP登录-ADSI] 后备认证成功: username={username}, role={role}")
                return _issue_jwt(email, display_name, username, role)
            else:
                logger.error(f"[LDAP登录-ADSI] 后备认证也失败了")
                return {"error": "LDAP 服务器连接失败，请检查网络或联系管理员"}
        return {"error": "LDAP 服务器连接失败，请检查网络或联系管理员"}

    # 搜索用户
    search_filter = settings.ldap_user_filter.format(username=username)
    logger.info(f"[LDAP登录] Step2-搜索用户: base={settings.ldap_user_search_base}, filter={search_filter}")

    entry = None
    user_dn = None
    search_error = None
    try:
        conn.search(
            search_base=settings.ldap_user_search_base,
            search_filter=search_filter,
            search_scope=SUBTREE,
            attributes=[
                "mail", "displayName", "userPrincipalName",
                "sAMAccountName", "distinguishedName",
                settings.ldap_group_attribute,
            ],
        )
        result_count = len(conn.entries) if conn.entries else 0
        logger.info(f"[LDAP登录] Step2-搜索完成: 结果数={result_count}, result_code={conn.result.get('result') if conn.result else 'N/A'}")

        if conn.entries:
            entry = conn.entries[0]
            user_dn = str(entry.entry_dn)
            logger.info(f"[LDAP登录] Step2-找到用户: dn={user_dn}")
    except Exception as e:
        search_error = str(e)
        logger.error(f"[LDAP登录] Step2-搜索异常: username={username}, base={settings.ldap_user_search_base}, error_type={type(e).__name__}, error={e}")

    # 关闭服务账号连接
    try:
        conn.unbind()
        logger.info(f"[LDAP登录] Step2-服务账号连接已关闭")
    except Exception as e:
        logger.warning(f"[LDAP登录] Step2-关闭服务账号连接异常: {e}")

    if search_error:
        return {"error": f"LDAP 用户搜索失败: {search_error}"}

    if not entry or not user_dn:
        logger.warning(f"[LDAP登录] Step2-用户不存在: username={username}, filter={search_filter}")
        return {"error": "用户不存在或无权登录"}

    # === Step 3: 检查 group 成员（先在 user 组找，再到 admin 组找）===
    role = None
    group_attr = settings.ldap_group_attribute
    try:
        groups_raw = getattr(entry, group_attr) if hasattr(entry, group_attr) else []
        group_list = [str(g) for g in (groups_raw or [])]
        logger.info(f"[LDAP登录] Step3-用户组成员: username={username}, groups={group_list}")
    except Exception as e:
        logger.warning(f"[LDAP登录] Step3-读取组属性异常: username={username}, attr={group_attr}, error={e}")
        group_list = []

    # admin 优先：如果在两个组中，admin 优先
    if settings.ldap_admin_group_dn and _check_group(entry, settings.ldap_admin_group_dn):
        role = "admin"
        logger.info(f"[LDAP登录] Step3-用户属于admin组: username={username}, group={settings.ldap_admin_group_dn}")
    elif settings.ldap_user_group_dn and _check_group(entry, settings.ldap_user_group_dn):
        role = "user"
        logger.info(f"[LDAP登录] Step3-用户属于user组: username={username}, group={settings.ldap_user_group_dn}")
    else:
        logger.warning(f"[LDAP登录] Step3-用户不属于任何授权组: username={username}, user_groups={group_list}, user_group_dn={settings.ldap_user_group_dn}, admin_group_dn={settings.ldap_admin_group_dn}")
        return {"error": "用户不属于授权组，无权登录"}

    # === Step 4: 用户密码验证（显式 bind + 返回值检查）===
    user_conn = None
    try:
        logger.info(f"[LDAP登录] Step4-开始密码验证: username={username}, user_dn={user_dn}")
        user_conn = Connection(
            server,
            user=user_dn,
            password=password,
            authentication=SIMPLE,
            auto_bind=False,
            raise_exceptions=False,
        )
        open_ok = user_conn.open()
        logger.info(f"[LDAP登录] Step4-连接打开: open_result={open_ok}")

        if not open_ok:
            logger.error(f"[LDAP登录] Step4-连接打开失败: username={username}, result={user_conn.result}")
            return {"error": "LDAP 连接失败，请稍后重试"}

        bind_ok = user_conn.bind()
        bind_result = user_conn.result
        logger.info(f"[LDAP登录] Step4-绑定结果: bind_ok={bind_ok}, result_code={bind_result.get('result') if bind_result else 'N/A'}, description={bind_result.get('description') if bind_result else 'N/A'}")

        if not bind_ok:
            logger.warning(f"[LDAP登录] Step4-密码错误: username={username}, user_dn={user_dn}, result={bind_result}")
            return {"error": "密码错误"}

        logger.info(f"[LDAP登录] Step4-密码验证通过: username={username}")

    except Exception as e:
        logger.error(f"[LDAP登录] Step4-密码验证异常: username={username}, error_type={type(e).__name__}, error={e}")
        return {"error": "密码错误"}
    finally:
        if user_conn:
            try:
                user_conn.unbind()
                logger.info(f"[LDAP登录] Step4-用户验证连接已关闭")
            except Exception as e:
                logger.warning(f"[LDAP登录] Step4-关闭用户连接异常: {e}")

    # === Step 5: 提取用户信息 ===
    email = ""
    email_source = "default"
    try:
        for attr_name in settings.ldap_email_attribute.split(","):
            attr_name = attr_name.strip()
            val = getattr(entry, attr_name, None)
            if val:
                email = str(val)
                email_source = attr_name
                break
    except Exception as e:
        logger.warning(f"[LDAP登录] Step5-提取邮箱异常: {e}")
    if not email:
        email = f"{username}@corp.novocorp.net"
    logger.info(f"[LDAP登录] Step5-用户邮箱: email={email}, source={email_source}")

    display_name = ""
    display_source = "default"
    try:
        for attr_name in settings.ldap_display_name_attribute.split(","):
            attr_name = attr_name.strip()
            val = getattr(entry, attr_name, None)
            if val:
                display_name = str(val)
                display_source = attr_name
                break
    except Exception as e:
        logger.warning(f"[LDAP登录] Step5-提取显示名异常: {e}")
    if not display_name:
        display_name = username
    logger.info(f"[LDAP登录] Step5-用户显示名: display_name={display_name}, source={display_source}")

    return _issue_jwt(email, display_name, username, role)


def _issue_jwt(email: str, display_name: str, username: str, role: str):
    """签发 JWT 令牌"""
    try:
        user = auth_service.find_or_create_user(email=email, username=display_name)
        logger.info(f"[LDAP登录] Step6-本地用户: id={user['id']}, username={user['username']}, email={user['email']}")
    except Exception as e:
        logger.error(f"[LDAP登录] Step6-创建/查找本地用户失败: error={e}")
        return {"error": f"用户信息同步失败: {e}"}

    try:
        token = create_token(user_id=user["id"], username=user["username"], role=role)
        logger.info(f"[LDAP登录] Step6-JWT签发成功: user_id={user['id']}, role={role}")
    except Exception as e:
        logger.error(f"[LDAP登录] Step6-JWT签发失败: error={e}")
        return {"error": "登录凭证生成失败"}

    logger.info(f"[LDAP登录] ===== 登录成功: username={username}, role={role}, email={email} =====")
    return {
        "token": token,
        "user": {
            "id": user["id"],
            "username": user["username"] or display_name or username,
            "email": user["email"],
            "role": role,
        },
    }
