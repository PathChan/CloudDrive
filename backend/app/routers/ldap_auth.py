import time
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
from app.services.ldap_logger import (
    LdapPhase, LdapLoginTracer, get_ldap_log_writer,
    _classify_error, _mask_username,
)
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


def _reverse_group_check(conn, user_dn: str) -> str | None:
    """反向组查询：直接查两个目标组获取 member 列表，匹配当前用户 DN。

    核心优化（对比旧方案 memberOf 遍历）：
    - 旧方案：查用户 → 拉取所有 memberOf → 逐个比对 → 数据量大，响应慢
    - 新方案：查两个目标组 → 只返回 cn + member → 数据量极小，一次查询命中
    
    参数:
        conn: 已绑定的 ldap3 Connection（服务账号）
        user_dn: 用户的 distinguishedName
    
    返回: "admin" | "user" | None
    """
    group_base = settings.ldap_group_search_base
    group_filter = settings.ldap_group_filter
    admin_cn = (settings.parse_dn_components(settings.ldap_admin_group_dn).get("cn") or "").lower()
    user_cn = (settings.parse_dn_components(settings.ldap_user_group_dn).get("cn") or "").lower()

    logger.info(
        f"[LDAP登录] Step3-反向组查询: "
        f"base={group_base}, filter={group_filter}, "
        f"checking_user_dn={user_dn[:80]}..., "
        f"admin_cn={admin_cn}, user_cn={user_cn}"
    )

    try:
        conn.search(
            search_base=group_base,
            search_filter=group_filter,
            search_scope=SUBTREE,
            attributes=["cn", "member"],
        )
        result_count = len(conn.entries) if conn.entries else 0
        logger.info(f"[LDAP登录] Step3-反向组查询完成: groups_found={result_count}")
    except Exception as e:
        logger.error(f"[LDAP登录] Step3-反向组查询异常: error={e}")
        return None

    if not conn.entries:
        logger.warning(f"[LDAP登录] Step3-反向组查询未找到目标组: filter={group_filter}")
        return None

    user_dn_lower = user_dn.lower()
    found_role = None

    for entry in conn.entries:
        cn = (str(getattr(entry, 'cn', ''))).lower()
        members = getattr(entry, 'member', []) or []
        member_count = len(members)

        logger.info(f"[LDAP登录] Step3-检查组: cn={cn}, member_count={member_count}")

        # 检查用户 DN 是否在该组的 member 列表中
        for member_dn in members:
            if str(member_dn).lower() == user_dn_lower:
                if cn == admin_cn:
                    logger.info(f"[LDAP登录] Step3-用户匹配admin组: cn={cn}")
                    return "admin"  # admin 优先，立即返回
                elif cn == user_cn:
                    found_role = "user"
                    logger.info(f"[LDAP登录] Step3-用户匹配user组: cn={cn}")
                break

    return found_role


