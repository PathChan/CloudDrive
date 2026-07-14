import uuid
from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Request, Query
from pydantic import BaseModel
from typing import Optional
from app.services import drive_service as cds
from app.utils.jwt_util import get_user_id_from_token
from app.models import FileItem, FolderItem, QuickAccessItem
from app.config import settings
from app.database import get_connection
from app.utils.id_utils import encode_id, decode_id, is_folder_id

router = APIRouter(prefix="/api/cloud-drive", tags=["cloud-drive"])

IMAGE_EXT_TO_MIME = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}


def _validate_file_type(filename: str) -> str:
    """校验并清理文件名（自动提取 basename，不移除非法字符，不限制扩展名）
    
    某些浏览器上传文件夹时可能将 `webkitRelativePath` 设为 filename 传入，
    需要提取有效的 basename。
    """
    # 确保使用 basename（移除可能的目录路径）
    cleaned = filename.strip()
    cleaned = cleaned.replace("\\", "/").split("/")[-1]
    
    # 移除 Windows 非法字符
    invalid_chars = '<>:"|?*'
    for char in invalid_chars:
        cleaned = cleaned.replace(char, "_")
    
    # 防止路径遍历攻击
    cleaned = cleaned.replace("..", "_")
    
    if not cleaned or cleaned == ".":
        raise HTTPException(status_code=400, detail="文件名无效")
    
    return cleaned


def get_user_id(request: Request) -> Optional[int]:
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header[7:]
        return get_user_id_from_token(token)
    query_token = request.query_params.get("token")
    if query_token:
        return get_user_id_from_token(query_token)
    return None


def require_user_id(request: Request) -> int:
    uid = get_user_id(request)
    if uid is None:
        return 1  # 默认用户（未登录时）
    return uid


