import uuid
import logging
from datetime import datetime, timedelta, timezone
from io import BufferedReader
from typing import Optional
import io


logger = logging.getLogger(__name__)
from minio import Minio
from minio.error import S3Error
from app.config import settings
from app.database import get_connection
from app.models import FileItem, FolderItem, BreadcrumbItem, QuickAccessItem
from app.services.convert_service import convert_to_pdf
from app.utils.id_utils import encode_id
from app.services.redis_client import (
    get_cached_folder_content, set_cached_folder_content, invalidate_folder_content,
    get_cached_breadcrumb, set_cached_breadcrumb, invalidate_breadcrumb,
    invalidate_breadcrumb_ancestors,
    get_cached_folder_info, set_cached_folder_info, invalidate_folder_info,
    get_cached_file_info, set_cached_file_info, invalidate_file_info,
    get_cached_favorite_ids, set_cached_favorite_ids, invalidate_favorite_ids,
    get_cached_total_size, set_cached_total_size, invalidate_total_size,
    get_cached_trash, set_cached_trash, invalidate_trash,
    invalidate_folder_content_multi,
    rebuild_all_cache as _rebuild_all_cache,
    verify_cache_consistency as _verify_cache_consistency,
    repair_missing_breadcrumbs as _repair_missing_breadcrumbs,
)


def _get_minio_client() -> Minio:
    return Minio(
        settings.minio_endpoint.replace("http://", "").replace("https://", ""),
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
    )


def _ensure_bucket():
    client = _get_minio_client()
    if not client.bucket_exists(settings.minio_bucket):
        client.make_bucket(settings.minio_bucket)


def _folder_row_to_model(row: dict) -> FolderItem:
    return FolderItem(
        id=row["id"],
        user_id=row["user_id"],
        parent_id=row["parent_id"],
        name=row["name"],
        path_node=row["path_node"],
        level=row["level"],
        is_deleted=bool(row["is_deleted"]),
        create_time=row.get("create_time"),
        update_time=row.get("update_time"),
    )


def _file_row_to_model(row: dict) -> FileItem:
    return FileItem(
        id=row["id"],
        user_id=row["user_id"],
        folder_id=row["folder_id"],
        name=row["name"],
        extension=row["extension"],
        size=row["size"],
        sha256=row["sha256"],
        minio_bucket=row["minio_bucket"],
        minio_object_name=row["minio_object_name"],
        is_deleted=bool(row["is_deleted"]),
        create_time=row.get("create_time"),
        update_time=row.get("update_time"),
    )


def _get_username(user_id: int) -> Optional[str]:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT username FROM user WHERE id = %s", (user_id,))
        row = cursor.fetchone()
        return row[0] if row else None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 目录 & 文件浏览
# ---------------------------------------------------------------------------

def list_folder_content(folder_id: int = 0, user_id: int = 0) -> dict:
    """列出指定文件夹下的直属子文件夹和文件（带 Redis 缓存）"""
    # 缓存优先：有 user_id 时尝试从缓存读取
    if user_id:
        cached = get_cached_folder_content(user_id, folder_id)
        if cached is not None:
            folders = [FolderItem(**f) for f in cached.get("folders", [])]
            files = [FileItem(**f) for f in cached.get("files", [])]
            return {"folders": folders, "files": files}

    conn = get_connection()
    try:
        cursor = conn.cursor(dictionary=True)

        cursor.execute(
            "SELECT * FROM `folder` WHERE parent_id = %s AND is_deleted = 0 ORDER BY name ASC",
            (folder_id,),
        )
        folder_rows = cursor.fetchall()
        folders = [_folder_row_to_model(r) for r in folder_rows]

        cursor.execute(
            "SELECT * FROM `file` WHERE folder_id = %s AND is_deleted = 0 ORDER BY name ASC",
            (folder_id,),
        )
        file_rows = cursor.fetchall()
        files = [_file_row_to_model(r) for r in file_rows]

        result = {"folders": folders, "files": files}

        # 写入缓存（仅在有 user_id 时缓存序列化后的 dict 数据）
        if user_id:
            try:
                set_cached_folder_content(user_id, folder_id, {
                    "folders": [f.model_dump() for f in folders],
                    "files": [f.model_dump() for f in files],
                })
            except Exception:
                pass

        return result
    finally:
        conn.close()


def get_total_size() -> int:
    cached = get_cached_total_size()
    if cached is not None:
        return cached
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COALESCE(SUM(size), 0) FROM `file` WHERE is_deleted = 0",
        )
        total = int(cursor.fetchone()[0])
        set_cached_total_size(total)
        return total
    finally:
        conn.close()


def get_file_by_id(file_id: int) -> Optional[FileItem]:
    cached = get_cached_file_info(file_id)
    if cached is not None:
        return FileItem(**cached)
    conn = get_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT * FROM `file` WHERE id = %s AND is_deleted = 0",
            (file_id,),
        )
        row = cursor.fetchone()
        model = _file_row_to_model(row) if row else None
        if model:
            set_cached_file_info(file_id, model.model_dump())
        return model
    finally:
        conn.close()


def get_folder_by_id(folder_id: int) -> Optional[FolderItem]:
    cached = get_cached_folder_info(folder_id)
    if cached is not None:
        return FolderItem(**cached)
    conn = get_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT * FROM `folder` WHERE id = %s AND is_deleted = 0",
            (folder_id,),
        )
        row = cursor.fetchone()
        model = _folder_row_to_model(row) if row else None
        if model:
            set_cached_folder_info(folder_id, model.model_dump())
        return model
    finally:
        conn.close()


