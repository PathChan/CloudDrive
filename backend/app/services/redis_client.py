"""Redis 缓存服务层 —— 持久化全量目录结构、面包屑、收藏 ID 等

设计原则：
- 持久化键永不过期（`cache_set_persist`），系统重启后自动从 DB 重建
- 缓存键有过期时间（`cache_set`），用于临时高频读数据
- 写入时同步更新（写穿透），删除时同步失效
- 提供全量重建（rebuild_all_cache）和一致性校验（verify_cache_consistency）
"""

import json
import logging
from typing import Optional, Any, Callable

import redis as redis_lib

from app.config import settings

logger = logging.getLogger(__name__)

# ---------- 单例连接 ----------

_client: Optional[redis_lib.Redis] = None
CACHE_TTL = 300  # 5 分钟（秒），仅用于临时缓存键
PERSIST_TTL = 0   # 0 = 永不过期（持久化键）


def get_redis() -> Optional[redis_lib.Redis]:
    """获取 Redis 连接（惰性初始化，连接失败返回 None）"""
    global _client
    if _client is not None:
        try:
            _client.ping()
            return _client
        except Exception:
            _client = None
    try:
        _client = redis_lib.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            socket_connect_timeout=2,
            socket_timeout=2,
            decode_responses=True,
        )
        _client.ping()
        return _client
    except Exception as e:
        logger.warning(f"Redis 连接失败，跳过缓存: {e}")
        return None


def close_redis():
    global _client
    if _client:
        try:
            _client.close()
        except Exception:
            pass
        _client = None


# ---------- 缓存键定义 ----------

def _key_folder_content(user_id: int, folder_id: int) -> str:
    return f"fc:{user_id}:{folder_id}"


def _key_breadcrumb(folder_id: int) -> str:
    return f"bc:{folder_id}"


def _key_folder_info(folder_id: int) -> str:
    return f"fi:{folder_id}"


def _key_file_info(file_id: int) -> str:
    return f"fi2:{file_id}"


def _key_favorite_ids(user_id: int) -> str:
    return f"fav:{user_id}"


def _key_total_size() -> str:
    return "ts"


def _key_trash(user_id: int) -> str:
    return f"trash:{user_id}"


# 所有持久化键的前缀集合，用于一致性校验
_PERSIST_KEY_PREFIXES = {"fc:", "bc:", "fi:", "fi2:", "fav:", "ts", "trash:"}


# ---------- 通用缓存操作 ----------

def cache_get(key: str) -> Optional[Any]:
    """从缓存读取 JSON 数据，解析失败或未命中返回 None"""
    r = get_redis()
    if r is None:
        return None
    try:
        raw = r.get(key)
        if raw is None:
            return None
        return json.loads(raw)
    except Exception as e:
        logger.debug(f"缓存读取失败 {key}: {e}")
        return None


def cache_set(key: str, value: Any, ttl: int = CACHE_TTL):
    """将数据以 JSON 写入缓存，设置过期时间"""
    r = get_redis()
    if r is None:
        return
    try:
        r.setex(key, ttl, json.dumps(value, ensure_ascii=False, default=str))
    except Exception as e:
        logger.debug(f"缓存写入失败 {key}: {e}")


def cache_set_persist(key: str, value: Any):
    """将数据以 JSON 写入 Redis，**永不过期**（持久化存储）"""
    r = get_redis()
    if r is None:
        return
    try:
        r.set(key, json.dumps(value, ensure_ascii=False, default=str))
    except Exception as e:
        logger.debug(f"缓存持久化写入失败 {key}: {e}")


def cache_delete(*keys: str):
    """删除一个或多个缓存键"""
    r = get_redis()
    if r is None or not keys:
        return
    try:
        r.delete(*keys)
    except Exception as e:
        logger.debug(f"缓存删除失败: {e}")


# ---------- 专用于持久化写入的包装 ----------
# 每个 set_cached_* 的持久化版本，供 rebuild_all_cache 使用

def set_persist_folder_content(user_id: int, folder_id: int, data: dict):
    cache_set_persist(_key_folder_content(user_id, folder_id), data)


def set_persist_breadcrumb(folder_id: int, data: list):
    cache_set_persist(_key_breadcrumb(folder_id), data)


