"""
PDF 转换服务（已禁用）
如需启用：安装 LibreOffice 后取消下方注释即可。
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def _is_convertible(ext: str) -> bool:
    return False


def convert_to_pdf(data: bytes, ext: str, mime_type: Optional[str] = None) -> Optional[bytes]:
    """PDF 转换已禁用，直接返回 None。"""
    return None


# ========== 以下是 LibreOffice 转换代码，安装后取消注释 ==========
"""
import os
import subprocess
import tempfile

CONVERTIBLE_EXTS = frozenset({
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".txt", ".csv", ".html", ".htm", ".md", ".rtf",
})

def _is_convertible(ext: str) -> bool:
    return ext.lower() in CONVERTIBLE_EXTS

def _soffice_command() -> str:
    for candidate in ("soffice", "libreoffice", "/usr/bin/libreoffice"):
        try:
            subprocess.run(
                [candidate, "--headless", "--version"],
                capture_output=True, timeout=10
            )
            return candidate
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired, subprocess.CalledProcessError):
            continue
    return "soffice"

def convert_to_pdf(data: bytes, ext: str, mime_type: Optional[str] = None) -> Optional[bytes]:
    if not _is_convertible(ext):
        return None
    cmd = _soffice_command()
    ext_lower = ext.lower()
    with tempfile.TemporaryDirectory(prefix="litedoc_convert_") as tmpdir:
        input_name = f"input{ext_lower}"
        input_path = os.path.join(tmpdir, input_name)
        with open(input_path, "wb") as f:
            f.write(data)
        try:
            subprocess.run(
                [cmd, "--headless", "--convert-to", "pdf", "--outdir", tmpdir, input_path],
                capture_output=True, timeout=120, check=True,
            )
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError, FileNotFoundError) as e:
            logger.warning("LibreOffice 转换失败 (%s): %s", ext, e)
            return None
        base = os.path.splitext(input_name)[0]
        pdf_path = os.path.join(tmpdir, f"{base}.pdf")
        if not os.path.exists(pdf_path):
            logger.warning("LibreOffice 未生成 PDF 文件: %s", input_path)
            return None
        with open(pdf_path, "rb") as f:
            return f.read()
"""