def get_by_id_include_deleted(file_id: int) -> Optional[FileItem]:
    conn = get_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT * FROM `file` WHERE id = %s",
            (file_id,),
        )
        row = cursor.fetchone()
        return _file_row_to_model(row) if row else None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 创建
# ---------------------------------------------------------------------------

def check_name_conflict(name: str, parent_id: int, exclude_id: int = 0, exclude_type: str = "") -> bool:
    """检查同一父目录下是否存在同名条目（同时检查folder和file表）
    支持排除自身（重命名时使用），区分大小写（MySQL utf8mb4 默认区分大小写）
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()
        # 检查文件夹
        if exclude_type != "folder":
            cursor.execute(
                "SELECT 1 FROM `folder` WHERE parent_id = %s AND name = %s AND is_deleted = 0 AND id != %s",
                (parent_id, name, exclude_id),
            )
            if cursor.fetchone() is not None:
                return True
        # 检查文件
        if exclude_type != "file":
            cursor.execute(
                "SELECT 1 FROM `file` WHERE folder_id = %s AND name = %s AND is_deleted = 0 AND id != %s",
                (parent_id, name, exclude_id),
            )
            if cursor.fetchone() is not None:
                return True
        return False
    finally:
        conn.close()


def find_folder_by_name(name: str, parent_id: int) -> Optional[int]:
    """查询指定父目录下是否有同名且未删除的文件夹，返回其ID或None"""
    conn = get_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT id FROM `folder` WHERE parent_id = %s AND name = %s AND is_deleted = 0",
            (parent_id, name.strip()),
        )
        row = cursor.fetchone()
        return row["id"] if row else None
    finally:
        conn.close()


def create_folder(user_id: int, name: str, parent_id: int) -> int:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        if parent_id == 0:
            level = 1
        else:
            cursor.execute(
                "SELECT path_node, level FROM `folder` WHERE id = %s AND is_deleted = 0",
                (parent_id,),
            )
            parent = cursor.fetchone()
            if parent is None:
                raise ValueError("父文件夹不存在")
            parent_path_node = parent[0]
            parent_level = parent[1]
            level = parent_level + 1

        # 同名检查（排除已删除的）
        if check_name_conflict(name, parent_id):
            raise ValueError(f"该目录下已存在同名条目「{name}」")

        cursor.execute(
            "INSERT INTO `folder` (user_id, parent_id, name, path_node, level) VALUES (%s, %s, %s, '', %s)",
            (user_id, parent_id, name, level),
        )
        new_id = cursor.lastrowid

        # 正确构造 path_node：包含完整祖先链 + 自身ID
        # 格式如 ",1,5,12," — 祖先ID列表 + 自身ID，逗号前后包裹
        if parent_id == 0:
            # 根目录文件夹：path_node 应包含自身ID
            actual_path_node = f",{new_id},"
        else:
            # 子文件夹：父级 path_node + 新ID + 逗号
            # 注意：父级 path_node 格式应为 ",ancestor1,ancestor2,...,parent_id,"
            # 直接拼接 new_id 即可得到 ",ancestor1,ancestor2,...,parent_id,new_id,"
            if parent_path_node.endswith(","):
                actual_path_node = parent_path_node + str(new_id) + ","
            else:
                # 兼容父级 path_node 格式异常的情况
                actual_path_node = parent_path_node + "," + str(new_id) + ","

        cursor.execute(
            "UPDATE `folder` SET path_node = %s WHERE id = %s",
            (actual_path_node, new_id),
        )
        conn.commit()

        # 失效父目录缓存
        invalidate_folder_content(user_id, parent_id)

        return new_id
    finally:
        conn.close()


def create_file(
    user_id: int,
    folder_id: int,
    name: str,
    extension: str,
    size: int,
    sha256: str,
    minio_bucket: str,
    minio_object_name: str,
) -> int:
    if check_name_conflict(name, folder_id):
        raise ValueError(f"同一目录下已存在名为「{name}」的文件夹或文件")
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO `file` (user_id, folder_id, name, extension, size, sha256, minio_bucket, minio_object_name) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (user_id, folder_id, name, extension, size, sha256, minio_bucket, minio_object_name),
        )
        conn.commit()
        new_id = cursor.lastrowid
        # 失效相关缓存
        invalidate_folder_content(user_id, folder_id)
        invalidate_total_size()
        return new_id
    finally:
        conn.close()


def ensure_folder_path(path_parts: list[str], parent_id: int, user_id: int) -> int:
    """递归确保路径文件夹存在，返回最深层的文件夹ID
    path_parts: ['文件夹1', '文件夹2', ...] 从父到子
    """
    current_parent = parent_id
    for folder_name in path_parts:
        if not folder_name.strip():
            continue
        # 检查当前层级是否已有同名文件夹
        conn = get_connection()
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute(
                "SELECT id FROM `folder` WHERE parent_id = %s AND name = %s AND is_deleted = 0",
                (current_parent, folder_name.strip()),
            )
            existing = cursor.fetchone()
            if existing:
                current_parent = existing["id"]
                continue
        finally:
            conn.close()
        # 不存在则创建
        try:
            current_parent = create_folder(user_id, folder_name.strip(), current_parent)
        except ValueError:
            # 并发场景：刚被其他进程创建了，再查一次
            conn = get_connection()
            try:
                cursor = conn.cursor(dictionary=True)
                cursor.execute(
                    "SELECT id FROM `folder` WHERE parent_id = %s AND name = %s AND is_deleted = 0",
                    (current_parent, folder_name.strip()),
                )
                existing = cursor.fetchone()
                if existing:
                    current_parent = existing["id"]
                else:
                    raise
            finally:
                conn.close()
    return current_parent


# ---------------------------------------------------------------------------
# 重命名
# ---------------------------------------------------------------------------

def rename_folder(folder_id: int, name: str, caller_user_id: int = 0):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        # 获取当前 parent_id
        cursor.execute(
            "SELECT parent_id, user_id FROM `folder` WHERE id = %s AND is_deleted = 0",
            (folder_id,),
        )
        row = cursor.fetchone()
        if row is None:
            raise ValueError("文件夹不存在")
        parent_id = row[0]
        if check_name_conflict(name, parent_id, exclude_id=folder_id, exclude_type="folder"):
            raise ValueError(f"同一目录下已存在名为「{name}」的文件夹或文件")
        cursor.execute(
            "UPDATE `folder` SET name = %s WHERE id = %s AND is_deleted = 0",
            (name, folder_id),
        )
        conn.commit()
        # 失效相关缓存（使用调用者的 user_id 而非文件夹拥有者的，确保 cache key 一致）
        uid = caller_user_id or row[1]
        invalidate_folder_content(uid, parent_id)
        invalidate_folder_info(folder_id)
        invalidate_breadcrumb_ancestors(folder_id)
    finally:
        conn.close()


def rename_file(file_id: int, name: str, caller_user_id: int = 0):
    """重命名文件，同时更新 extension"""
    ext = ""
    dot_idx = name.rfind(".")
    if dot_idx >= 0:
        ext = name[dot_idx:].lower()
    conn = get_connection()
    try:
        cursor = conn.cursor()
        # 获取当前 folder_id 和 user_id
        cursor.execute(
            "SELECT folder_id, user_id FROM `file` WHERE id = %s AND is_deleted = 0",
            (file_id,),
        )
        row = cursor.fetchone()
        if row is None:
            raise ValueError("文件不存在")
        folder_id = row[0]
        if check_name_conflict(name, folder_id, exclude_id=file_id, exclude_type="file"):
            raise ValueError(f"同一目录下已存在名为「{name}」的文件夹或文件")
        cursor.execute(
            "UPDATE `file` SET name = %s, extension = %s WHERE id = %s AND is_deleted = 0",
            (name, ext, file_id),
        )
        conn.commit()
        # 失效相关缓存（使用调用者的 user_id）
        uid = caller_user_id or row[1]
        invalidate_folder_content(uid, folder_id)
        invalidate_file_info(file_id)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 移动
# ---------------------------------------------------------------------------

def move_folder(folder_id: int, target_parent_id: int, user_id: int):
    """移动文件夹到目标父文件夹下，级联更新子孙 path_node"""
    conn = get_connection()
    try:
        cursor = conn.cursor(dictionary=True)

        # 查询当前文件夹
        cursor.execute(
            "SELECT * FROM `folder` WHERE id = %s AND is_deleted = 0",
            (folder_id,),
        )
        folder = cursor.fetchone()
        if folder is None:
            return

        old_parent_id = folder["parent_id"]
        old_path_node = folder["path_node"]

        # 查询目标父文件夹
        if target_parent_id == 0:
            target_path_node = ","
        else:
            cursor.execute(
                "SELECT path_node FROM `folder` WHERE id = %s AND is_deleted = 0",
                (target_parent_id,),
            )
            target = cursor.fetchone()
            if target is None:
                raise ValueError("目标父文件夹不存在")
            target_path_node = target["path_node"]

        # 构造新的 path_node
        if target_path_node.endswith(","):
            new_path_node = target_path_node + str(folder_id) + ","
        else:
            new_path_node = target_path_node + "," + str(folder_id) + ","

        # 更新当前文件夹
        cursor.execute(
            "UPDATE `folder` SET parent_id = %s, path_node = %s WHERE id = %s",
            (target_parent_id, new_path_node, folder_id),
        )

        # 级联更新所有子孙文件夹的 path_node
        # 使用 CONCAT + SUBSTRING 替换前缀（而不是全局 REPLACE）。
        # 因为 old_path_node 可能为 ","（旧的根目录文件夹缺陷），REPLACE 会破坏所有逗号。
        #
        # SUBSTRING 从 LENGTH(old_path_node)+1 位开始截取，即去掉旧前缀后拼接新前缀：
        #   旧: ",5,10,"  → 新: ",1,5,10," （old_path_node=",5,", new_path_node=",1,5,"）
        if old_path_node and old_path_node != ",":
            old_prefix_len = len(old_path_node)
            cursor.execute(
                "UPDATE `folder` SET path_node = CONCAT(%s, SUBSTRING(path_node, %s)) "
                "WHERE user_id = %s AND path_node LIKE %s AND id != %s",
                (new_path_node, old_prefix_len + 1,
                 user_id, old_path_node + "%", folder_id),
            )
        elif old_path_node == ",":
            # 根目录文件夹（path_node=","）的历史缺陷情况：
            # 子孙的 path_node 不包含根目录自身的 ID（格式如 ",child_id,..."），
            # 无法通过前缀替换。跳过级联更新，由数据迁移脚本统一修复。
            # 迁移脚本会基于 parent_id 链重建所有 path_node。
            logger.warning(
                "move_folder: 文件夹 %d path_node 为 ','，跳过级联更新。"
                "请运行数据迁移脚本修复所有 path_node。", folder_id
            )

        conn.commit()
    finally:
        conn.close()

    # 失效相关缓存（源父目录 + 目标父目录）
    invalidate_folder_content(user_id, old_parent_id)
    invalidate_folder_content(user_id, target_parent_id)
    invalidate_folder_info(folder_id)
    invalidate_breadcrumb_ancestors(folder_id)


def move_file(file_id: int, folder_id: int, user_id: int = 0):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        # 获取旧的 folder_id
        cursor.execute(
            "SELECT folder_id FROM `file` WHERE id = %s AND is_deleted = 0",
            (file_id,),
        )
        row = cursor.fetchone()
        old_folder_id = row[0] if row else None
        if old_folder_id is None:
            return
        cursor.execute(
            "UPDATE `file` SET folder_id = %s WHERE id = %s AND is_deleted = 0",
            (folder_id, file_id),
        )
        conn.commit()
    finally:
        conn.close()

    # 失效相关缓存（源目录 + 目标目录 + 文件信息）
    if user_id:
        invalidate_folder_content(user_id, old_folder_id)
        invalidate_folder_content(user_id, folder_id)
    invalidate_file_info(file_id)


# ---------------------------------------------------------------------------
# 软删除（回收站）
# ---------------------------------------------------------------------------

def _soft_delete_folder(folder_id: int, user_id: int):
    """递归软删除文件夹及子文件（每个步骤使用独立连接，避免连接池耗尽）"""
    # 1. 先查询子文件夹（用独立连接，关闭后再递归）
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id FROM `folder` WHERE parent_id = %s AND is_deleted = 0",
            (folder_id,),
        )
        sub_ids = [r[0] for r in cursor.fetchall()]
    finally:
        conn.close()

    # 递归删除子文件夹（每个递归调用独立连接）
    for sf_id in sub_ids:
        _soft_delete_folder(sf_id, user_id)

    # 2. 软删除该文件夹下的所有文件
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE `file` SET is_deleted = 1 WHERE folder_id = %s AND is_deleted = 0",
            (folder_id,),
        )
        conn.commit()
    finally:
        conn.close()

    # 3. 软删除文件夹本身
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE `folder` SET is_deleted = 1 WHERE id = %s",
            (folder_id,),
        )
        conn.commit()
    finally:
        conn.close()

    # 4. 记录删除日志（忽略表不存在的错误）
    try:
        conn = get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO delete_logs (file_id, deleted_by) VALUES (%s, %s)",
                (folder_id, user_id),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


def _soft_delete_file(file_id: int, user_id: int):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE `file` SET is_deleted = 1 WHERE id = %s",
            (file_id,),
        )
        conn.commit()
    finally:
        conn.close()

    # 记录删除日志（忽略表不存在的错误）
    try:
        conn = get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO delete_logs (file_id, deleted_by) VALUES (%s, %s)",
                (file_id, user_id),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


def delete_file(file_id: int, user_id: int):
    file = get_file_by_id(file_id)
    if file is None:
        return
    _soft_delete_file(file_id, user_id)
    # 失效相关缓存
    invalidate_folder_content(user_id, file.folder_id)
    invalidate_file_info(file_id)
    invalidate_total_size()
    invalidate_trash(user_id)


def delete_folder(folder_id: int, user_id: int):
    folder = get_folder_by_id(folder_id)
    if folder is None:
        return
    _soft_delete_folder(folder_id, user_id)
    # 失效相关缓存
    invalidate_folder_content(user_id, folder.parent_id)
    invalidate_folder_info(folder_id)
    invalidate_trash(user_id)


# ---------------------------------------------------------------------------
# 恢复
# ---------------------------------------------------------------------------

def restore_folder(folder_id: int, user_id: int = 0):
    """递归恢复文件夹及子文件（带缓存失效，使用独立连接避免连接池耗尽）"""
    # 1. 先获取父目录 ID 和子文件夹列表（独立连接，关闭后再递归）
    parent_id = 0
    sub_ids = []
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT parent_id FROM `folder` WHERE id = %s", (folder_id,))
        row = cursor.fetchone()
        if row:
            parent_id = row[0]
        cursor.execute("SELECT id FROM `folder` WHERE parent_id = %s AND is_deleted = 1", (folder_id,))
        sub_ids = [r[0] for r in cursor.fetchall()]
    finally:
        conn.close()

    # 2. 恢复文件夹本身（独立连接）
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("UPDATE `folder` SET is_deleted = 0 WHERE id = %s", (folder_id,))
        conn.commit()
    finally:
        conn.close()

    # 3. 恢复文件（独立连接）
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("UPDATE `file` SET is_deleted = 0 WHERE folder_id = %s AND is_deleted = 1", (folder_id,))
        conn.commit()
    finally:
        conn.close()

    # 4. 递归恢复子文件夹（每个递归调用独立连接）
    for sf_id in sub_ids:
        restore_folder(sf_id, user_id)

    # 失效相关缓存
    if user_id:
        invalidate_folder_content(user_id, parent_id)
    invalidate_folder_info(folder_id)
    invalidate_trash(user_id)


def restore_file(file_id: int, user_id: int = 0):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        # 获取 folder_id 和 user_id
        cursor.execute(
            "SELECT folder_id, user_id FROM `file` WHERE id = %s",
            (file_id,),
        )
        row = cursor.fetchone()
        folder_id = row[0] if row else 0
        file_owner = row[1] if row else 0
        cursor.execute(
            "UPDATE `file` SET is_deleted = 0 WHERE id = %s",
            (file_id,),
        )
        conn.commit()
    finally:
        conn.close()

    # 失效相关缓存
    uid = user_id or file_owner
    if uid:
        invalidate_folder_content(uid, folder_id)
    invalidate_file_info(file_id)
    invalidate_trash(user_id)


# ---------------------------------------------------------------------------
# 永久删除
# ---------------------------------------------------------------------------

def permanent_delete_file(file_id: int, user_id: int = 0):
    file = get_by_id_include_deleted(file_id)
    if file is None:
        return

    # 删除 MinIO 对象
    if file.minio_object_name:
        try:
            client = _get_minio_client()
            client.remove_object(settings.minio_bucket, file.minio_object_name)
        except S3Error:
            pass

    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM delete_logs WHERE file_id = %s", (file_id,))
        cursor.execute("DELETE FROM favorites WHERE file_id = %s AND item_type = 'file'", (file_id,))
        cursor.execute("DELETE FROM `file` WHERE id = %s", (file_id,))
        conn.commit()
    finally:
        conn.close()

    # 失效相关缓存
    if user_id:
        invalidate_folder_content(user_id, file.folder_id)
    invalidate_file_info(file_id)
    invalidate_total_size()
    invalidate_trash(user_id)


def permanent_delete_folder(folder_id: int, user_id: int = 0):
    """递归永久删除文件夹及所有子文件（每个步骤使用独立连接）"""
    # 1. 先获取父目录 ID 和子文件夹列表（独立连接，关闭后再递归）
    parent_id = 0
    sub_ids = []
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT id, parent_id FROM `folder` WHERE id = %s", (folder_id,))
        row = cursor.fetchone()
        if row:
            parent_id = row[1]
        cursor.execute("SELECT id FROM `folder` WHERE parent_id = %s", (folder_id,))
        sub_ids = [r[0] for r in cursor.fetchall()]
    finally:
        conn.close()

    # 递归删除子文件夹（每个递归调用独立连接）
    for sf_id in sub_ids:
        permanent_delete_folder(sf_id, user_id)

    # 2. 删除该文件夹下的所有文件（独立连接）
    conn = get_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT * FROM `file` WHERE folder_id = %s",
            (folder_id,),
        )
        files = cursor.fetchall()
        for f in files:
            if f["minio_object_name"]:
                try:
                    client = _get_minio_client()
                    client.remove_object(settings.minio_bucket, f["minio_object_name"])
                except S3Error:
                    pass
        # 清理收藏记录
        cursor.execute(
            "DELETE f FROM favorites f INNER JOIN `file` fl ON f.file_id = fl.id AND f.item_type = 'file' WHERE fl.folder_id = %s",
            (folder_id,),
        )
        # 批量删除文件
        cursor.execute(
            "DELETE FROM `file` WHERE folder_id = %s",
            (folder_id,),
        )
        conn.commit()
    finally:
        conn.close()

    # 3. 删除文件夹本身及关联记录（独立连接）
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM favorites WHERE file_id = %s AND item_type = 'folder'", (folder_id,))
        cursor.execute("DELETE FROM `folder` WHERE id = %s", (folder_id,))
        cursor.execute("DELETE FROM delete_logs WHERE file_id = %s", (folder_id,))
        conn.commit()
    finally:
        conn.close()

    # 失效相关缓存
    if user_id:
        invalidate_folder_content(user_id, parent_id)
    invalidate_folder_info(folder_id)
    invalidate_total_size()
    invalidate_trash(user_id)


def empty_trash(user_id: int = 0):
    trash = list_trash(user_id)
    for f in trash["files"]:
        permanent_delete_file(f.id, user_id)
    for fol in trash["folders"]:
        permanent_delete_folder(fol.id, user_id)


def list_trash(user_id: int = 0) -> dict:
    """列出当前用户的回收站内容（带 Redis 缓存）"""
    if user_id:
        cached = get_cached_trash(user_id)
        if cached is not None:
            folders = [FolderItem(**f) for f in cached.get("folders", [])]
            files = [FileItem(**f) for f in cached.get("files", [])]
            return {"folders": folders, "files": files}

    conn = get_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT * FROM `folder` WHERE is_deleted = 1 AND user_id = %s "
            "AND parent_id NOT IN (SELECT id FROM `folder` WHERE is_deleted = 1) "
            "ORDER BY update_time DESC",
            (user_id,),
        )
        folders = [_folder_row_to_model(r) for r in cursor.fetchall()]

        cursor.execute(
            "SELECT * FROM `file` WHERE is_deleted = 1 AND user_id = %s "
            "AND folder_id NOT IN (SELECT id FROM `folder` WHERE is_deleted = 1) "
            "ORDER BY update_time DESC",
            (user_id,),
        )
        files = [_file_row_to_model(r) for r in cursor.fetchall()]

        result = {"folders": folders, "files": files}

        if user_id:
            set_cached_trash(user_id, {
                "folders": [f.model_dump() for f in folders],
                "files": [f.model_dump() for f in files],
            })

        return result
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 搜索
# ---------------------------------------------------------------------------

def _collect_subfolder_ids(root_folder_id: int, user_id: int) -> list[int]:
    """递归收集 root_folder_id 下所有子文件夹 ID

    - root_folder_id > 0: 收集该目录及其所有子目录（含自身）
    - root_folder_id = 0: 收集所有顶层目录（parent_id=0）及其所有子目录
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()
        result = []
        queue = []

        if root_folder_id == 0:
            # 根目录：从所有顶层文件夹开始递归
            cursor.execute("SELECT id FROM `folder` WHERE parent_id = 0 AND is_deleted = 0")
        else:
            # 指定目录：从该目录开始递归
            cursor.execute(
                "SELECT id FROM `folder` WHERE id = %s AND is_deleted = 0",
                (root_folder_id,)
            )

        for row in cursor.fetchall():
            result.append(row[0])
            queue.append(row[0])

        while queue:
            current = queue.pop(0)
            cursor.execute(
                "SELECT id FROM `folder` WHERE parent_id = %s AND is_deleted = 0",
                (current,)
            )
            for row in cursor.fetchall():
                result.append(row[0])
                queue.append(row[0])

        return result
    finally:
        conn.close()


