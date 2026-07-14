# 复合 ID 编码/解码工具
# 用于解决 folder 和 file 表 auto_increment ID 重复的问题
# 编码规则: "f{numeric_id}" = folder, "d{numeric_id}" = file
# 例: folder.id=2 → "f2",  file.id=2 → "d2"


def encode_id(numeric_id: int, item_type: str) -> str:
    """将数字 ID 和类型编码为复合 ID 字符串"""
    prefix = "f" if item_type == "folder" else "d"
    return f"{prefix}{numeric_id}"


def decode_id(compound_id: str) -> tuple[str, int]:
    """将复合 ID 字符串解码为 (item_type, numeric_id)
    item_type 为 "folder" 或 "file"
    """
    if not compound_id:
        raise ValueError(f"无效的复合ID: {compound_id}")
    prefix = compound_id[0]
    try:
        numeric_id = int(compound_id[1:])
    except (ValueError, IndexError):
        raise ValueError(f"无效的复合ID: {compound_id}")
    if prefix == "f":
        return ("folder", numeric_id)
    elif prefix == "d":
        return ("file", numeric_id)
    else:
        raise ValueError(f"无效的复合ID前缀: {compound_id}")


def is_folder_id(compound_id: str) -> bool:
    """判断复合 ID 是否为文件夹"""
    return compound_id.startswith("f")


def is_file_id(compound_id: str) -> bool:
    """判断复合 ID 是否为文件"""
    return compound_id.startswith("d")


def encode_favorite_key(numeric_id: int, item_type: str) -> str:
    """收藏专用编码 - 用于嵌入 favorite_ids 集合中"""
    return encode_id(numeric_id, item_type)
