"""LDAP 登录监控日志模块

功能：
- 结构化 JSON 日志，每行一条记录
- 日志分级：INFO / WARNING / ERROR / PERF（性能告警）
- 用户名脱敏处理
- 自动日志轮转（按大小 + 按日期）
- 记录各阶段的网络参数、错误码、耗时
"""
import os
import re
import json
import time
import gzip
import shutil
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from app.config import settings

logger = logging.getLogger(__name__)

# ============================================================
# 日志轮转配置
# ============================================================
# 单文件最大 10MB
MAX_LOG_SIZE = 10 * 1024 * 1024
# 保留最近 30 天的日志
MAX_LOG_AGE_DAYS = 30
# 最多保留 10 个归档文件
MAX_ARCHIVE_FILES = 10


# ============================================================
# 操作阶段枚举
# ============================================================
class LdapPhase:
    """LDAP 登录流程的各个阶段"""
    CONNECT = "CONNECT"             # 创建服务器连接
    SERVICE_BIND = "SERVICE_BIND"   # 服务账号绑定
    SEARCH = "SEARCH"               # 搜索用户
    GROUP_CHECK = "GROUP_CHECK"     # 检查组权限
    USER_BIND = "USER_BIND"         # 用户密码验证
    INFO_EXTRACT = "INFO_EXTRACT"   # 提取用户信息
    JWT_ISSUE = "JWT_ISSUE"         # JWT 签发
    ADSI_FALLBACK = "ADSI_FALLBACK" # ADSI 后备路径
    STARTUP_CHECK = "STARTUP_CHECK" # 启动连通性检查
    OVERALL = "OVERALL"             # 整体登录结果


# 阶段定义元数据
PHASE_META = {
    LdapPhase.CONNECT: {
        "seq": 1,
        "label": "LDAP连接建立",
        "timeout_sec": 10,
        "perf_threshold_sec": 3.0,
    },
    LdapPhase.SERVICE_BIND: {
        "seq": 2,
        "label": "服务账号绑定",
        "timeout_sec": 10,
        "perf_threshold_sec": 2.0,
    },
    LdapPhase.SEARCH: {
        "seq": 3,
        "label": "用户搜索",
        "timeout_sec": 15,
        "perf_threshold_sec": 2.0,
    },
    LdapPhase.GROUP_CHECK: {
        "seq": 4,
        "label": "组成员检查",
        "timeout_sec": 5,
        "perf_threshold_sec": 0.5,
    },
    LdapPhase.USER_BIND: {
        "seq": 5,
        "label": "用户密码验证",
        "timeout_sec": 10,
        "perf_threshold_sec": 2.0,
    },
    LdapPhase.INFO_EXTRACT: {
        "seq": 6,
        "label": "用户信息提取",
        "timeout_sec": 3,
        "perf_threshold_sec": 0.3,
    },
    LdapPhase.JWT_ISSUE: {
        "seq": 7,
        "label": "JWT令牌签发",
        "timeout_sec": 5,
        "perf_threshold_sec": 1.0,
    },
    LdapPhase.ADSI_FALLBACK: {
        "seq": 99,
        "label": "ADSI后备认证",
        "timeout_sec": 15,
        "perf_threshold_sec": 5.0,
    },
}


# ============================================================
# 错误类型分类
# ============================================================
# 连接类错误（网络层）
CONNECT_ERRORS = {
    "LDAP_SERVER_DOWN": "LDAP服务器不可达",
    "LDAP_CONNECT_TIMEOUT": "LDAP连接超时",
    "LDAP_CONNECT_REFUSED": "LDAP连接被拒绝",
    "LDAP_DNS_FAILURE": "LDAP域名解析失败",
    "LDAP_NETWORK_UNREACHABLE": "LDAP网络不可达",
    "LDAP_SSL_ERROR": "LDAP SSL/TLS握手失败",
    "LDAP_SERVER_UNAVAILABLE": "LDAP服务器不可用",
}

