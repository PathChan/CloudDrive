"""测试包含 IP 地址的 LDAP 连接"""
import sys, socket
sys.path.insert(0, ".")

# 先拿 IP
print("=== 查询 IP ===")
try:
    ip = socket.gethostbyname("dccntj002.corp.novocorp.net")
    print(f"[OK] IP = {ip}")
except Exception as e:
    print(f"DNS 解析失败: {e}")
    # 试试常用的内网 DNS 服务器
    print("\n可能需要在网卡配置里加公司 DNS 服务器")
    sys.exit(1)