def set_persist_folder_info(folder_id: int, data: dict):
    cache_set_persist(_key_folder_info(folder_id), data)


def set_persist_file_info(file_id: int, data: dict):
    cache_set_persist(_key_file_info(file_id), data)


def set_persist_favorite_ids(user_id: int, data: set):
    cache_set_persist(_key_favorite_ids(user_id), list(data))


def set_persist_total_size(size: int):
    cache_set_persist(_key_total_size(), size)


def set_persist_trash(user_id: int, data: list):
    cache_set_persist(_key_trash(user_id), data)


# ---------- 专用缓存操作 ----------

def get_cached_folder_content(user_id: int, folder_id: int) -> Optional[dict]:
    return cache_get(_key_folder_content(user_id, folder_id))


def set_cached_folder_content(user_id: int, folder_id: int, data: dict):
    cache_set_persist(_key_folder_content(user_id, folder_id), data)


def invalidate_folder_content(user_id: int, folder_id: int):
    cache_delete(_key_folder_content(user_id, folder_id))


def invalidate_folder_content_multi(user_id: int, *folder_ids: int):
    keys = [_key_folder_content(user_id, fid) for fid in folder_ids if fid is not None]
    if keys:
        cache_delete(*keys)


def get_cached_breadcrumb(folder_id: int) -> Optional[list]:
    return cache_get(_key_breadcrumb(folder_id))


def set_cached_breadcrumb(folder_id: int, data: list):
    cache_set_persist(_key_breadcrumb(folder_id), data)


def invalidate_breadcrumb(folder_id: int):
    cache_delete(_key_breadcrumb(folder_id))


def invalidate_breadcrumb_ancestors(folder_id: int):
    """级联失效面包屑（当前文件夹 + 所有祖先链）
    
    当文件夹被重命名、移动或删除时，其自身及所有祖先的面包屑都会发生变化。
    通过查询 parent_id 链遍历所有祖先，逐一失效其缓存。
    如果数据库查询失败，至少保证当前目录的缓存被删除。
    """
    keys_to_delete = [_key_breadcrumb(folder_id)]
    
    # 尝试通过 parent_id 链查询所有祖先
    try:
        from app.database import get_connection
        conn = get_connection()
        try:
            cursor = conn.cursor()
            current_id = folder_id
            max_depth = 50
            while current_id and max_depth > 0:
                cursor.execute(
                    "SELECT parent_id FROM `folder` WHERE id = %s",
                    (current_id,),
                )
                row = cursor.fetchone()
                if row is None:
                    break
                parent_id = row[0]
                if parent_id and parent_id != 0:
                    keys_to_delete.append(_key_breadcrumb(parent_id))
                current_id = parent_id
                max_depth -= 1
        finally:
            conn.close()
    except Exception:
        pass  # 至少确保当前目录被失效
    
    if keys_to_delete:
        cache_delete(*keys_to_delete)


def get_cached_folder_info(folder_id: int) -> Optional[dict]:
    return cache_get(_key_folder_info(folder_id))


def set_cached_folder_info(folder_id: int, data: dict):
    cache_set_persist(_key_folder_info(folder_id), data)


def invalidate_folder_info(folder_id: int):
    cache_delete(_key_folder_info(folder_id))


def get_cached_file_info(file_id: int) -> Optional[dict]:
    return cache_get(_key_file_info(file_id))


def set_cached_file_info(file_id: int, data: dict):
    cache_set_persist(_key_file_info(file_id), data)


def invalidate_file_info(file_id: int):
    cache_delete(_key_file_info(file_id))


def get_cached_favorite_ids(user_id: int) -> Optional[set]:
    raw = cache_get(_key_favorite_ids(user_id))
    if raw is None:
        return None
    return set(raw) if isinstance(raw, list) else None


def set_cached_favorite_ids(user_id: int, data: set):
    cache_set_persist(_key_favorite_ids(user_id), list(data))


def invalidate_favorite_ids(user_id: int):
    cache_delete(_key_favorite_ids(user_id))


def get_cached_total_size() -> Optional[int]:
    raw = cache_get(_key_total_size())
    return int(raw) if raw is not None else None


def set_cached_total_size(size: int):
    cache_set_persist(_key_total_size(), size)


def invalidate_total_size():
    cache_delete(_key_total_size())