def search_files(keyword: str, user_id: int, root_folder_id: int = 0) -> tuple[list[FolderItem], list[FileItem]]:
    """模糊搜索文件和文件夹（返回 (folders, files) 元组）

    搜索范围由 root_folder_id 决定：
    - root_folder_id = 0: 搜索所有顶层目录及其子目录
    - root_folder_id > 0: 只搜索该目录及其子目录下的内容
    """
    like = f"%{keyword}%"
    conn = get_connection()
    try:
        cursor = conn.cursor(dictionary=True)

        # 获取搜索范围的文件夹 ID 列表
        folder_ids = _collect_subfolder_ids(root_folder_id, user_id)

        if not folder_ids:
            # 没有任何可见目录，返回空结果
            return [], []

        placeholders = ','.join(['%s'] * len(folder_ids))

        # 搜索文件夹（模糊匹配，相关性排序：完全匹配 > 前缀匹配 > 模糊匹配）
        cursor.execute(
            "SELECT * FROM `folder` "
            f"WHERE id IN ({placeholders}) AND name LIKE %s AND is_deleted = 0 "
            "ORDER BY "
            "  CASE "
            "    WHEN name = %s THEN 0"
            "    WHEN name LIKE %s THEN 1"
            "    ELSE 2"
            "  END,"
            "  name ASC",
            folder_ids + [like, keyword, f"{keyword}%"],
        )
        folders = [_folder_row_to_model(r) for r in cursor.fetchall()]

        # 搜索文件（模糊匹配，按相关性排序）
        # 根目录搜索时，额外包含直接挂在根目录下的文件（folder_id = 0）
        if root_folder_id == 0:
            file_where = f"(folder_id IN ({placeholders}) OR folder_id = 0) AND name LIKE %s"
        else:
            file_where = f"folder_id IN ({placeholders}) AND name LIKE %s"

        cursor.execute(
            "SELECT * FROM `file` "
            f"WHERE {file_where} AND is_deleted = 0 "
            "ORDER BY "
            "  CASE "
            "    WHEN name = %s THEN 0"
            "    WHEN name LIKE %s THEN 1"
            "    ELSE 2"
            "  END,"
            "  name ASC",
            folder_ids + [like, keyword, f"{keyword}%"],
        )
        files = [_file_row_to_model(r) for r in cursor.fetchall()]

        return folders, files
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 收藏操作
# ---------------------------------------------------------------------------

