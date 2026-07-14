from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class User(BaseModel):
    id: int
    username: str
    email: str


class FolderItem(BaseModel):
    id: int
    user_id: int
    parent_id: int = 0
    name: str
    path_node: str = ""
    level: int = 1
    is_deleted: bool = False
    create_time: Optional[datetime] = None
    update_time: Optional[datetime] = None


class FileItem(BaseModel):
    id: int
    user_id: int
    folder_id: int = 0
    name: str
    extension: str = ""
    size: int = 0
    sha256: str = ""
    minio_bucket: str = ""
    minio_object_name: str = ""
    is_deleted: bool = False
    create_time: Optional[datetime] = None
    update_time: Optional[datetime] = None


class FavoriteItem(BaseModel):
    id: int
    user_id: int
    file_id: int
    created_at: Optional[datetime] = None


class DeleteLogItem(BaseModel):
    id: int
    file_id: int
    deleted_by: int
    deleted_at: Optional[datetime] = None


class BreadcrumbItem(BaseModel):
    id: int
    name: str


class QuickAccessItem(BaseModel):
    id: int
    user_id: int
    name: str
    file_id: int
    file_name: Optional[str] = None
    is_folder: bool = True
    sort_order: int = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None