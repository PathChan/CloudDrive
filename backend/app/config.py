from pydantic_settings import BaseSettings
from typing import Set, List, Tuple, Optional
import re
import logging

logger = logging.getLogger(__name__)


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
    ldap_host: str = ""
    ldap_port: int = 389
    ldap_use_ssl: bool = False
    ldap_bind_user: str = ""
    ldap_bind_password: str = ""
    ldap_user_search_base: str = ""
    ldap_user_filter: str = "(&(objectClass=user)(sAMAccountName={username}))"
    ldap_group_attribute: str = "memberOf"
    ldap_user_group_dn: str = ""
    ldap_admin_group_dn: str = ""
    ldap_email_attribute: str = "mail,userPrincipalName"
    ldap_display_name_attribute: str = "displayName"

    # User
    user_secret_key: str

    # ===== LDAP DN 解析与构建 =====

    # 可选的独立 OU/DC 配置（优先级低于 ldap_user_search_base）
    ldap_ou_components: str = ""   # 逗号分隔，如 "UserAccounts,CNTJ,Company"
    ldap_dc_components: str = ""   # 逗号分隔，如 "corp,novocorp,net"

    @staticmethod
    def parse_dn_components(dn: str) -> dict:
        """将 LDAP DN 字符串解析为结构化组件。
        
        例如 "OU=UserAccounts,OU=CNTJ,DC=corp,DC=novocorp,DC=net"
        返回: {
            "ous": ["UserAccounts", "CNTJ"],
            "dcs": ["corp", "novocorp", "net"],
            "all": [("OU", "UserAccounts"), ("OU", "CNTJ"), ("DC", "corp"), ("DC", "novocorp"), ("DC", "net")]
        }
        """
        if not dn or not dn.strip():
            return {"ous": [], "dcs": [], "all": [], "cn": None, "raw": dn}

        components = []
        ous = []
        dcs = []
        cn = None

        # 按逗号分割（简单分割，不处理转义逗号）
        parts = [p.strip() for p in dn.split(",") if p.strip()]
        for part in parts:
            match = re.match(r'^(OU|DC|CN)\s*=\s*(.+)$', part, re.IGNORECASE)
            if match:
                attr_type = match.group(1).upper()
                attr_value = match.group(2)
                components.append((attr_type, attr_value))
                if attr_type == "OU":
                    ous.append(attr_value)
                elif attr_type == "DC":
                    dcs.append(attr_value)
                elif attr_type == "CN":
                    cn = attr_value

        return {
            "ous": ous,
            "dcs": dcs,
            "all": components,
            "cn": cn,
            "raw": dn,
        }

    @staticmethod
    def build_search_base(ou_components: List[str], dc_components: List[str]) -> str:
        """从 OU 和 DC 组件构建完整的搜索基 DN。
        
        参数:
            ou_components: OU 组件列表，按层级从低到高排列，如 ["UserAccounts", "CNTJ", "Company"]
            dc_components: DC 组件列表，如 ["corp", "novocorp", "net"]
        
        返回:
            完整的 DN，如 "OU=UserAccounts,OU=CNTJ,OU=Company,DC=corp,DC=novocorp,DC=net"
        """
        parts = []
        for ou in ou_components:
            ou = ou.strip()
            if ou:
                parts.append(f"OU={ou}")
        for dc in dc_components:
            dc = dc.strip()
            if dc:
                parts.append(f"DC={dc}")
        return ",".join(parts)

    @property
    def ldap_search_base_components(self) -> dict:
        """获取解析后的搜索基 DN 组件（优先从 ldap_user_search_base 解析，否则用独立字段构建）"""
        if self.ldap_user_search_base and self.ldap_user_search_base.strip():
            return self.parse_dn_components(self.ldap_user_search_base)

        # 使用独立字段构建
        ous = [o.strip() for o in self.ldap_ou_components.split(",") if o.strip()] if self.ldap_ou_components else []
        dcs = [d.strip() for d in self.ldap_dc_components.split(",") if d.strip()] if self.ldap_dc_components else []
        if not dcs:
            return {"ous": ous, "dcs": dcs, "all": [], "cn": None, "raw": ""}

        dn = self.build_search_base(ous, dcs)
        return self.parse_dn_components(dn)

    @property
    def ldap_group_search_base(self) -> str:
        """从 group DN 中提取组搜索基 DN（去掉 CN 部分，只保留 OU/DC 前缀）。
        
        例如从 "CN=STJ_Asset_Records_Portal-user,OU=GroupsMan,OU=UsersAndGroups,DC=corp,DC=novocorp,DC=net"
        提取出 "OU=GroupsMan,OU=UsersAndGroups,DC=corp,DC=novocorp,DC=net"
        
        用于反向组查询：直接查目标组获取 member 列表，而非遍历用户的 memberOf。
        """
        # 优先用 user_group_dn 提取，因为两个组的前缀相同
        dn = self.ldap_user_group_dn or self.ldap_admin_group_dn
        if not dn or not dn.strip():
            return ""
        comps = self.parse_dn_components(dn)
        if not comps["dcs"]:
            return ""
        # 重建 DN，跳过 CN 部分，只保留 OU + DC
        return self.build_search_base(comps["ous"], comps["dcs"])

    @property
    def ldap_group_filter(self) -> str:
        """构建反向组查询的 LDAP 过滤器。
        
        同时查询 user 和 admin 两个组，只返回 cn 和 member 属性。
        格式: (|(cn=STJ_Asset_Records_Portal-user)(cn=STJ_Asset_Records_Portal-admin))
        """
        user_cn = None
        admin_cn = None
        if self.ldap_user_group_dn:
            uc = self.parse_dn_components(self.ldap_user_group_dn)
            user_cn = uc.get("cn")
        if self.ldap_admin_group_dn:
            ac = self.parse_dn_components(self.ldap_admin_group_dn)
            admin_cn = ac.get("cn")
        parts = []
        if user_cn:
            parts.append(f"(cn={user_cn})")
        if admin_cn:
            parts.append(f"(cn={admin_cn})")
        if len(parts) == 1:
            return parts[0]
        return f"(|{''.join(parts)})"

    def build_user_search_base(self, additional_ou: Optional[str] = None) -> str:
        """构建精准的用户搜索基 DN。
        
        参数:
            additional_ou: 可选，额外追加的 OU（用于缩小搜索范围）
        
        返回:
            完整的搜索基 DN 字符串
        
        如果 ldap_user_search_base 已配置，直接使用它；
        否则从独立 OU/DC 配置字段构建。
        """
        if self.ldap_user_search_base and self.ldap_user_search_base.strip():
            base = self.ldap_user_search_base.strip()
            if additional_ou:
                base = f"OU={additional_ou},{base}"
            return base

        comps = self.ldap_search_base_components
        ous = list(comps["ous"])
        if additional_ou:
            ous.insert(0, additional_ou)
        return self.build_search_base(ous, comps["dcs"])

    @property
    def beta_user_ids(self) -> Set[int]:
        return {int(x.strip()) for x in self.beta_users.split(",") if x.strip()}

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()