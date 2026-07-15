from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.routers import auth, cloud_drive, microsoft_auth


def check_connections():
    """Startup check for MySQL, Redis, and MinIO connections."""
    import mysql.connector

    # Check MySQL
    try:
        conn = mysql.connector.connect(
            host=settings.db_host,
            port=settings.db_port,
            database=settings.db_name,
            user=settings.db_user,
            password=settings.db_password,
            connect_timeout=5,
        )
        conn.close()
        print(f"[OK] MySQL connected ({settings.db_host}:{settings.db_port})")
    except Exception as e:
        print(f"[WARN] MySQL connection failed: {e}")

    # Check Redis
    try:
        import redis as redis_lib
        r = redis_lib.Redis(host=settings.redis_host, port=settings.redis_port, socket_connect_timeout=3)
        r.ping()
        r.close()
        print(f"[OK] Redis connected ({settings.redis_host}:{settings.redis_port})")
    except Exception as e:
        print(f"[WARN] Redis connection failed: {e}")

    # Check MinIO
    try:
        from minio import Minio
        client = Minio(
            settings.minio_endpoint.replace("http://", "").replace("https://", ""),
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )
        client.list_buckets()
        print(f"[OK] MinIO connected ({settings.minio_endpoint})")
    except Exception as e:
        print(f"[WARN] MinIO connection failed: {e}")


def rebuild_cache_if_empty():
    """启动时检查 Redis 是否已有持久化目录数据，为空则从 DB 全量重建"""
    from app.services.redis_client import get_redis
    from app.services.drive_service import rebuild_all_cache

    r = get_redis()
    if r is None:
        print("[WARN] Redis 不可用，跳过缓存重建")
        return

    # 检查是否有任何持久化键
    persist_prefixes = ("fc:", "bc:", "fi:", "fi2:", "fav:", "ts", "trash:")
    keys = r.keys("*") or []
    has_persist_data = any(
        k.startswith(p) for k in keys for p in persist_prefixes
    )

    if not has_persist_data:
        print("[INFO] Redis 持久化目录为空，开始从数据库全量重建...")
        result = rebuild_all_cache()
        if result.get("status") == "ok":
            print(f"[OK] Redis 全量重建完成: {result['keys_written']} 个键 ({result['folders']} 文件夹, {result['files']} 文件, {result['users']} 用户)")
        elif result.get("status") == "skipped":
            print(f"[WARN] Redis 重建已跳过: {result.get('reason')}")
        else:
            print(f"[ERROR] Redis 重建失败: {result.get('error')}")
    else:
        # 有数据但做快速一致性检查
        from app.services.drive_service import verify_cache_consistency as vcc
        result = vcc()
        if result.get("status") == "consistent":
            print(f"[OK] Redis 持久化目录校验通过: {result['keys_count']} 个键, {result['users']} 个用户")
        else:
            print(f"[WARN] Redis 一致性校验发现异常: {result}")
            # 只警告不自动重建，避免启动过慢


@asynccontextmanager
async def lifespan(app: FastAPI):
    check_connections()
    rebuild_cache_if_empty()
    yield


app = FastAPI(title="LiteDoc", description="LiteDoc Backend Service", lifespan=lifespan)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(cloud_drive.router)
app.include_router(microsoft_auth.router)


@app.get("/admin/health")
def health_check():
    """基础健康检查"""
    return {"status": "ok", "service": "LiteDoc"}


@app.post("/admin/rebuild-cache")
def admin_rebuild_cache():
    """手动触发 Redis 全量重建（用于运维诊断）"""
    from app.services.drive_service import rebuild_all_cache
    result = rebuild_all_cache()
    return result


@app.get("/admin/verify-cache")
def admin_verify_cache():
    """手动触发 Redis 一致性校验"""
    from app.services.drive_service import verify_cache_consistency
    result = verify_cache_consistency()
    return result


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=settings.server_port,
        limit_concurrency=100,
    )