# 认证类错误（绑定层）
BIND_ERRORS = {
    "LDAP_INVALID_CREDENTIALS": "LDAP凭据无效（服务账号）",
    "LDAP_ACCOUNT_LOCKED": "LDAP账号已锁定",
    "LDAP_ACCOUNT_DISABLED": "LDAP账号已禁用",
    "LDAP_ACCOUNT_EXPIRED": "LDAP账号已过期",
    "LDAP_PASSWORD_EXPIRED": "LDAP密码已过期",
    "LDAP_INSUFFICIENT_ACCESS": "LDAP权限不足",
    "LDAP_AUTH_METHOD_NOT_SUPPORTED": "LDAP认证方法不支持",
    "LDAP_NTLM_NEGOTIATION_FAILED": "LDAP NTLM协商失败",
    "LDAP_BIND_TIMEOUT": "LDAP绑定超时",
}

# 查询类错误
SEARCH_ERRORS = {
    "LDAP_SEARCH_BASE_NOT_FOUND": "LDAP搜索基DN不存在",
    "LDAP_SEARCH_FILTER_INVALID": "LDAP搜索过滤器语法错误",
    "LDAP_SEARCH_SIZE_LIMIT": "LDAP搜索结果超限",
    "LDAP_SEARCH_TIMEOUT": "LDAP搜索超时",
    "LDAP_SEARCH_TOO_MANY_RESULTS": "LDAP搜索返回多条结果",
    "LDAP_SEARCH_NO_RESULTS": "LDAP搜索无结果",
    "LDAP_SEARCH_UNEXPECTED_ERROR": "LDAP搜索未知异常",
}

# 用户密码验证错误
USER_BIND_ERRORS = {
    "LDAP_USER_WRONG_PASSWORD": "用户密码错误",
    "LDAP_USER_ACCOUNT_LOCKED": "用户账号已锁定",
    "LDAP_USER_ACCOUNT_DISABLED": "用户账号已禁用",
    "LDAP_USER_ACCOUNT_EXPIRED": "用户账号已过期",
    "LDAP_USER_PASSWORD_EXPIRED": "用户密码已过期",
    "LDAP_USER_MUST_CHANGE_PASSWORD": "用户必须修改密码",
    "LDAP_USER_LOGIN_HOURS": "用户不在允许的登录时间",
    "LDAP_USER_BIND_TIMEOUT": "用户密码验证超时",
}

# 权限类错误
AUTH_ERRORS = {
    "LDAP_GROUP_NOT_FOUND": "用户不属于任何授权组",
    "LDAP_GROUP_ATTRIBUTE_MISSING": "组成员属性缺失",
    "LDAP_GROUP_CHECK_FAILED": "组成员检查异常",
}

# 系统类错误
SYSTEM_ERRORS = {
    "LDAP_SERVER_CONFIG_ERROR": "LDAP服务器配置错误",
    "LDAP_POWERSHELL_UNAVAILABLE": "PowerShell不可用（ADSI依赖）",
    "LDAP_ADSI_POWERSHELL_TIMEOUT": "ADSI PowerShell执行超时",
    "LDAP_ADSI_POWERSHELL_ERROR": "ADSI PowerShell执行异常",
    "LDAP_JWT_ISSUE_FAILED": "JWT签发失败",
    "LDAP_DB_USER_SYNC_FAILED": "数据库用户同步失败",
}

# 成功代码
SUCCESS_CODES = {
    "LDAP_LOGIN_SUCCESS": "LDAP登录成功",
    "LDAP_PHASE_OK": "阶段执行成功",
}