def toggle_favorite(item_id: int, user_id: int, favorite: bool, item_type: str = "file"):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        if favorite:
            cursor.execute(
                "INSERT IGNORE INTO favorites (user_id, file_id, item_type) VALUES (%s, %s, %s)",
                (user_id, item_id, item_type),
            )
        else:
            cursor.execute(
                "DELETE FROM favorites WHERE user_id = %s AND file_id = %s AND item_type = %s",
                (user_id, item_id, item_type),
            )
        conn.commit()
    finally:
        conn.close()
    # 失效收藏缓存
    invalidate_favorite_ids(user_id)


def list_favorites(user_id: int) -> list:
    """返回混合列表，每项包含 type 字段标示是 file 还是 folder"""
    conn = get_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        # 收藏的文件
        cursor.execute(
            """SELECT f.*, 'file' AS item_type FROM `file` f
               INNER JOIN favorites fav ON f.id = fav.file_id AND fav.item_type = 'file'
               WHERE fav.user_id = %s AND f.is_deleted = 0
               ORDER BY f.name ASC""",
            (user_id,),
        )
        files = cursor.fetchall()
        # 收藏的文件夹
        cursor.execute(
            """SELECT f.*, 'folder' AS item_type FROM `folder` f
               INNER JOIN favorites fav ON f.id = fav.file_id AND fav.item_type = 'folder'
               WHERE fav.user_id = %s AND f.is_deleted = 0
               ORDER BY f.name ASC""",
            (user_id,),
        )
        folders = cursor.fetchall()
        # 合并返回
        result = []
        for r in folders:
            result.append(("folder", _folder_row_to_model(r)))
        for r in files:
            result.append(("file", _file_row_to_model(r)))
        return result
    finally:
        conn.close()