def _get_item_type(item_id: int) -> Optional[str]:
    """判断 ID 属于文件夹还是文件
    优先检查 folder 表（folder 优先级高于 file），
    避免两表 auto_increment 冲突导致无法识别
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM `folder` WHERE id = %s", (item_id,))
        if cursor.fetchone() is not None:
            return "folder"
        cursor.execute("SELECT 1 FROM `file` WHERE id = %s", (item_id,))
        if cursor.fetchone() is not None:
            return "file"
        return None
    finally:
        conn.close()


def _get_file_full_dict(file_id: int) -> Optional[dict]:
    """获取 file 表的完整行（含 mime_type/pdf_object_name 等不在 FileItem 模型中的字段）"""
    conn = get_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM `file` WHERE id = %s", (file_id,))
        return cursor.fetchone()
    finally:
        conn.close()


def _file_to_dict(item, favorite_ids: set = None, item_type_override: str = None):
    """将 FolderItem/FileItem 转为前端字典，ID 使用复合ID编码（f{id}/d{id}）
    
    item_type_override: 当 item 是原始 dict 时，显式指定类型（'folder' 或 'file'）
    """
    if item_type_override:
        _type = item_type_override
    else:
        _type = "folder" if isinstance(item, FolderItem) else "file"
    compound_id = encode_id(item["id"] if isinstance(item, dict) else item.id, _type)
    is_fav = bool(favorite_ids and compound_id in favorite_ids)
    base = {
        "id": compound_id,
        "name": item["name"] if isinstance(item, dict) else item.name,
        "created_at": str(item["create_time"] if isinstance(item, dict) else item.create_time) if (isinstance(item, dict) and item.get("create_time")) or (not isinstance(item, dict) and item.create_time) else None,
        "updated_at": str(item["update_time"] if isinstance(item, dict) else item.update_time) if (isinstance(item, dict) and item.get("update_time")) or (not isinstance(item, dict) and item.update_time) else None,
        "uploader": cds._get_username(item["user_id"] if isinstance(item, dict) else item.user_id),
        "is_favorite": is_fav,
    }
    if _type == "folder":
        base.update({
            "is_folder": True,
            "parent_id": encode_id(item["parent_id"] if isinstance(item, dict) else item.parent_id, "folder") if (isinstance(item, dict) and item.get("parent_id")) or (not isinstance(item, dict) and item.parent_id) else "f0",
            "level": item.get("level", 1) if isinstance(item, dict) else item.level,
        })
    else:
        base.update({
            "is_folder": False,
            "folder_id": encode_id(item["folder_id"] if isinstance(item, dict) else item.folder_id, "folder") if (isinstance(item, dict) and item.get("folder_id")) or (not isinstance(item, dict) and item.folder_id) else "f0",
            "size": item["size"] if isinstance(item, dict) else item.size,
            "extension": item["extension"] if isinstance(item, dict) else item.extension,
            "minio_object_name": item["minio_object_name"] if isinstance(item, dict) else item.minio_object_name,
        })
    return base


def _resolve_type_param(type_param: Optional[str], numeric_id: int) -> Optional[str]:
    """解析前端传入的可选 type 参数，优先使用前端类型（解决ID冲突）"""
    if type_param in ("folder", "file"):
        return type_param
    if type_param is not None:
        raise HTTPException(status_code=400, detail="无效的类型参数，仅支持 folder/file")
    return _get_item_type(numeric_id)


# ---------- Files ----------

@router.get("/files")
def list_files(parent_id: Optional[str] = None, request: Request = None):
    user_id = require_user_id(request)
    # parent_id 可能是复合ID或数字ID
    numeric_parent = 0
    if parent_id:
        if parent_id.startswith("f") or parent_id.startswith("d"):
            try:
                _, numeric_parent = decode_id(parent_id)
            except ValueError:
                numeric_parent = int(parent_id)
        else:
            try:
                numeric_parent = int(parent_id)
            except ValueError:
                numeric_parent = 0

    content = cds.list_folder_content(numeric_parent, user_id)
    favorite_ids = cds.get_favorite_ids(user_id)
    total_size = cds.get_total_size()
    items = []
    for f in content["folders"]:
        items.append(_file_to_dict(f, favorite_ids))
    for f in content["files"]:
        items.append(_file_to_dict(f, favorite_ids))
    return {
        "files": items,
        "totalSize": total_size,
    }


@router.get("/files/search")
def search_files(keyword: str, request: Request = None):
    user_id = require_user_id(request)
    if not keyword or not keyword.strip():
        raise HTTPException(status_code=400, detail="搜索关键词不能为空")
    folders, files = cds.search_files(keyword.strip(), user_id)
    favorite_ids = cds.get_favorite_ids(user_id)
    items = []
    for f in folders:
        items.append(_file_to_dict(f, favorite_ids))
    for f in files:
        items.append(_file_to_dict(f, favorite_ids))
    return {"files": items}


# ---------- Trash ----------

@router.get("/trash")
def list_trash(request: Request = None):
    user_id = require_user_id(request)
    content = cds.list_trash(user_id)
    items = []
    for f in content["folders"]:
        items.append(_file_to_dict(f))
    for f in content["files"]:
        items.append(_file_to_dict(f))
    return {"files": items}


@router.post("/trash/restore/{file_id}")
def restore_file(file_id: int, request: Request = None, type: Optional[str] = Query(None)):
    """恢复回收站条目，支持 type 参数：?type=folder 或 ?type=file"""
    user_id = require_user_id(request)
    item_type = _resolve_type_param(type, file_id)
    if item_type == "folder":
        cds.restore_folder(file_id, user_id)
    elif item_type == "file":
        cds.restore_file(file_id, user_id)
    else:
        raise HTTPException(status_code=404, detail="文件或文件夹不存在")
    return {"success": True}


@router.delete("/trash/permanent/{file_id}")
def permanent_delete_file(file_id: int, request: Request = None, type: Optional[str] = Query(None)):
    """永久删除，支持 type 参数：?type=folder 或 ?type=file"""
    user_id = require_user_id(request)
    item_type = _resolve_type_param(type, file_id)
    if item_type == "folder":
        cds.permanent_delete_folder(file_id, user_id)
    elif item_type == "file":
        cds.permanent_delete_file(file_id, user_id)
    else:
        raise HTTPException(status_code=404, detail="文件或文件夹不存在")
    return {"success": True}


@router.delete("/trash/empty")
def empty_trash(request: Request = None):
    user_id = require_user_id(request)
    cds.empty_trash(user_id)
    return {"success": True}


# ---------- Favorites ----------

@router.get("/favorites")
def list_favorites(request: Request = None):
    user_id = require_user_id(request)
    items = cds.list_favorites(user_id)
    favorite_ids = cds.get_favorite_ids(user_id)
    result = []
    for item_type, item in items:
        d = _file_to_dict(item, favorite_ids, item_type_override=item_type)
        result.append(d)
    return {"files": result}


class ToggleFavoriteBody(BaseModel):
    favorite: bool
    item_type: Optional[str] = None  # 前端可选传入，解决ID冲突


@router.post("/file/{file_id}/favorite")
def toggle_favorite(file_id: int, body: ToggleFavoriteBody, request: Request = None):
    user_id = require_user_id(request)
    item_type = body.item_type or _get_item_type(file_id)
    if item_type is None:
        raise HTTPException(status_code=404, detail="文件或文件夹不存在")
    cds.toggle_favorite(file_id, user_id, body.favorite, item_type)
    return {"success": True, "is_favorite": body.favorite}


# ---------- Batch ----------

class BatchItem(BaseModel):
    """批量操作条目，使用复合ID"""
    id: str  # 如 "f2" 或 "d2"


class BatchDeleteBody(BaseModel):
    items: list[BatchItem]


@router.post("/batch/delete")
def batch_delete(body: BatchDeleteBody, request: Request = None):
    user_id = require_user_id(request)
    if not body.items:
        raise HTTPException(status_code=400, detail="条目列表不能为空")
    cds.batch_delete_typed(user_id, [item.id for item in body.items])
    return {"success": True}


class BatchMoveBody(BaseModel):
    items: list[BatchItem]
    target_parent_id: Optional[str] = None  # 目标父目录复合ID


@router.post("/batch/move")
def batch_move(body: BatchMoveBody, request: Request = None):
    user_id = require_user_id(request)
    if not body.items:
        raise HTTPException(status_code=400, detail="条目列表不能为空")
    target_numeric = 0
    if body.target_parent_id:
        try:
            _, target_numeric = decode_id(body.target_parent_id)
        except ValueError:
            target_numeric = int(body.target_parent_id)
    try:
        cds.batch_move_typed([item.id for item in body.items], target_numeric, user_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"success": True}


class CopyBody(BaseModel):
    file_id: str  # 复合ID
    target_parent_id: Optional[str] = None  # 复合ID


@router.post("/file/copy")
def copy_file(body: CopyBody, request: Request = None):
    user_id = require_user_id(request)
    target_num = 0
    if body.target_parent_id:
        try:
            _, target_num = decode_id(body.target_parent_id)
        except ValueError:
            target_num = int(body.target_parent_id)
    try:
        _, num_id = decode_id(body.file_id)
        new_id = cds.copy_file(num_id, target_num, user_id)
        return {"id": encode_id(new_id, "file"), "success": True}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


class UndoUploadBody(BaseModel):
    """上传回滚：删除已创建的文件记录和 MinIO 对象"""
    file_ids: list[str]  # 复合ID列表，如 ["d1", "d2", ...]


@router.post("/upload/undo")
def undo_upload(body: UndoUploadBody, request: Request = None):
    """上传失败时回滚：永久删除已创建的文件记录和 MinIO 对象"""
    user_id = require_user_id(request)
    if not body.file_ids:
        return {"success": True}
    cds.batch_permanent_delete_typed(user_id, body.file_ids)
    return {"success": True, "deleted_count": len(body.file_ids)}


class BatchCopyBody(BaseModel):
    items: list[BatchItem]
    target_parent_id: Optional[str] = None


@router.post("/batch/copy")
def batch_copy(body: BatchCopyBody, request: Request = None):
    user_id = require_user_id(request)
    if not body.items:
        raise HTTPException(status_code=400, detail="条目列表不能为空")
    target_num = 0
    if body.target_parent_id:
        try:
            _, target_num = decode_id(body.target_parent_id)
        except ValueError:
            target_num = int(body.target_parent_id)
    try:
        cds.batch_copy_typed([item.id for item in body.items], target_num, user_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"success": True}


# ---------- 名称冲突检查 ----------

@router.get("/check-name")
def check_name(name: str, parent_id: str = "f0", exclude_id: str = ""):
    """实时验证名称是否可用，parent_id 和 exclude_id 支持复合ID"""
    if not name.strip():
        return {"conflict": False}
    numeric_parent = 0
    if parent_id and parent_id != "f0":
        try:
            _, numeric_parent = decode_id(parent_id)
        except ValueError:
            try:
                numeric_parent = int(parent_id)
            except ValueError:
                numeric_parent = 0
    numeric_exclude = 0
    exclude_type = ""
    if exclude_id:
        try:
            exclude_type, numeric_exclude = decode_id(exclude_id)
        except ValueError:
            try:
                numeric_exclude = int(exclude_id)
            except ValueError:
                numeric_exclude = 0
    conflict = cds.check_name_conflict(name.strip(), numeric_parent, numeric_exclude, exclude_type)
    return {"conflict": conflict}


# ---------- Folder ----------

class CreateFolderBody(BaseModel):
    name: str
    parent_id: Optional[str] = None  # 复合ID


@router.post("/folder")
def create_folder(body: CreateFolderBody, request: Request = None):
    user_id = require_user_id(request)
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="文件夹名称不能为空")
    numeric_parent = 0
    if body.parent_id:
        try:
            _, numeric_parent = decode_id(body.parent_id)
        except ValueError:
            try:
                numeric_parent = int(body.parent_id)
            except ValueError:
                numeric_parent = 0
    try:
        folder_id = cds.create_folder(user_id, name, numeric_parent)
    except ValueError as e:
        # 同名文件夹已存在 → 返回现有文件夹（类似 mkdir -p 行为）
        existing = cds.find_folder_by_name(name, numeric_parent)
        if existing is not None:
            return {"id": encode_id(existing, "folder"), "name": name, "is_folder": True, "exists": True}
        raise HTTPException(status_code=409, detail=str(e))
    return {"id": encode_id(folder_id, "folder"), "name": name, "is_folder": True}


# ---------- Upload ----------

class UploadPresignBody(BaseModel):
    filename: str
    parent_id: Optional[str] = None  # 复合ID
    file_size: int = 0


@router.post("/upload/presign")
def get_upload_presign(body: UploadPresignBody, request: Request = None):
    user_id = require_user_id(request)
    filename = _validate_file_type(body.filename)
    ext = ""
    dot_idx = filename.rfind(".")
    if dot_idx >= 0:
        ext = filename[dot_idx:].lower()
    object_name = f"cloud_drive/{user_id}/{uuid.uuid4().hex}{ext}"
    upload_url = cds.get_upload_presign_url(object_name)
    return {"uploadUrl": upload_url, "objectName": object_name, "method": "PUT"}


class UploadConfirmBody(BaseModel):
    name: str
    parent_id: Optional[str] = None  # 复合ID
    objectName: str
    file_size: int = 0


@router.post("/upload/confirm")
def confirm_upload(body: UploadConfirmBody, request: Request = None):
    user_id = require_user_id(request)
    name = body.name.strip()
    if not name or not body.objectName:
        raise HTTPException(status_code=400, detail="参数不完整")
    ext = ""
    dot_idx = name.rfind(".")
    if dot_idx >= 0:
        ext = name[dot_idx:].lower()
    numeric_parent = 0
    if body.parent_id:
        try:
            _, numeric_parent = decode_id(body.parent_id)
        except ValueError:
            try:
                numeric_parent = int(body.parent_id)
            except ValueError:
                numeric_parent = 0
    try:
        file_id = cds.create_file(
            user_id=user_id,
            folder_id=numeric_parent,
            name=name,
            extension=ext,
            size=body.file_size,
            sha256="",
            minio_bucket=settings.minio_bucket,
            minio_object_name=body.objectName,
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))

    # 尝试将办公文档转换为 PDF
    try:
        file_data = cds.download_file_proxy(body.objectName)
        pdf_object_name = cds.convert_and_upload_pdf(file_data, ext, None, user_id)
        if pdf_object_name:
            cds.update_pdf_object_name(file_id, pdf_object_name)
    except Exception:
        pass  # PDF 转换失败不影响上传成功

    return {"id": encode_id(file_id, "file"), "name": name}


@router.get("/file/{file_id}/download")
def download_file(file_id: int, request: Request = None):
    user_id = require_user_id(request)
    file = cds.get_file_by_id(file_id)
    if file is None:
        raise HTTPException(status_code=404, detail="文件不存在")
    download_url = cds.get_download_presign_url(file.minio_object_name)
    return {"downloadUrl": download_url, "name": file.name}


@router.get("/file/{file_id}/download-proxy")
def download_file_proxy(file_id: int, inline: str = "0", request: Request = None):
    user_id = get_user_id(request)
    if user_id is None:
        raise HTTPException(status_code=401, detail="缺少认证令牌")
    file = cds.get_file_by_id(file_id)
    if file is None:
        raise HTTPException(status_code=404, detail="文件不存在")
    try:
        data = cds.download_file_proxy(file.minio_object_name)
        from starlette.responses import Response
        disposition = "inline" if inline == "1" else "attachment"
        import urllib.parse
        safe_filename = urllib.parse.quote(file.name, safe='')
        disposition_header = f'{disposition}; filename="{safe_filename}"; filename*=UTF-8\'\'{safe_filename}'
        return Response(
            content=data,
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": disposition_header,
                "Content-Length": str(len(data)),
            },
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"下载失败: {str(e)}")


IMAGE_EXT_TO_MIME = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}


@router.get("/file/{file_id}/preview")
def preview_file(file_id: int, request: Request = None):
    user_id = get_user_id(request)
    if user_id is None:
        raise HTTPException(status_code=401, detail="缺少认证令牌")

    # 获取完整行数据，含 mime_type 和 pdf_object_name
    row = _get_file_full_dict(file_id)
    if row is None:
        raise HTTPException(status_code=404, detail="文件不存在")

    mime_type = row.get("mime_type") or ""
    pdf_object_name = row.get("pdf_object_name") or ""
    minio_object_name = row.get("minio_object_name") or ""
    extension = row.get("extension") or ""

    try:
        # 图片直接返回原图
        is_image = False
        content_type = mime_type
        if mime_type and mime_type.startswith("image/"):
            is_image = True
        elif extension.lower() in IMAGE_EXT_TO_MIME:
            is_image = True
            content_type = IMAGE_EXT_TO_MIME[extension.lower()]
        elif minio_object_name.lower().endswith(".jpg") or minio_object_name.lower().endswith(".jpeg"):
            is_image = True
            content_type = "image/jpeg"
        elif minio_object_name.lower().endswith(".png"):
            is_image = True
            content_type = "image/png"
        elif minio_object_name.lower().endswith(".gif"):
            is_image = True
            content_type = "image/gif"
        elif minio_object_name.lower().endswith(".webp"):
            is_image = True
            content_type = "image/webp"

        if is_image:
            data = cds.download_file_proxy(minio_object_name)
            from starlette.responses import Response
            return Response(
                content=data,
                media_type=content_type,
                headers={
                    "Content-Disposition": f'inline; filename="preview.{content_type.split("/")[-1]}"',
                    "Content-Length": str(len(data)),
                },
            )

        # 有 PDF 版本则返回 PDF
        if pdf_object_name:
            data = cds.download_pdf_proxy(pdf_object_name)
            content_type = "application/pdf"
        # 已经是 PDF
        elif mime_type == "application/pdf" or minio_object_name.lower().endswith(".pdf"):
            data = cds.download_file_proxy(minio_object_name)
            content_type = "application/pdf"
        else:
            raise HTTPException(status_code=404, detail="该文件不支持在线预览")

        from starlette.responses import Response
        return Response(
            content=data,
            media_type=content_type,
            headers={
                "Content-Disposition": f'inline; filename="preview.{content_type.split("/")[-1]}"',
                "Content-Length": str(len(data)),
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"预览失败: {str(e)}")


# ---------- Rename / Move / Delete ----------

class RenameBody(BaseModel):
    name: str
    item_type: Optional[str] = None  # "folder", "file" 或不传（由前端告知类型，解决ID冲突）


@router.put("/file/{file_id}/rename")
def rename_file(file_id: int, body: RenameBody, request: Request = None):
    user_id = require_user_id(request)
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="名称不能为空")
    # 优先使用前端传入的类型（解决 folder/file 表 id 冲突）
    item_type = body.item_type or _get_item_type(file_id)
    if item_type == "folder":
        try:
            cds.rename_folder(file_id, name, user_id)
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e))
    elif item_type == "file":
        try:
            cds.rename_file(file_id, name, user_id)
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e))
    else:
        raise HTTPException(status_code=404, detail="文件或文件夹不存在")
    return {"success": True}


class MoveBody(BaseModel):
    parent_id: Optional[str] = None  # 复合ID


@router.put("/file/{file_id}/move")
def move_file(file_id: int, body: MoveBody, request: Request = None, type: Optional[str] = Query(None)):
    """移动，支持 type 参数：?type=folder 或 ?type=file"""
    user_id = require_user_id(request)
    item_type = _resolve_type_param(type, file_id)
    numeric_parent = 0
    if body.parent_id:
        try:
            _, numeric_parent = decode_id(body.parent_id)
        except ValueError:
            try:
                numeric_parent = int(body.parent_id)
            except ValueError:
                numeric_parent = 0
    if item_type == "folder":
        cds.move_folder(file_id, numeric_parent, user_id)
    elif item_type == "file":
        cds.move_file(file_id, numeric_parent, user_id)
    else:
        raise HTTPException(status_code=404, detail="文件或文件夹不存在")
    return {"success": True}


@router.delete("/file/{file_id}")
def delete_file(file_id: int, request: Request = None, type: Optional[str] = Query(None)):
    """删除，支持 type 参数：?type=folder 或 ?type=file"""
    user_id = require_user_id(request)
    item_type = _resolve_type_param(type, file_id)
    if item_type == "folder":
        cds.delete_folder(file_id, user_id)
    elif item_type == "file":
        cds.delete_file(file_id, user_id)
    else:
        raise HTTPException(status_code=404, detail="文件或文件夹不存在")
    return {"success": True}


# ---------- Breadcrumb ----------

@router.get("/breadcrumb/{file_id}")
def get_breadcrumb(file_id: str, request: Request = None):
    user_id = require_user_id(request)
    numeric_id = 0
    if file_id.startswith("f") or file_id.startswith("d"):
        try:
            _, numeric_id = decode_id(file_id)
        except ValueError:
            try:
                numeric_id = int(file_id)
            except ValueError:
                numeric_id = 0
    else:
        try:
            numeric_id = int(file_id)
        except ValueError:
            numeric_id = 0
    path = cds.get_breadcrumb(numeric_id)
    return {"path": [{"id": encode_id(p.id, "folder"), "name": p.name} for p in path]}


# ---------- Quick Access ----------

class AddQuickAccessBody(BaseModel):
    name: str
    file_id: str  # 复合ID


class UpdateQuickAccessBody(BaseModel):
    name: str


@router.get("/quick-access")
def list_quick_access(request: Request = None):
    user_id = require_user_id(request)
    items = cds.list_quick_access(user_id)
    return {
        "items": [
            {
                "id": item.id,  # QuickAccess 自增ID，非复合ID
                "name": item.name,
                "file_id": encode_id(item.file_id, "folder") if item.is_folder else encode_id(item.file_id, "file"),
                "file_name": item.file_name,
                "is_folder": item.is_folder,
                "sort_order": item.sort_order,
                "created_at": str(item.created_at) if item.created_at else None,
            }
            for item in items
        ]
    }


@router.post("/quick-access")
def add_quick_access(body: AddQuickAccessBody, request: Request = None):
    user_id = require_user_id(request)
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="名称不能为空")
    if not body.file_id:
        raise HTTPException(status_code=400, detail="请指定文件夹")
    try:
        _, numeric_id = decode_id(body.file_id)
    except ValueError:
        numeric_id = int(body.file_id)
    new_id = cds.add_quick_access(user_id, name, numeric_id)
    return {"id": new_id, "success": True}


@router.put("/quick-access/{item_id}")
def update_quick_access(item_id: int, body: UpdateQuickAccessBody, request: Request = None):
    user_id = require_user_id(request)
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="名称不能为空")
    cds.update_quick_access(item_id, user_id, name)
    return {"success": True}


@router.delete("/quick-access/{item_id}")
def delete_quick_access(item_id: int, request: Request = None):
    user_id = require_user_id(request)
    cds.delete_quick_access(item_id, user_id)
    return {"success": True}


# ---------- Proxy Upload ----------

@router.post("/upload/proxy")
async def proxy_upload(
    file: UploadFile = File(...),
    parent_id: Optional[str] = Form(None),  # 复合ID
    relative_path: Optional[str] = Form(None),
    request: Request = None,
):
    user_id = require_user_id(request)
    filename = file.filename
    if not filename or not filename.strip():
        raise HTTPException(status_code=400, detail="文件名为空")

    # 解析相对路径，递归创建文件夹
    target_parent = 0
    if parent_id:
        try:
            _, target_parent = decode_id(parent_id)
        except ValueError:
            try:
                target_parent = int(parent_id)
            except ValueError:
                target_parent = 0
    if relative_path:
        # relative_path 如 "subfolder1/subfolder2"
        parts = [p for p in relative_path.replace("\\", "/").split("/") if p]
        if parts:
            target_parent = cds.ensure_folder_path(parts, target_parent, user_id)

    filename = _validate_file_type(filename)
    contents = await file.read()
    file_size = len(contents)
    try:
        ext = ""
        dot_idx = filename.rfind(".")
        if dot_idx >= 0:
            ext = filename[dot_idx:].lower()
        object_name = f"cloud_drive/{user_id}/{uuid.uuid4().hex}{ext}"
        import io
        cds.proxy_upload(io.BytesIO(contents), file_size, file.content_type, object_name)

        pdf_object_name = cds.convert_and_upload_pdf(contents, ext, file.content_type, user_id)

        file_id = cds.create_file(
            user_id=user_id,
            folder_id=target_parent,
            name=filename,
            extension=ext,
            size=file_size,
            sha256="",
            minio_bucket=settings.minio_bucket,
            minio_object_name=object_name,
        )
        if pdf_object_name:
            cds.update_pdf_object_name(file_id, pdf_object_name)
        return {"id": encode_id(file_id, "file"), "name": filename, "hasPdf": pdf_object_name is not None}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"上传失败: {str(e)}")