def _classify_error(exception: Exception) -> tuple[str, str, str]:
    """根据异常类型自动分类错误。

    返回: (error_code, error_message, error_category)
    """
    error_type = type(exception).__name__
    error_str = str(exception).lower()

    # DNS / 网络不可达
    if any(kw in error_str for kw in ('invalid server address', 'getaddrinfo', 'dns', 'name or service not known')):
        return ("LDAP_DNS_FAILURE", str(exception), "connect")

    # 连接超时
    if any(kw in error_str for kw in ('timeout', 'timed out')):
        if 'connect' in error_str or 'socket' in error_str:
            return ("LDAP_CONNECT_TIMEOUT", str(exception), "connect")
        return ("LDAP_BIND_TIMEOUT", str(exception), "bind")

    # 连接拒绝
    if any(kw in error_str for kw in ('connection refused', 'connection reset', 'connection aborted')):
        return ("LDAP_CONNECT_REFUSED", str(exception), "connect")

    # 网络不可达
    if any(kw in error_str for kw in ('unreachable', 'network', 'no route')):
        return ("LDAP_NETWORK_UNREACHABLE", str(exception), "connect")

    # SSL 错误
    if any(kw in error_str for kw in ('ssl', 'tls', 'certificate', 'handshake')):
        return ("LDAP_SSL_ERROR", str(exception), "connect")

    # 凭据无效
    if any(kw in error_str for kw in ('invalidcredentials', 'invalid credentials', 'authentication failed', '80090308')):
        return ("LDAP_INVALID_CREDENTIALS", str(exception), "bind")

    # 账号锁定
    if any(kw in error_str for kw in ('account locked', 'locked out', '775')):
        return ("LDAP_ACCOUNT_LOCKED", str(exception), "bind")

    # 账号禁用
    if any(kw in error_str for kw in ('account disabled', 'disabled', '533')):
        return ("LDAP_ACCOUNT_DISABLED", str(exception), "bind")

    # 权限不足
    if any(kw in error_str for kw in ('insufficient', 'not authorized', 'no access', 'insufficientaccessrights')):
        return ("LDAP_INSUFFICIENT_ACCESS", str(exception), "bind")

    # 默认：未分类异常
    return ("LDAP_UNCLASSIFIED_ERROR", str(exception), "system")


def _mask_username(username: str) -> str:
    """对用户名进行脱敏处理。

    规则：
    - 长度 <= 3: 只保留首字符，其余用 * 替换
    - 长度 4-6: 保留首尾字符，中间用 * 替换
    - 长度 > 6: 保留首2位和尾2位，中间用 * 替换
    """
    if not username:
        return "***"
    n = len(username)
    if n <= 3:
        return username[0] + "*" * (n - 1)
    if n <= 6:
        return username[0] + "*" * (n - 2) + username[-1]
    return username[:2] + "*" * (n - 4) + username[-2:]


def _mask_dn(dn: str) -> str:
    """对 DN 中的敏感部分进行脱敏（保留 OU/DC 结构，隐藏 CN 用户名部分）"""
    if not dn:
        return dn
    # 替换 CN=xxx 中的值为脱敏
    return re.sub(r'(?i)CN=([^,]+)', lambda m: f"CN={_mask_username(m.group(1))}", dn)


# ============================================================
# 日志记录上下文
# ============================================================
class LdapLogEntry:
    """单条 LDAP 监控日志条目"""

    __slots__ = (
        "timestamp", "level", "phase", "phase_seq", "phase_label",
        "username_masked", "error_code", "error_message", "error_category",
        "duration_ms", "over_threshold", "network_params", "result",
        "details",
    )

    def __init__(
        self,
        phase: str,
        username: str = "",
        *,
        level: str = "INFO",
        error_code: str = "",
        error_message: str = "",
        error_category: str = "",
        duration_ms: float = 0,
        network_params: Optional[dict] = None,
        result: str = "",
        details: Optional[dict] = None,
    ):
        meta = PHASE_META.get(phase, {})
        self.timestamp = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        self.level = level
        self.phase = phase
        self.phase_seq = meta.get("seq", 0)
        self.phase_label = meta.get("label", phase)
        self.username_masked = _mask_username(username)
        self.error_code = error_code
        self.error_message = error_message
        self.error_category = error_category
        self.duration_ms = round(duration_ms, 2)
        self.over_threshold = False
        if meta.get("perf_threshold_sec") and duration_ms > 0:
            self.over_threshold = (duration_ms / 1000) > meta["perf_threshold_sec"]
        self.network_params = network_params or {}
        self.result = result
        self.details = details or {}

    def to_dict(self) -> dict:
        d = {
            "ts": self.timestamp,
            "level": self.level,
            "phase": self.phase,
            "phase_seq": self.phase_seq,
            "phase_label": self.phase_label,
            "user": self.username_masked,
            "duration_ms": self.duration_ms,
        }
        if self.error_code:
            d["error_code"] = self.error_code
            d["error_message"] = self.error_message
            d["error_category"] = self.error_category
        if self.over_threshold:
            d["perf_alert"] = True
        if self.network_params:
            d["network"] = self.network_params
        if self.result:
            d["result"] = self.result
        if self.details:
            d["details"] = self.details
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, default=str)


