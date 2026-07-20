import json
import redis as redis_lib

r = redis_lib.Redis(host='127.0.0.1', port=6379, socket_connect_timeout=3, decode_responses=True)

# 检查 fc:0 是否存在
raw = r.get('fc:0')
if raw is None:
    print('fc:0 does NOT exist in Redis')
    print('\nLooking for old format keys...')
    keys = r.keys('fc:*:0')
    print(f'Old format keys matching fc:*:0: {keys}')
else:
    print('fc:0 EXISTS in Redis')
    try:
        data = json.loads(raw)
        folders = data.get('folders', [])
        files = data.get('files', [])
        print(f'folders count: {len(folders)}, files count: {len(files)}')
        if folders:
            print(f'First folder keys: {list(folders[0].keys())[:10]}')
        if files:
            print(f'First file keys: {list(files[0].keys())[:10]}')
    except Exception as e:
        print(f'Parse error: {e}')
        print(f'Raw (first 500 chars): {raw[:500]}')

# 检查是否有其他 fc: 键
fc_keys = r.keys('fc:*')
print(f'\nTotal fc:* keys: {len(fc_keys)}')
if fc_keys:
    print(f'Sample keys: {fc_keys[:5]}')

r.close()