def get_cached_trash(user_id: int) -> Optional[list]:
    return cache_get(_key_trash(user_id))


def set_cached_trash(user_id: int, data: list):
    cache_set_persist(_key_trash(user_id), data)


def invalidate_trash(user_id: int):
    cache_delete(_key_trash(user_id))


# ===================================================================
# 全量目录重建 & 一致性校验
# ===================================================================
# rebuild_all_cache 和 verify_cache_consistency 是全局操作，
# 它们不依赖之前的缓存状态，直接从 DB 读取后写入 Redis。
# 调用方需提供 get_db_connection 方法（通常来自 app.database）。

def rebuild_all_cache(get_db_connection: Callable):
    """从数据库读取全量数据，写入 Redis 持久化键（永不过期）

    调用时机：
      1. 系统启动时（如果 Redis 为空或指定了 --rebuild-cache）
      2. 数据异常时手动触发
      3. 定时一致性校验发现不一致时自动触发

    参数:
      get_db_connection: 返回 mysql.connector 连接的工厂函数
    """
    r = get_redis()
    if r is None:
        logger.warning("Redis 不可用，跳过全量重建")
        return {"status": "skipped", "reason": "redis_unavailable"}

    conn = get_db_connection()
    try:
        cursor = conn.cursor(dictionary=True)

        # ---------- 1. 获取所有用户 ----------
        cursor.execute("SELECT id FROM `user`")
        user_ids = [row["id"] for row in cursor.fetchall()]

        total_keys = 0
        folders_written = 0
        files_written = 0

        for uid in user_ids:
            # ---------- 2. 重建所有文件夹的层级内容 ----------
            # 读取所有非删除文件夹
            cursor.execute(
                "SELECT * FROM `folder` WHERE user_id = %s AND is_deleted = 0 ORDER BY parent_id, name",
                (uid,),
            )
            all_folders = cursor.fetchall()

            # 先缓存每个文件夹的信息
            folder_info_map = {}
            for f in all_folders:
                fid = f["id"]
                folder_info_map[fid] = f
                set_persist_folder_info(fid, f)
                total_keys += 1

            # 按 parent_id 分组写入文件夹内容缓存
            folder_by_parent: dict[int, list] = {}
            for f in all_folders:
                pid = f["parent_id"]
                folder_by_parent.setdefault(pid, []).append(f)

            # 读取所有非删除文件
            cursor.execute(
                "SELECT * FROM `file` WHERE user_id = %s AND is_deleted = 0 ORDER BY folder_id, name",
                (uid,),
            )
            all_files = cursor.fetchall()

            # 按 folder_id 分组
            file_by_folder: dict[int, list] = {}
            for f in all_files:
                fid = f["folder_id"]
                file_by_folder.setdefault(fid, []).append(f)
                # 缓存文件信息
                set_persist_file_info(f["id"], f)
                total_keys += 1
                files_written += 1

            # 为所有 parent_id（包括根 0 和所有出现的 parent_id）写入内容缓存
            all_parent_ids = set(folder_by_parent.keys()) | {0}
            for pid in all_parent_ids:
                folders_at_pid = folder_by_parent.get(pid, [])
                files_at_pid = file_by_folder.get(pid, [])
                content_data = {
                    "folders": folders_at_pid,
                    "files": files_at_pid,
                }
                set_persist_folder_content(uid, pid, content_data)
                total_keys += 1

            # ---------- 3. 面包屑 ----------
            # 为每个文件夹及其根路径生成面包屑
            for fid in folder_info_map:
                bc = _build_breadcrumb_from_map(fid, folder_info_map)
                if bc:
                    set_persist_breadcrumb(fid, bc)
                    total_keys += 1

            # ---------- 4. 收藏 ID ----------
            cursor.execute(
                "SELECT file_id FROM `favorites` WHERE user_id = %s",
                (uid,),
            )
            fav_ids = {row["file_id"] for row in cursor.fetchall()}
            set_persist_favorite_ids(uid, fav_ids)
            total_keys += 1

            # ---------- 5. 回收站 ----------
            cursor.execute(
                "SELECT * FROM `folder` WHERE user_id = %s AND is_deleted = 1",
                (uid,),
            )
            trash_folders = cursor.fetchall()
            cursor.execute(
                "SELECT * FROM `file` WHERE user_id = %s AND is_deleted = 1",
                (uid,),
            )
            trash_files = cursor.fetchall()
            if trash_folders or trash_files:
                set_persist_trash(uid, {"folders": trash_folders, "files": trash_files})
                total_keys += 1

            folders_written += len(all_folders)

        # ---------- 6. 总大小 ----------
        cursor.execute("SELECT COALESCE(SUM(size), 0) AS total FROM `file` WHERE is_deleted = 0")
        total_sz = cursor.fetchone()["total"]
        set_persist_total_size(total_sz)
        total_keys += 1

        logger.info(
            f"Redis 全量重建完成: {total_keys} 个持久化键, "
            f"{folders_written} 个文件夹, {files_written} 个文件, "
            f"{len(user_ids)} 个用户"
        )
        return {
            "status": "ok",
            "keys_written": total_keys,
            "folders": folders_written,
            "files": files_written,
            "users": len(user_ids),
        }
    except Exception as e:
        logger.error(f"Redis 全量重建失败: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}
    finally:
        conn.close()


def _build_breadcrumb_from_map(folder_id: int, folder_info_map: dict) -> list:
    """从 folder_info_map 中构建面包屑路径（根 → 当前）"""
    bc = []
    fid = folder_id
    max_depth = 50  # 防止死循环
    while fid and fid in folder_info_map and max_depth > 0:
        f = folder_info_map[fid]
        bc.insert(0, {"id": f["id"], "name": f["name"]})
        fid = f["parent_id"]
        max_depth -= 1
    return bc


def verify_cache_consistency(get_db_connection: Callable) -> dict:
    """校验 Redis 持久化数据与数据库的一致性

    返回统计字典：
      {
        "status": "consistent" | "inconsistent" | "error",
        "details": { ... }
      }
    """
    r = get_redis()
    if r is None:
        return {"status": "error", "detail": "redis_unavailable"}

    conn = get_db_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        issues = []

        # 1. 检查 Redis 中是否有持久化键
        all_keys = set(r.keys("*") or [])
        persist_keys = {k for k in all_keys
                        if any(k.startswith(p) for p in _PERSIST_KEY_PREFIXES)}

        if not persist_keys:
            return {"status": "inconsistent", "detail": "redis_empty"}

        # 2. 检查每个用户的文件夹内容缓存
        cursor.execute("SELECT id FROM `user`")
        user_ids = [row["id"] for row in cursor.fetchall()]

        for uid in user_ids:
            # 检查 fc:{uid}:0（根目录）是否存在
            root_key = _key_folder_content(uid, 0)
            if root_key not in persist_keys:
                issues.append(f"用户 {uid} 根目录缓存缺失")

            # 检查面包屑
            cursor.execute(
                "SELECT id FROM `folder` WHERE user_id = %s AND is_deleted = 0",
                (uid,),
            )
            for row in cursor.fetchall():
                bc_key = _key_breadcrumb(row["id"])
                if bc_key not in persist_keys:
                    issues.append(f"面包屑缺失: folder_id={row['id']}")

            # 检查收藏
            fav_key = _key_favorite_ids(uid)
            if fav_key not in persist_keys:
                issues.append(f"用户 {uid} 收藏缓存缺失")

            # 检查回收站
            trash_key = _key_trash(uid)
            cursor.execute(
                "SELECT 1 FROM `folder` WHERE user_id = %s AND is_deleted = 1 LIMIT 1",
                (uid,),
            )
            has_trash_folder = cursor.fetchone() is not None
            cursor.execute(
                "SELECT 1 FROM `file` WHERE user_id = %s AND is_deleted = 1 LIMIT 1",
                (uid,),
            )
            has_trash_file = cursor.fetchone() is not None
            if (has_trash_folder or has_trash_file) and trash_key not in persist_keys:
                issues.append(f"用户 {uid} 回收站缓存缺失")

        if issues:
            return {
                "status": "inconsistent",
                "issues": issues,
                "issues_count": len(issues),
            }

        return {
            "status": "consistent",
            "keys_count": len(persist_keys),
            "users": len(user_ids),
        }
    except Exception as e:
        logger.error(f"一致性校验失败: {e}", exc_info=True)
        return {"status": "error", "detail": str(e)}
    finally:
        conn.close()