def is_favorited(item_id: int, user_id: int, item_type: str = "file") -> bool:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT 1 FROM favorites WHERE user_id = %s AND file_id = %s AND item_type = %s",
            (user_id, item_id, item_type),
        )
        return cursor.fetchone() is not None
    finally:
        conn.close()


def get_favorite_ids(user_id: int) -> set[str]:
    """批量查询当前用户收藏的所有条目 ID（带 Redis 缓存），返回复合ID集合如 {'f2', 'd3'}"""
    cached = get_cached_favorite_ids(user_id)
    if cached is not None:
        return cached
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT file_id, item_type FROM favorites WHERE user_id = %s", (user_id,))
        ids = {encode_id(row[0], row[1]) for row in cursor.fetchall()}
        set_cached_favorite_ids(user_id, ids)
        return ids
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 批量操作
# ---------------------------------------------------------------------------

def batch_delete(user_id: int, file_ids: list[int]):
    for file_id in file_ids:
        delete_file(file_id, user_id)


def batch_delete_typed(user_id: int, compound_ids: list[str]):
    """批量删除，接受复合ID列表（如 ['f2', 'd3']），自动区分文件夹和文件"""
    from app.utils.id_utils import decode_id
    for cid in compound_ids:
        item_type, numeric_id = decode_id(cid)
        if item_type == "folder":
            delete_folder(numeric_id, user_id)
        else:
            delete_file(numeric_id, user_id)


