FROM python:3.12-slim

# 安装 LibreOffice（含 writer / calc / impress 以支持所有 Office 格式）
RUN apt-get update && apt-get install -y --no-install-recommends \
    libreoffice-headless \
    libreoffice-writer \
    libreoffice-calc \
    libreoffice-impress \
    fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 复制依赖文件并安装 Python 包
COPY pyproject.toml uv.lock ./
RUN pip install uv && uv sync --no-dev

# 复制源码
COPY . .

EXPOSE 8082

CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8082"]