# ============================================================
# 日志文件管理（轮转 + 写入）
# ============================================================
class LdapLogWriter:
    """负责 JSON 日志文件的写入、轮转和清理"""

    def __init__(self, log_dir: str = "logs/ldap"):
        self._log_dir = Path(log_dir)
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._current_date = datetime.utcnow().strftime("%Y%m%d")
        self._file_index = self._find_latest_index()
        self._current_path = self._build_path()
        self._bytes_written = self._current_path.stat().st_size if self._current_path.exists() else 0
        self._lock = __import__('threading').Lock()

    @property
    def log_dir(self) -> str:
        return str(self._log_dir)

    def _build_path(self, date_str: str = None, index: int = None) -> Path:
        date_str = date_str or self._current_date
        index = index if index is not None else self._file_index
        return self._log_dir / f"ldap_auth_{date_str}_{index:03d}.jsonl"

    def _find_latest_index(self) -> int:
        prefix = f"ldap_auth_{self._current_date}_"
        existing = sorted(
            [f for f in os.listdir(self._log_dir) if f.startswith(prefix) and f.endswith(".jsonl")],
        )
        if existing:
            last = existing[-1]
            try:
                return int(last[len(prefix):-6])
            except ValueError:
                return 0
        return 0

    def _rotate_if_needed(self):
        """检查是否需要轮转（日期变化 或 文件超出大小限制）"""
        today = datetime.utcnow().strftime("%Y%m%d")

        # 日期变更 → 重置索引
        if today != self._current_date:
            self._current_date = today
            self._file_index = self._find_latest_index()
            self._current_path = self._build_path()
            self._bytes_written = self._current_path.stat().st_size if self._current_path.exists() else 0
            return

        # 大小超限 → 递增索引
        if self._bytes_written >= MAX_LOG_SIZE:
            self._file_index += 1
            self._current_path = self._build_path()
            self._bytes_written = self._current_path.stat().st_size if self._current_path.exists() else 0

    def _cleanup_old_logs(self):
        """清理超过保留期限的日志文件"""
        cutoff = datetime.utcnow() - timedelta(days=MAX_LOG_AGE_DAYS)
        try:
            for f in self._log_dir.glob("ldap_auth_*.jsonl"):
                try:
                    mtime = datetime.fromtimestamp(f.stat().st_mtime)
                    if mtime < cutoff:
                        f.unlink()
                        logger.debug(f"[LDAP日志] 清理过期日志: {f.name}")
                except Exception:
                    pass
        except Exception:
            pass

    def _compress_old_logs(self):
        """压缩非当天的日志文件以节省空间"""
        today = datetime.utcnow().strftime("%Y%m%d")
        try:
            for f in self._log_dir.glob("ldap_auth_*.jsonl"):
                if today not in f.name:
                    gz_path = f.with_suffix(".jsonl.gz")
                    if not gz_path.exists():
                        with open(f, "rb") as fin, gzip.open(gz_path, "wb") as fout:
                            shutil.copyfileobj(fin, fout)
                        f.unlink()
                        logger.debug(f"[LDAP日志] 压缩日志: {f.name} -> {gz_path.name}")
        except Exception:
            pass

    def write(self, entry: LdapLogEntry):
        """写入一条日志记录"""
        with self._lock:
            self._rotate_if_needed()
            line = entry.to_json() + "\n"
            line_bytes = line.encode("utf-8")
            with open(self._current_path, "ab") as f:
                f.write(line_bytes)
            self._bytes_written += len(line_bytes)
            # 每 100 条日志触发一次清理
            if self._bytes_written % (100 * 200) < 200:  # ~200 bytes per line
                self._cleanup_old_logs()


