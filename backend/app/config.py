from pydantic_settings import BaseSettings
from typing import Set


class Settings(BaseSettings):
    # Server
    server_port: int = 8082

    # Database
    db_host: str
    db_port: int
    db_name: str
    db_user: str
    db_password: str

    # JWT
    jwt_secret: str
    jwt_expiration_hours: int = 168

    # MinIO
    minio_endpoint: str
    minio_access_key: str
    minio_secret_key: str
    minio_bucket: str
    minio_secure: bool = False

    # Cloud Drive
    beta_users: str = "1,5"

    # Redis
    redis_host: str = "127.0.0.1"
    redis_port: int = 6379

    # Microsoft SSO
    microsoft_client_id: str = ""
    microsoft_client_secret: str = ""
    microsoft_tenant_id: str = "common"
    microsoft_redirect_uri: str = "http://localhost:8082/api/auth/microsoft/callback"
    frontend_url: str = "http://localhost:5174"

    # LDAP
    ldap_enabled: bool = False
    ldap_server: str = ""
    ldap_bind_dn: str = ""
    ldap_bind_password: str = ""
    ldap_base_dn: str = ""
    ldap_user_filter: str = "(sAMAccountName={})"

    # User
    user_secret_key: str

    @property
    def beta_user_ids(self) -> Set[int]:
        return {int(x.strip()) for x in self.beta_users.split(",") if x.strip()}

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()