def batch_permanent_delete_typed(user_id: int, compound_ids: list[str]):
    """批量永久删除（用于上传回滚），接受复合ID列表，自动区分文件夹和文件"""
    from app.utils.id_utils import decode_id
    for cid in compound_ids:
        item_type, numeric_id = decode_id(cid)
        if item_type == "folder":
            permanent_delete_folder(numeric_id, user_id)
        else:
            permanent_delete_file(numeric_id, user_id)


def batch_move(file_ids: list[int], target_folder_id: int):
    for file_id in file_ids:
        move_file(file_id, target_folder_id)


def batch_move_typed(compound_ids: list[str], target_folder_id: int, user_id: int = 0):
    """批量移动，接受复合ID列表，自动区分文件夹和文件"""
    from app.utils.id_utils import decode_id
    for cid in compound_ids:
        item_type, numeric_id = decode_id(cid)
        if item_type == "folder":
            move_folder(numeric_id, target_folder_id, user_id)
        else:
            move_file(numeric_id, target_folder_id, user_id)


def copy_file(file_id: int, target_folder_id: int, user_id: int) -> int:
    """复制文件，复用 MinIO 对象（无需实际拷贝数据）"""
    source = get_file_by_id(file_id)
    if source is None:
        raise ValueError("源文件不存在")

    new_id = create_file(
        user_id=user_id,
        folder_id=target_folder_id,
        name=source.name,
        extension=source.extension,
        size=source.size,
        sha256=source.sha256,
        minio_bucket=source.minio_bucket,
        minio_object_name=source.minio_object_name,
    )
    return new_id