# ============================================================
# 全局单例
# ============================================================
_writer: Optional[LdapLogWriter] = None


def get_ldap_log_writer() -> LdapLogWriter:
    global _writer
    if _writer is None:
        _writer = LdapLogWriter()
    return _writer


# ============================================================
# 便捷 API
# ============================================================
class LdapLoginTracer:
    """LDAP 登录流程跟踪器 —— 在一次登录请求中跟踪所有阶段。

    用法:
        tracer = LdapLoginTracer("testuser")
        with tracer.phase(LdapPhase.CONNECT):
            server = _create_server()
        with tracer.phase(LdapPhase.SERVICE_BIND):
            conn = Connection(...)
        # ... 更多阶段 ...
        tracer.set_result("success")
        # 最后一行会自动标记 overall
    """

    def __init__(self, username: str, writer: Optional[LdapLogWriter] = None):
        self.username = username
        self.writer = writer or get_ldap_log_writer()
        self._start_time = time.time()
        self._network_params = {
            "host": settings.ldap_host,
            "port": settings.ldap_port,
            "use_ssl": settings.ldap_use_ssl,
            "bind_user_masked": _mask_username(settings.ldap_bind_user),
            "search_base": settings.ldap_user_search_base,
        }
        self._phases = []  # 记录每个阶段的耗时
        self._final_result = ""
        self._logged_overall = False

    class PhaseContext:
        """阶段上下文管理器，自动记录耗时"""
        def __init__(self, tracer: "LdapLoginTracer", phase: str):
            self.tracer = tracer
            self.phase = phase
            self.start = 0.0
            self.duration_ms = 0.0

        def __enter__(self):
            self.start = time.time()
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            self.duration_ms = (time.time() - self.start) * 1000
            entry = LdapLogEntry(
                phase=self.phase,
                username=self.tracer.username,
                level="INFO" if exc_type is None else "ERROR",
                duration_ms=self.duration_ms,
                network_params=self.tracer._network_params,
            )
            if exc_type is not None and exc_val is not None:
                error_code, error_message, error_category = _classify_error(exc_val)
                entry.level = "ERROR"
                entry.error_code = error_code
                entry.error_message = error_message
                entry.error_category = error_category
                entry.result = "error"
            else:
                entry.error_code = "LDAP_PHASE_OK"
                entry.error_message = "阶段执行成功"
                entry.error_category = "success"
                entry.result = "ok"

            # 性能告警
            if entry.over_threshold and entry.level == "INFO":
                entry.level = "PERF"

            self.tracer.writer.write(entry)
            self.tracer._phases.append((self.phase, self.duration_ms, entry.level != "ERROR"))
            return False  # 不抑制异常

    def phase(self, name: str) -> PhaseContext:
        return self.PhaseContext(self, name)

    def log_event(
        self,
        phase: str,
        *,
        level: str = "INFO",
        error_code: str = "",
        error_message: str = "",
        error_category: str = "",
        duration_ms: float = 0,
        result: str = "",
        details: Optional[dict] = None,
    ):
        """记录一个阶段事件（不自动计时，手动指定参数）"""
        entry = LdapLogEntry(
            phase=phase,
            username=self.username,
            level=level,
            error_code=error_code,
            error_message=error_message,
            error_category=error_category,
            duration_ms=duration_ms,
            network_params=self._network_params,
            result=result,
            details=details,
        )
        if entry.over_threshold and level == "INFO":
            entry.level = "PERF"
        self.writer.write(entry)

    def log_exception(
        self,
        phase: str,
        exception: Exception,
        duration_ms: float = 0,
    ):
        """记录一个阶段的异常"""
        error_code, error_message, error_category = _classify_error(exception)
        entry = LdapLogEntry(
            phase=phase,
            username=self.username,
            level="ERROR",
            error_code=error_code,
            error_message=error_message,
            error_category=error_category,
            duration_ms=duration_ms,
            network_params=self._network_params,
            result="error",
            details={"exception_type": type(exception).__name__},
        )
        self.writer.write(entry)

    def set_result(self, result: str, details: Optional[dict] = None):
        """设置最终登录结果，生成 OVERALL 汇总日志"""
        self._final_result = result
        total_ms = (time.time() - self._start_time) * 1000

        # 汇总每个阶段的耗时
        phase_summary = {}
        for p, d, ok in self._phases:
            phase_summary[p] = {"duration_ms": round(d, 2), "ok": ok}

        overall_entry = LdapLogEntry(
            phase=LdapPhase.OVERALL,
            username=self.username,
            level="INFO" if result == "success" else "ERROR",
            duration_ms=total_ms,
            network_params=self._network_params,
            result=result,
            details={
                **(details or {}),
                "phase_count": len(self._phases),
                "phase_timings": phase_summary,
            },
        )
        self.writer.write(overall_entry)
        self._logged_overall = True

    def __del__(self):
        # 确保 OVERALL 日志一定被记录（即使流程异常退出）
        if not getattr(self, '_logged_overall', True):
            try:
                self.set_result(getattr(self, '_final_result', 'incomplete'))
            except Exception:
                pass


