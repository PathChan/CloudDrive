import requests
import json

# 登录获取admin token
resp = requests.post('http://127.0.0.1:5000/api/auth/login', json={'email':'123456', 'password':'123456'})
print('=== Admin Login ===')
admin_data = resp.json()
print(f'Status: {resp.status_code}')
print(f'Role: {admin_data.get("user",{}).get("role","?")}')
token_admin = admin_data['token']

# 用admin token获取文件列表
resp2 = requests.get('http://127.0.0.1:5000/api/cloud-drive/files', headers={'Authorization': 'Bearer ' + token_admin})
print('\n=== Admin Files ===')
print(f'Status: {resp2.status_code}')
try:
    data2 = resp2.json()
    files = data2.get('files', [])
    print(f'Files count: {len(files)}')
    print(f'totalSize: {data2.get("totalSize", "?")}')
    for f in files[:3]:
        ftype = "folder" if f.get("is_folder") else "file"
        print(f'  - {f.get("name","?")} ({ftype}, id={f.get("id","?")})')
except Exception as e:
    print(f'Error: {e}')
    print(f'Raw: {resp2.text[:500]}')

# 登录获取user token
resp3 = requests.post('http://127.0.0.1:5000/api/auth/login', json={'email':'12345678', 'password':'12345678'})
print('\n=== User Login ===')
user_data = resp3.json()
print(f'Status: {resp3.status_code}')
print(f'Role: {user_data.get("user",{}).get("role","?")}')
token_user = user_data['token']

# 用user token获取文件列表
resp4 = requests.get('http://127.0.0.1:5000/api/cloud-drive/files', headers={'Authorization': 'Bearer ' + token_user})
print('\n=== User Files ===')
print(f'Status: {resp4.status_code}')
try:
    data4 = resp4.json()
    files4 = data4.get('files', [])
    print(f'Files count: {len(files4)}')
    print(f'totalSize: {data4.get("totalSize", "?")}')
    for f in files4[:3]:
        ftype = "folder" if f.get("is_folder") else "file"
        print(f'  - {f.get("name","?")} ({ftype}, id={f.get("id","?")})')
except Exception as e:
    print(f'Error: {e}')
    print(f'Raw: {resp4.text[:500]}')

print('\n=== Direct DB check (root folders) ===')
import mysql.connector
conn = mysql.connector.connect(
    host='127.0.0.1', port=3306, database='LiteDoc',
    user='root', password='8KfR3xZ9_mPqW0asWxcxx1r0OkM',
)
cursor = conn.cursor(dictionary=True)
cursor.execute("SELECT COUNT(*) as cnt FROM folder WHERE parent_id = 0 AND is_deleted = 0")
print(f'Root folders (parent_id=0): {cursor.fetchone()["cnt"]}')
cursor.execute("SELECT COUNT(*) as cnt FROM file WHERE folder_id = 0 AND is_deleted = 0")
print(f'Root files (folder_id=0): {cursor.fetchone()["cnt"]}')
cursor.execute("SELECT COUNT(*) as cnt FROM folder WHERE is_deleted = 0")
print(f'Total non-deleted folders: {cursor.fetchone()["cnt"]}')
cursor.execute("SELECT COUNT(*) as cnt FROM file WHERE is_deleted = 0")
print(f'Total non-deleted files: {cursor.fetchone()["cnt"]}')
cursor.close()
conn.close()