def _check_group(entry, target_group_dn: str) -> bool:
    """检查用户是否属于指定 group（旧方案，保留兼容）"""
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

    # -- 解析并输出搜索基 DN 组件 --
    comps = settings.ldap_search_base_components
    print(f"[LDAP] Search Base DN: {comps.get('raw') or '(none)'}")
    if comps["ous"]:
        print(f"[LDAP]   OU hierarchy: {' -> '.join(comps['ous'])}")
    if comps["dcs"]:
        print(f"[LDAP]   DC domain: {'.'.join(comps['dcs'])}")
    if comps["cn"]:
        print(f"[LDAP]   CN: {comps['cn']}")

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
    safe_group_search_base = _ps_escape(settings.ldap_group_search_base or '')
    safe_group_filter = _ps_escape(settings.ldap_group_filter or '')

    # LDAPS 方案前缀：端口636 + SSL=true 时使用 LDAPS://
    ldap_scheme = "LDAPS" if (settings.ldap_use_ssl or settings.ldap_port == 636) else "LDAP"

    # 构建 PowerShell 脚本
    ps_script = f"""
$ErrorActionPreference = 'Stop'

$scheme     = '{ldap_scheme}'
$hostName   = '{safe_host}'
$bindUser   = '{safe_bind_user}'
$bindPass   = '{safe_bind_pass}'
$searchBase = '{safe_search_base}'
$userFilter = '{safe_user_filter}'
$username   = '{safe_username}'
$domainUser = '{domain_user}'
$password   = '{safe_password}'
$groupSearchBase = '{safe_group_search_base}'
$groupFilter     = '{safe_group_filter}'
$userGroupCN  = {_ps_escape(settings.parse_dn_components(settings.ldap_user_group_dn).get('cn') or '')}
$adminGroupCN = {_ps_escape(settings.parse_dn_components(settings.ldap_admin_group_dn).get('cn') or '')}
$emailAttrs = @({', '.join(f"'{a.strip()}'" for a in settings.ldap_email_attribute.split(','))})
$dispAttrs  = @({', '.join(f"'{a.strip()}'" for a in settings.ldap_display_name_attribute.split(','))})

try {{
    # Step 1: 用服务账号绑定并搜索用户（LDAPS）
    $rootPath = "$scheme://$hostName:{settings.ldap_port}/$searchBase"

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

    $result = $searcher.FindOne()
    if (-not $result) {{
        Write-Output 'ERROR:USER_NOT_FOUND:用户不存在或无权登录'
        exit 0
    }}

    $props = $result.Properties
    $userDN = $props['distinguishedname'][0].ToString()

    # Step 2: 反向组查询 —— 直接查两个目标组获取 member 列表
    if ($groupSearchBase -and $groupFilter) {{
        $groupPath = "$scheme://$hostName:{settings.ldap_port}/$groupSearchBase"
        $groupDe = New-Object System.DirectoryServices.DirectoryEntry($groupPath, $bindUser, $bindPass)

        $groupSearcher = New-Object System.DirectoryServices.DirectorySearcher($groupDe)
        $groupSearcher.Filter = $groupFilter
        $groupSearcher.SearchScope = [System.DirectoryServices.SearchScope]::Subtree
        $groupSearcher.PageSize = 1000
        $groupSearcher.CacheResults = $false
        [void]$groupSearcher.PropertiesToLoad.Add('cn')
        [void]$groupSearcher.PropertiesToLoad.Add('member')

        $role = $null
        $groupResults = $groupSearcher.FindAll()
        foreach ($gr in $groupResults) {{
            $memberList = @($gr.Properties['member'] | ForEach-Object {{ $_.ToString().ToLower() }})
            $cn = ($gr.Properties['cn'] | Select-Object -First 1) -as [string]
            if ($memberList -contains $userDN.ToLower()) {{
                if ($cn -eq $adminGroupCN) {{
                    $role = 'admin'
                    break  # admin 优先
                }} elseif ($cn -eq $userGroupCN) {{
                    $role = 'user'
                }}
            }}
        }}
        $groupDe.Dispose()

        if (-not $role) {{
            Write-Output 'ERROR:NO_GROUP:用户不属于授权组，无权登录'
            exit 0
        }}
    }} else {{
        Write-Output 'ERROR:NO_GROUP_CONFIG:组查询配置缺失'
        exit 0
    }}

    # Step 3: 验证用户密码
    try {{
        $userPath = "$scheme://$hostName:{settings.ldap_port}/$userDN"
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

    # 创建登录流程跟踪器（自动记录每个阶段的结构化日志）
    tracer = LdapLoginTracer(username)

    if not username or not password:
        tracer.log_event(LdapPhase.OVERALL, level="WARNING",
                         error_code="LDAP_EMPTY_CREDENTIALS",
                         error_message="用户名或密码为空",
                         error_category="validation",
                         result="error")
        logger.warning(f"[LDAP登录] 用户名或密码为空: username={username}")
        return {"error": "用户名和密码不能为空"}

    # === Step 1: 创建 LDAP 服务器对象 ===
    try:
        with tracer.phase(LdapPhase.CONNECT):
            server = _create_server()
        logger.info(f"[LDAP登录] Step1-服务器对象创建成功: {settings.ldap_host}:{settings.ldap_port}")
    except Exception as e:
        logger.error(f"[LDAP登录] Step1-创建服务器对象失败: host={settings.ldap_host}, error={e}")
        tracer.set_result("error", {"failed_at": "CONNECT", "reason": str(e)})
        return {"error": f"LDAP 服务器配置错误: {e}"}

    # === Step 2: 服务账号 NTLM 绑定 + 搜索用户 ===
    conn = None
    bind_exception = None
    try:
        logger.info(f"[LDAP登录] Step2-开始服务账号NTLM绑定: bind_user={settings.ldap_bind_user}")
        with tracer.phase(LdapPhase.SERVICE_BIND):
            conn = Connection(server, user=settings.ldap_bind_user,
                              password=settings.ldap_bind_password,
                              authentication=NTLM, auto_bind=True)
        logger.info(f"[LDAP登录] Step2-服务账号NTLM绑定成功: {conn.bound}")

    except Exception as e:
        bind_exception = e
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

            # ADSI 后备有独立的 tracer
            adsi_start = time.time()
            adsi_result = _try_adsi_fallback(username, password)
            adsi_duration = (time.time() - adsi_start) * 1000

            if adsi_result:
                email, display_name, role = adsi_result
                tracer.log_event(
                    LdapPhase.ADSI_FALLBACK,
                    level="INFO",
                    error_code="LDAP_PHASE_OK",
                    error_message="ADSI后备认证成功",
                    error_category="success",
                    duration_ms=adsi_duration,
                    result="ok",
                    details={"role": role},
                )
                logger.info(f"[LDAP登录-ADSI] 后备认证成功: username={username}, role={role}")
                return _issue_jwt_with_tracer(tracer, email, display_name, username, role)
            else:
                tracer.log_event(
                    LdapPhase.ADSI_FALLBACK,
                    level="ERROR",
                    error_code="LDAP_ADSI_POWERSHELL_ERROR",
                    error_message="ADSI后备认证也失败",
                    error_category="system",
                    duration_ms=adsi_duration,
                    result="error",
                )
                logger.error(f"[LDAP登录-ADSI] 后备认证也失败了")
                tracer.set_result("error", {"failed_at": "ADSI_FALLBACK", "reason": str(e)[:200]})
                return {"error": "LDAP 服务器连接失败，请检查网络或联系管理员"}

        tracer.set_result("error", {"failed_at": "SERVICE_BIND", "reason": str(e)[:200]})
        return {"error": "LDAP 服务器连接失败，请检查网络或联系管理员"}

    # 搜索用户
    search_base = settings.build_user_search_base()
    search_filter = settings.ldap_user_filter.format(username=username)

    # 解析并输出搜索基 DN 的组件，便于排查搜索范围问题
    search_comps = settings.parse_dn_components(search_base)
    logger.info(
        f"[LDAP登录] Step2-搜索用户: "
        f"base={search_base}, "
        f"filter={search_filter}, "
        f"OUs={search_comps.get('ous', [])}, "
        f"DCs={search_comps.get('dcs', [])}"
    )

    entry = None
    user_dn = None
    search_error = None
    result_count = 0
    try:
        with tracer.phase(LdapPhase.SEARCH):
            conn.search(
                search_base=search_base,
                search_filter=search_filter,
                search_scope=SUBTREE,
                attributes=[
                    "mail", "displayName", "userPrincipalName",
                    "sAMAccountName", "distinguishedName",
                    settings.ldap_group_attribute,
                ],
            )
            result_count = len(conn.entries) if conn.entries else 0
        logger.info(
            f"[LDAP登录] Step2-搜索完成: results={result_count}, "
            f"result_code={conn.result.get('result') if conn.result else 'N/A'}, "
            f"description={conn.result.get('description', 'N/A') if conn.result else 'N/A'}"
        )

        if conn.entries:
            if result_count > 1:
                logger.warning(
                    f"[LDAP登录] Step2-搜索返回多条结果 ({result_count})，取第一条。"
                    f"考虑缩小 search_base 范围。"
                )
                tracer.log_event(
                    LdapPhase.SEARCH,
                    level="WARNING",
                    error_code="LDAP_SEARCH_TOO_MANY_RESULTS",
                    error_message=f"搜索返回 {result_count} 条结果，取第一条",
                    error_category="search",
                    result="warning",
                    details={"result_count": result_count, "search_base": search_base},
                )
            entry = conn.entries[0]
            user_dn = str(entry.entry_dn)
            logger.info(f"[LDAP登录] Step2-找到用户: dn={user_dn}")
    except Exception as e:
        search_error = str(e)
        logger.error(
            f"[LDAP登录] Step2-搜索异常: "
            f"username={username}, base={search_base}, "
            f"error_type={type(e).__name__}, error={e}"
        )

    if search_error:
        tracer.set_result("error", {"failed_at": "SEARCH", "reason": search_error[:200]})
        return {"error": f"LDAP 用户搜索失败: {search_error}"}

    if not entry or not user_dn:
        tracer.log_event(
            LdapPhase.SEARCH,
            level="WARNING",
            error_code="LDAP_SEARCH_NO_RESULTS",
            error_message="未找到匹配用户",
            error_category="search",
            result="not_found",
        )
        tracer.set_result("error", {"failed_at": "SEARCH", "reason": "user_not_found"})
        logger.warning(f"[LDAP登录] Step2-用户不存在: username={username}, filter={search_filter}")
        return {"error": "用户不存在或无权登录"}

    # === Step 3: 反向组查询（直接查目标组 member 列表，无需遍历用户 memberOf）===
    gc_start = time.time()
    role = _reverse_group_check(conn, user_dn)
    gc_duration = (time.time() - gc_start) * 1000

    if role is None:
        tracer.log_event(
            LdapPhase.GROUP_CHECK,
            level="ERROR",
            error_code="LDAP_GROUP_NOT_FOUND",
            error_message="用户不属于任何授权组（反向组查询未匹配）",
            error_category="auth",
            duration_ms=gc_duration,
            result="error",
            details={
                "user_dn_masked": _mask_username(user_dn.split(",")[0].replace("CN=", "")) if user_dn else "N/A",
                "group_search_base": settings.ldap_group_search_base,
                "group_filter": settings.ldap_group_filter,
            },
        )
        tracer.set_result("error", {"failed_at": "GROUP_CHECK"})
        logger.warning(f"[LDAP登录] Step3-用户不属于任何授权组: username={username}")
        return {"error": "用户不属于授权组，无权登录"}

    tracer.log_event(
        LdapPhase.GROUP_CHECK,
        level="INFO",
        error_code="LDAP_PHASE_OK",
        error_message=f"反向组查询通过: {role}",
        error_category="success",
        duration_ms=gc_duration,
        result="ok",
        details={"role": role, "query_method": "reverse_group_lookup"},
    )
    logger.info(f"[LDAP登录] Step3-反向组查询完成: role={role}, duration={gc_duration:.0f}ms")

    # 关闭服务账号连接
    try:
        conn.unbind()
        logger.info(f"[LDAP登录] Step2-服务账号连接已关闭")
    except Exception as e:
        logger.warning(f"[LDAP登录] Step2-关闭服务账号连接异常: {e}")

    # === Step 4: 用户密码验证 ===
    user_conn = None
    bind_failed = False
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
        open_ok = False
        with tracer.phase(LdapPhase.USER_BIND):
            open_ok = user_conn.open()
            logger.info(f"[LDAP登录] Step4-连接打开: open_result={open_ok}")

            if not open_ok:
                logger.error(f"[LDAP登录] Step4-连接打开失败: username={username}, result={user_conn.result}")
                bind_failed = True
            else:
                bind_ok = user_conn.bind()
                bind_result = user_conn.result
                logger.info(f"[LDAP登录] Step4-绑定结果: bind_ok={bind_ok}, result_code={bind_result.get('result') if bind_result else 'N/A'}")
                if not bind_ok:
                    logger.warning(f"[LDAP登录] Step4-密码错误: username={username}, user_dn={user_dn}")
                    bind_failed = True

        if bind_failed:
            # 根据 LDAP result code 细化错误分类
            if user_conn and user_conn.result:
                ldap_code = user_conn.result.get("result", -1)
                ldap_desc = user_conn.result.get("description", "")
                tracer.log_event(
                    LdapPhase.USER_BIND,
                    level="ERROR",
                    error_code=_get_ldap_bind_error_code(ldap_code, ldap_desc),
                    error_message=f"LDAP code={ldap_code}: {ldap_desc}",
                    error_category="bind",
                    result="error",
                    details={"ldap_result_code": ldap_code, "ldap_description": ldap_desc},
                )
            tracer.set_result("error", {"failed_at": "USER_BIND"})
            return {"error": "密码错误"}

        logger.info(f"[LDAP登录] Step4-密码验证通过: username={username}")

    except Exception as e:
        logger.error(f"[LDAP登录] Step4-密码验证异常: username={username}, error_type={type(e).__name__}, error={e}")
        tracer.set_result("error", {"failed_at": "USER_BIND", "reason": str(e)[:200]})
        return {"error": "密码错误"}
    finally:
        if user_conn:
            try:
                user_conn.unbind()
                logger.info(f"[LDAP登录] Step4-用户验证连接已关闭")
            except Exception as e:
                logger.warning(f"[LDAP登录] Step4-关闭用户连接异常: {e}")

    # === Step 5: 提取用户信息 ===
    ie_start = time.time()
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

    ie_duration = (time.time() - ie_start) * 1000
    tracer.log_event(
        LdapPhase.INFO_EXTRACT,
        level="INFO",
        error_code="LDAP_PHASE_OK",
        error_message="用户信息提取完成",
        error_category="success",
        duration_ms=ie_duration,
        result="ok",
        details={
            "email_source": email_source,
            "display_source": display_source,
            "email_masked": _mask_username(email.split("@")[0]) + "@" + email.split("@")[-1] if "@" in email else "***",
        },
    )
    logger.info(f"[LDAP登录] Step5-用户邮箱: email={email}, source={email_source}")
    logger.info(f"[LDAP登录] Step5-用户显示名: display_name={display_name}, source={display_source}")

    return _issue_jwt_with_tracer(tracer, email, display_name, username, role)


def _get_ldap_bind_error_code(ldap_code: int, ldap_desc: str) -> str:
    """根据 LDAP result code 返回结构化错误码"""
    desc_lower = ldap_desc.lower()
    code_map = {
        49: {
            "locked": "LDAP_USER_ACCOUNT_LOCKED",
            "disabled": "LDAP_USER_ACCOUNT_DISABLED",
            "expired": "LDAP_USER_ACCOUNT_EXPIRED",
            "password expired": "LDAP_USER_PASSWORD_EXPIRED",
            "must change": "LDAP_USER_MUST_CHANGE_PASSWORD",
            "logon hours": "LDAP_USER_LOGIN_HOURS",
            "default": "LDAP_USER_WRONG_PASSWORD",
        },
        52: "LDAP_SERVER_UNAVAILABLE",
        53: "LDAP_SERVER_UNAVAILABLE",
        80: "LDAP_SERVER_UNAVAILABLE",
        85: "LDAP_SEARCH_TIMEOUT",
    }
    if ldap_code in code_map:
        if isinstance(code_map[ldap_code], dict):
            for keyword, error_code in code_map[ldap_code].items():
                if keyword in desc_lower:
                    return error_code
            return code_map[ldap_code]["default"]
        return code_map[ldap_code]
    return "LDAP_USER_WRONG_PASSWORD"


def _issue_jwt_with_tracer(tracer: LdapLoginTracer, email: str, display_name: str, username: str, role: str):
    """签发 JWT 令牌（带 trace 记录）"""
    try:
        with tracer.phase(LdapPhase.JWT_ISSUE):
            user = auth_service.find_or_create_user(email=email, username=display_name)
        logger.info(f"[LDAP登录] Step6-本地用户: id={user['id']}, username={user['username']}, email={user['email']}")
    except Exception as e:
        logger.error(f"[LDAP登录] Step6-创建/查找本地用户失败: error={e}")
        tracer.set_result("error", {"failed_at": "JWT_ISSUE", "reason": str(e)[:200]})
        return {"error": f"用户信息同步失败: {e}"}

    try:
        token = create_token(user_id=user["id"], username=user["username"], role=role)
        logger.info(f"[LDAP登录] Step6-JWT签发成功: user_id={user['id']}, role={role}")
    except Exception as e:
        logger.error(f"[LDAP登录] Step6-JWT签发失败: error={e}")
        tracer.set_result("error", {"failed_at": "JWT_ISSUE", "reason": str(e)[:200]})
        return {"error": "登录凭证生成失败"}

    tracer.set_result("success", details={
        "user_id": user["id"],
        "role": role,
    })
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


def _issue_jwt(email: str, display_name: str, username: str, role: str):
    """签发 JWT 令牌（向后兼容，无 tracer）"""
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
