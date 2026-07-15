import asyncio
import re
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.routers import auth, cloud_drive, microsoft_auth

logger = logging.getLogger(__name__)

# 后台一致性检查间隔（秒），默认 30 分钟
CACHE_CHECK_INTERVAL = 1800


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
    """启动时检查 Redis 是否已有持久化目录数据，为空则从 DB 全量重建
    
    增强功能：
    1. 自动检测一致性异常
    2. 发现异常时自动触发重建
    3. 记录详细告警日志
    """
    from app.services.redis_client import get_redis
    from app.services.drive_service import rebuild_all_cache, verify_cache_consistency
    
    r = get_redis()
    if r is None:
        logger.warning("Redis 不可用，跳过缓存重建")
        return

    # 检查是否有任何持久化键
    persist_prefixes = ("fc:", "bc:", "fi:", "fi2:", "fav:", "ts", "trash:")
    keys = r.keys("*") or []
    has_persist_data = any(
        k.startswith(p) for k in keys for p in persist_prefixes
    )

    if not has_persist_data:
        logger.info("Redis 持久化目录为空，开始从数据库全量重建...")
        result = rebuild_all_cache()
        if result.get("status") == "ok":
            logger.info(f"Redis 全量重建完成: {result['keys_written']} 个键 ({result['folders']} 文件夹, {result['files']} 文件, {result['users']} 用户)")
        elif result.get("status") == "skipped":
            logger.warning(f"Redis 重建已跳过: {result.get('reason')}")
        else:
            logger.error(f"Redis 重建失败: {result.get('error')}")
    else:
        # 有数据但做快速一致性检查
        result = verify_cache_consistency()
        if result.get("status") == "consistent":
            logger.info(f"Redis 持久化目录校验通过: {result['keys_count']} 个键, {result['users']} 个用户")
        else:
            # 发现不一致，记录告警并自动修复
            _handle_consistency_issues(result)


def _handle_consistency_issues(result: dict):
    """处理一致性校验结果，根据问题类型选择修复策略
    
    参数:
        result: verify_cache_consistency() 返回的结果字典
    """
    from app.services.drive_service import rebuild_all_cache, repair_missing_breadcrumbs
    
    issues = result.get('issues', [])
    issues_count = result.get('issues_count', 0)
    logger.warning(f"Redis 一致性校验发现异常: {issues_count} 个问题")
    for issue in issues[:10]:  # 只记录前10个问题避免日志过长
        logger.warning(f"  - {issue}")
    if issues_count > 10:
        logger.warning(f"  ... 还有 {issues_count - 10} 个问题")
    
    # 分析异常类型，优先使用针对性修复
    breadcrumb_issues = [i for i in issues if i.startswith('面包屑缺失')]
    
    if breadcrumb_issues and len(breadcrumb_issues) == issues_count:
        # 只有面包屑缺失，使用针对性修复（更快）
        folder_ids = []
        for issue in breadcrumb_issues:
            match = re.search(r'folder_id=(\d+)', issue)
            if match:
                folder_ids.append(int(match.group(1)))
        
        if folder_ids:
            logger.info(f"开始针对性修复 {len(folder_ids)} 个缺失的面包屑...")
            repair_result = repair_missing_breadcrumbs(folder_ids)
            if repair_result.get("status") == "ok":
                logger.info(f"面包屑修复完成: {repair_result['repaired']} 个成功, {repair_result['failed']} 个失败")
            else:
                logger.error(f"面包屑修复失败: {repair_result.get('error')}")
    else:
        # 有多种类型的不一致，执行全量重建
        logger.info("发现多种一致性问题，开始全量重建...")
        rebuild_result = rebuild_all_cache()
        if rebuild_result.get("status") == "ok":
            logger.info(f"全量重建完成: {rebuild_result['keys_written']} 个键已修复")
        else:
            logger.error(f"全量重建失败: {rebuild_result.get('error')}")


async def periodic_cache_consistency_check():
    """后台定期任务：检查 Redis 与数据库一致性，发现异常自动修复
    
    默认每 30 分钟执行一次，可通过 CACHE_CHECK_INTERVAL 配置调整。
    """
    from app.services.drive_service import verify_cache_consistency
    from app.services.redis_client import get_redis
    
    while True:
        await asyncio.sleep(CACHE_CHECK_INTERVAL)
        
        # 检查 Redis 是否可用
        r = get_redis()
        if r is None:
            logger.warning("后台检查: Redis 不可用，跳过本次一致性校验")
            continue
        
        try:
            # 在线程池中执行同步的一致性校验
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, verify_cache_consistency)
            
            if result.get("status") == "consistent":
                logger.debug(f"后台一致性检查通过: {result.get('keys_count')} 个键")
            else:
                logger.warning("后台一致性检查发现异常，开始修复...")
                # 在线程池中执行修复
                await loop.run_in_executor(None, _handle_consistency_issues, result)
        except Exception as e:
            logger.error(f"后台一致性检查失败: {e}", exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    check_connections()
    rebuild_cache_if_empty()
    
    # 启动后台定期一致性检查任务
    periodic_task = asyncio.create_task(periodic_cache_consistency_check())
    logger.info(f"后台 Redis 一致性检查已启动，间隔 {CACHE_CHECK_INTERVAL} 秒")
    
    yield
    
    # 关闭时取消后台任务
    periodic_task.cancel()
    try:
        await periodic_task
    except asyncio.CancelledError:
        pass


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