# ============================================================
# 日志查看工具
# ============================================================
def tail_logs(lines: int = 20) -> list[dict]:
    """读取最近 N 条 LDAP 登录日志"""
    w = get_ldap_log_writer()
    log_files = sorted(w._log_dir.glob("ldap_auth_*.jsonl"), reverse=True)
    entries = []
    for f in log_files:
        if f.suffix == ".jsonl":
            with open(f, "rb") as fh:
                raw = fh.read()
            for line in raw.decode("utf-8").strip().split("\n"):
                if line.strip():
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
            if len(entries) >= lines:
                break
    return entries[-lines:]


def get_error_summary(hours: int = 24) -> dict:
    """获取最近 N 小时的错误摘要统计"""
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    w = get_ldap_log_writer()

    summary = {
        "total_entries": 0,
        "total_errors": 0,
        "total_warnings": 0,
        "total_perf_alerts": 0,
        "total_success": 0,
        "errors_by_phase": {},
        "errors_by_code": {},
        "users_affected": set(),
        "avg_duration_ms_overall": 0,
    }

    overall_count = 0
    total_duration = 0

    for f in sorted(w._log_dir.glob("ldap_auth_*.jsonl")):
        if f.suffix != ".jsonl":
            continue
        try:
            mtime = datetime.fromtimestamp(f.stat().st_mtime)
            # 粗略过滤：按文件修改时间
            if mtime < cutoff - timedelta(days=1):
                continue
        except Exception:
            pass

        with open(f, "rb") as fh:
            for line in fh.read().decode("utf-8").split("\n"):
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                ts_str = entry.get("ts", "")
                try:
                    entry_time = datetime.strptime(ts_str[:19], "%Y-%m-%dT%H:%M:%S")
                    if entry_time < cutoff:
                        continue
                except (ValueError, TypeError):
                    pass

                summary["total_entries"] += 1
                level = entry.get("level", "")
                phase = entry.get("phase", "")

                if level == "ERROR":
                    summary["total_errors"] += 1
                    summary["errors_by_phase"][phase] = summary["errors_by_phase"].get(phase, 0) + 1
                    code = entry.get("error_code", "UNKNOWN")
                    summary["errors_by_code"][code] = summary["errors_by_code"].get(code, 0) + 1
                    user = entry.get("user", "")
                    if user:
                        summary["users_affected"].add(user)
                elif level == "WARNING":
                    summary["total_warnings"] += 1
                elif level == "PERF":
                    summary["total_perf_alerts"] += 1

                if phase == LdapPhase.OVERALL and entry.get("result") == "success":
                    summary["total_success"] += 1

                if phase == LdapPhase.OVERALL:
                    dur = entry.get("duration_ms", 0)
                    if dur > 0:
                        total_duration += dur
                        overall_count += 1

    if overall_count > 0:
        summary["avg_duration_ms_overall"] = round(total_duration / overall_count, 2)

    summary["users_affected"] = list(summary["users_affected"])
    return summary
