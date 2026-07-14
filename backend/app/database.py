import mysql.connector
from mysql.connector.pooling import MySQLConnectionPool
from app.config import settings

_pool: MySQLConnectionPool | None = None


def get_pool() -> MySQLConnectionPool:
    global _pool
    if _pool is None:
        _pool = MySQLConnectionPool(
            pool_name="cloud_drive_pool",
            pool_size=10,
            pool_reset_session=True,
            host=settings.db_host,
            port=settings.db_port,
            database=settings.db_name,
            user=settings.db_user,
            password=settings.db_password,
            use_unicode=True,
            charset="utf8mb4",
        )
    return _pool


def get_connection():
    return get_pool().get_connection()