def _is_descendant(target_parent_id: int, folder_id: int) -> bool:
    """检查 target_parent_id 是否是 folder_id 的后代（防止循环复制）"""
    if target_parent_id == folder_id:
        return True
    conn = get_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT path_node FROM `folder` WHERE id = %s AND is_deleted = 0",
            (target_parent_id,),
        )
        row = cursor.fetchone()
        if row and row.get("path_node"):
            path_node = row["path_node"]
            return f",{folder_id}," in path_node
        return False
    finally:
        conn.close()


def copy_folder(folder_id: int, target_parent_id: int, user_id: int) -> int:
    """复制文件夹及其所有子文件和子文件夹"""
    source_folder = get_folder_by_id(folder_id)
    if source_folder is None:
        raise ValueError("源文件夹不存在")

    if _is_descendant(target_parent_id, folder_id):
        raise ValueError("不能将文件夹复制到自身或其子文件夹中")

    new_folder_id = create_folder(user_id, source_folder.name, target_parent_id)

    content = list_folder_content(folder_id)

    for sub_folder in content["folders"]:
        copy_folder(sub_folder.id, new_folder_id, user_id)

    for file_item in content["files"]:
        copy_file(file_item.id, new_folder_id, user_id)

    return new_folder_id


def batch_copy(file_ids: list[int], target_folder_id: int, user_id: int):
    for file_id in file_ids:
        copy_file(file_id, target_folder_id, user_id)


def batch_copy_typed(compound_ids: list[str], target_folder_id: int, user_id: int):
    """批量复制，接受复合ID列表，自动区分文件夹和文件"""
    from app.utils.id_utils import decode_id
    for cid in compound_ids:
        item_type, numeric_id = decode_id(cid)
        if item_type == "folder":
            copy_folder(numeric_id, target_folder_id, user_id)
        else:
            copy_file(numeric_id, target_folder_id, user_id)


# ---------------------------------------------------------------------------
# 面包屑导航
# ---------------------------------------------------------------------------

def get_breadcrumb(folder_id: int) -> list[BreadcrumbItem]:
    """基于 parent_id 上溯遍历构建面包屑导航（带 Redis 缓存）
    
    不再依赖 path_node 字段，避免 path_node 异常导致路径显示错误。
    改为从当前文件夹开始，沿着 parent_id 链一直遍历到根目录。
    """
    if folder_id == 0:
        return []

    cached = get_cached_breadcrumb(folder_id)
    if cached is not None:
        return [BreadcrumbItem(**b) for b in cached]

    conn = get_connection()
    try:
        cursor = conn.cursor(dictionary=True)

        # 从当前文件夹开始，沿 parent_id 链上溯到根目录
        path: list[BreadcrumbItem] = []
        current_id = folder_id
        max_depth = 50  # 防止死循环
        while current_id and current_id != 0 and max_depth > 0:
            cursor.execute(
                "SELECT id, name, parent_id FROM `folder` WHERE id = %s",
                (current_id,),
            )
            row = cursor.fetchone()
            if row is None:
                break
            # 插入到最前面（根 → 当前）
            path.insert(0, BreadcrumbItem(id=row["id"], name=row["name"]))
            current_id = row["parent_id"]
            max_depth -= 1

        # 写入缓存
        set_cached_breadcrumb(folder_id, [b.model_dump() for b in path])
        return path
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# MinIO presigned URLs
# ---------------------------------------------------------------------------

def get_upload_presign_url(object_name: str) -> str:
    _ensure_bucket()
    client = _get_minio_client()
    return client.presigned_put_object(settings.minio_bucket, object_name, expires=timedelta(minutes=15))


def get_download_presign_url(object_name: str) -> str:
    _ensure_bucket()
    client = _get_minio_client()
    return client.presigned_get_object(settings.minio_bucket, object_name, expires=timedelta(hours=1))


def download_file_proxy(object_name: str) -> bytes:
    _ensure_bucket()
    client = _get_minio_client()
    response = client.get_object(settings.minio_bucket, object_name)
    try:
        return response.read()
    finally:
        response.close()


def proxy_upload(input_stream: BufferedReader, file_size: int, content_type: Optional[str], object_name: str):
    _ensure_bucket()
    client = _get_minio_client()
    client.put_object(
        settings.minio_bucket,
        object_name,
        input_stream,
        file_size,
        content_type=content_type or "application/octet-stream",
    )


# ---------------------------------------------------------------------------
# PDF conversion & upload
# ---------------------------------------------------------------------------

def convert_and_upload_pdf(data: bytes, ext: str, mime_type: Optional[str], user_id: int) -> Optional[str]:
    pdf_data = convert_to_pdf(data, ext, mime_type)
    if pdf_data is None:
        return None

    pdf_object_name = f"cloud_drive/{user_id}/pdf/{uuid.uuid4().hex}.pdf"
    proxy_upload(
        io.BytesIO(pdf_data),
        len(pdf_data),
        "application/pdf",
        pdf_object_name,
    )
    return pdf_object_name


def update_pdf_object_name(file_id: int, pdf_object_name: Optional[str]):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE `file` SET pdf_object_name = %s WHERE id = %s",
            (pdf_object_name, file_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_pdf_download_url(pdf_object_name: str) -> str:
    _ensure_bucket()
    client = _get_minio_client()
    return client.presigned_get_object(settings.minio_bucket, pdf_object_name, expires=timedelta(hours=1))


def download_pdf_proxy(pdf_object_name: str) -> bytes:
    _ensure_bucket()
    client = _get_minio_client()
    response = client.get_object(settings.minio_bucket, pdf_object_name)
    try:
        return response.read()
    finally:
        response.close()


# ---------------------------------------------------------------------------
# Quick Access 快捷访问
# ---------------------------------------------------------------------------

def _quick_access_row_to_model(row: dict) -> QuickAccessItem:
    return QuickAccessItem(
        id=row["id"],
        user_id=row["user_id"],
        name=row["name"],
        file_id=row["file_id"],
        file_name=row.get("file_name"),
        is_folder=row.get("is_folder", True),
        sort_order=row.get("sort_order") or 0,
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


def list_quick_access(user_id: int) -> list[QuickAccessItem]:
    conn = get_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """SELECT q.*, 
                      COALESCE(f.name, fo.name) AS file_name,
                      CASE WHEN fo.id IS NOT NULL THEN TRUE ELSE FALSE END AS is_folder
               FROM quick_access q
               LEFT JOIN `file` f ON q.file_id = f.id
               LEFT JOIN `folder` fo ON q.file_id = fo.id
               WHERE q.user_id = %s
               ORDER BY q.sort_order ASC, q.created_at ASC""",
            (user_id,),
        )
        return [_quick_access_row_to_model(r) for r in cursor.fetchall()]
    finally:
        conn.close()


def add_quick_access(user_id: int, name: str, file_id: int) -> int:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO quick_access (user_id, name, file_id) VALUES (%s, %s, %s)",
            (user_id, name, file_id),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def update_quick_access(item_id: int, user_id: int, name: str):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE quick_access SET name = %s WHERE id = %s AND user_id = %s",
            (name, item_id, user_id),
        )
        conn.commit()
    finally:
        conn.close()


def delete_quick_access(item_id: int, user_id: int):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM quick_access WHERE id = %s AND user_id = %s",
            (item_id, user_id),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Redis 全量重建 & 一致性校验
# ---------------------------------------------------------------------------

def rebuild_all_cache():
    """从数据库读取全量数据，写入 Redis 持久化键（永不过期）

    在 main.py 启动时调用：如果 Redis 中无持久化数据，自动重建。
    也可手动通过 API 触发（如 /admin/rebuild-cache）。
    """
    return _rebuild_all_cache(get_connection)


def verify_cache_consistency() -> dict:
    """校验 Redis 持久化数据与数据库的一致性"""
    return _verify_cache_consistency(get_connection)


def repair_missing_breadcrumbs(folder_ids: list[int]) -> dict:
    """针对性修复缺失的面包屑缓存
    
    当一致性校验发现特定文件夹的面包屑缺失时，只重建这些缺失的面包屑，
    而不是执行全量重建，提高效率。
    
    参数:
        folder_ids: 需要修复面包屑的文件夹ID列表
        
    返回:
        修复结果统计字典
    """
    return _repair_missing_breadcrumbs(get_connection, folder_ids)