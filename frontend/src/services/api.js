const BASE_URL = '/api/cloud-drive'
const AUTH_URL = '/api/auth'

// 从复合ID中提取数字ID（如 "f2" → 2, "d3" → 3, "2" → 2）
function getNumericId(compoundId) {
  if (typeof compoundId === 'string' && (compoundId.startsWith('f') || compoundId.startsWith('d'))) {
    return parseInt(compoundId.substring(1))
  }
  return compoundId
}

// 判断复合ID是否为文件夹
function isFolderCompound(compoundId) {
  return typeof compoundId === 'string' && compoundId.startsWith('f')
}

// 从复合ID获取类型字符串
function getTypeFromId(compoundId) {
  return isFolderCompound(compoundId) ? 'folder' : 'file'
}

async function request(url, options = {}) {
  const token = localStorage.getItem('token')
  const headers = {
    'Content-Type': 'application/json',
    ...(token ? { 'Authorization': `Bearer ${token}` } : {}),
    ...options.headers,
  }
  try {
    const res = await fetch(`${BASE_URL}${url}`, { ...options, headers })
    const contentType = res.headers.get('content-type') || ''
    let data
    if (contentType.includes('application/json')) {
      data = await res.json()
    } else {
      const text = await res.text()
      data = { error: text.substring(0, 200) }
    }
    if (!res.ok) {
      throw new Error(data.detail || data.error || '请求失败')
    }
    return data
  } catch (error) {
    console.error('API Error:', error)
    throw error
  }
}

async function authRequest(url, options = {}) {
  try {
    const res = await fetch(`${AUTH_URL}${url}`, {
      ...options,
      headers: {
        'Content-Type': 'application/json',
        ...options.headers,
      },
    })
    const contentType = res.headers.get('content-type') || ''
    let data
    if (contentType.includes('application/json')) {
      data = await res.json()
    } else {
      const text = await res.text()
      data = { error: text.substring(0, 200) }
    }
    if (!res.ok) {
      throw new Error(data.detail || data.error || '请求失败')
    }
    // 检查业务层错误（后端返回 HTTP 200 但包含 error 字段）
    if (data.error) {
      throw new Error(data.error)
    }
    return data
  } catch (error) {
    console.error('Auth API Error:', error)
    throw error
  }
}

export const api = {
  auth: {
    login: (data) => authRequest('/login', { method: 'POST', body: JSON.stringify(data) }),
    register: (data) => authRequest('/register', { method: 'POST', body: JSON.stringify(data) }),
    ldapLogin: (data) => authRequest('/ldap/login', { method: 'POST', body: JSON.stringify(data) }),
  },
  cloudDrive: {
    list: (params = {}) => {
      const query = new URLSearchParams()
      if (params.parent_id) query.set('parent_id', params.parent_id)
      const qs = query.toString()
      return request(`/files${qs ? '?' + qs : ''}`)
    },
    createFolder: (name, parentId) => request('/folder', { method: 'POST', body: JSON.stringify({ name, parent_id: parentId }) }),
    getUploadPresign: (filename, parentId, fileSize) => request('/upload/presign', { method: 'POST', body: JSON.stringify({ filename, parent_id: parentId, file_size: fileSize }) }),
    confirmUpload: (data) => request('/upload/confirm', { method: 'POST', body: JSON.stringify(data) }),
    download: (fileId) => request(`/file/${getNumericId(fileId)}/download`),
    getDownloadProxyUrl: (fileId, inline = false) => {
      const params = new URLSearchParams()
      params.set('token', localStorage.getItem('token') || '')
      if (inline) params.set('inline', '1')
      return `${BASE_URL}/file/${getNumericId(fileId)}/download-proxy?${params.toString()}`
    },
    getPreviewUrl: (fileId) => {
      const params = new URLSearchParams()
      params.set('token', localStorage.getItem('token') || '')
      return `${BASE_URL}/file/${getNumericId(fileId)}/preview?${params.toString()}`
    },
    rename: (fileId, name, itemType) => {
      const numericId = getNumericId(fileId)
      return request(`/file/${numericId}/rename`, {
        method: 'PUT',
        body: JSON.stringify({ name, item_type: itemType || getTypeFromId(fileId) }),
      })
    },
    move: (fileId, parentId) => {
      const numericId = getNumericId(fileId)
      const type = getTypeFromId(fileId)
      return request(`/file/${numericId}/move?type=${type}`, {
        method: 'PUT',
        body: JSON.stringify({ parent_id: parentId }),
      })
    },
    deleteFile: (fileId) => {
      const numericId = getNumericId(fileId)
      const type = getTypeFromId(fileId)
      return request(`/file/${numericId}?type=${type}`, { method: 'DELETE' })
    },
    getBreadcrumb: (fileId) => request(`/breadcrumb/${getNumericId(fileId)}`),

    search: (keyword, rootFolderId = 0) => request(`/files/search?keyword=${encodeURIComponent(keyword)}&root_folder_id=${rootFolderId}`),

    listTrash: () => request('/trash'),
    restoreFile: (fileId) => {
      const numericId = getNumericId(fileId)
      const type = getTypeFromId(fileId)
      return request(`/trash/restore/${numericId}?type=${type}`, { method: 'POST' })
    },
    permanentDelete: (fileId) => {
      const numericId = getNumericId(fileId)
      const type = getTypeFromId(fileId)
      return request(`/trash/permanent/${numericId}?type=${type}`, { method: 'DELETE' })
    },
    emptyTrash: () => request('/trash/empty', { method: 'DELETE' }),

    listFavorites: () => request('/favorites'),
    toggleFavorite: (fileId, favorite) => {
      const numericId = getNumericId(fileId)
      const itemType = getTypeFromId(fileId)
      return request(`/file/${numericId}/favorite`, {
        method: 'POST',
        body: JSON.stringify({ favorite, item_type: itemType }),
      })
    },

    checkName: (name, parentId = 'f0', excludeId = '') => {
      const params = new URLSearchParams({ name, parent_id: parentId })
      if (excludeId) params.set('exclude_id', excludeId)
      return request(`/check-name?${params.toString()}`)
    },

    batchDelete: (fileIds) => {
      // fileIds 是复合ID数组，如 ["f2", "d3"]
      const items = fileIds.map(id => ({ id }))
      return request('/batch/delete', { method: 'POST', body: JSON.stringify({ items }) })
    },
    batchMove: (fileIds, targetParentId) => {
      const items = fileIds.map(id => ({ id }))
      return request('/batch/move', {
        method: 'POST',
        body: JSON.stringify({ items, target_parent_id: targetParentId }),
      })
    },
    copy: (fileId, targetParentId) => request('/file/copy', { method: 'POST', body: JSON.stringify({ file_id: fileId, target_parent_id: targetParentId }) }),
    batchCopy: (fileIds, targetParentId) => {
      const items = fileIds.map(id => ({ id }))
      return request('/batch/copy', {
        method: 'POST',
        body: JSON.stringify({ items, target_parent_id: targetParentId }),
      })
    },

    listQuickAccess: () => request('/quick-access'),
    addQuickAccess: (name, fileId) => request('/quick-access', { method: 'POST', body: JSON.stringify({ name, file_id: fileId }) }),
    updateQuickAccess: (itemId, name) => request(`/quick-access/${itemId}`, { method: 'PUT', body: JSON.stringify({ name }) }),
    deleteQuickAccess: (itemId) => request(`/quick-access/${itemId}`, { method: 'DELETE' }),
  },
  oss: {
    /**
     * 预签名上传三步流程（推荐方式）：
     * 1. 获取预签名 URL → 2. 直接上传到 MinIO → 3. 确认上传创建记录
     * 
     * 相比代理上传，优势：
     * - 文件直达 MinIO，无后端中转，速度更快
     * - 不占用后端内存/带宽
     * - 支持超大文件
     */
    presignUpload: async (file, parentId, relativePath, onProgress, retryCount = 3) => {
      const token = localStorage.getItem('token')
      if (!token) throw new Error('未登录')
      
      // 如果存在 relativePath，先创建对应文件夹结构（mkdir -p 语义）
      let targetParentId = parentId
      if (relativePath) {
        const parts = relativePath.replace(/\\/g, '/').split('/').filter(Boolean)
        for (const folderName of parts) {
          const folderRes = await fetch(`${BASE_URL}/folder`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
            body: JSON.stringify({ name: folderName, parent_id: targetParentId }),
          })
          if (!folderRes.ok) {
            const errData = await folderRes.json().catch(() => ({ detail: '创建文件夹失败' }))
            throw new Error(errData.detail || '创建文件夹失败')
          }
          const folderData = await folderRes.json()
          targetParentId = folderData.id  // 新建或已存在的文件夹ID
        }
      }
      
      // 带重试的上传实现
      const doUpload = async (attempt) => {
        // Step 1: 获取预签名 URL
        const presignRes = await fetch(`${BASE_URL}/upload/presign`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
          body: JSON.stringify({ filename: file.name, parent_id: targetParentId, file_size: file.size }),
        })
        if (!presignRes.ok) {
          const err = await presignRes.json().catch(() => ({ detail: '获取上传地址失败' }))
          throw new Error(err.detail || '获取上传地址失败')
        }
        const { uploadUrl, objectName } = await presignRes.json()
        
        // Step 2: 直接上传到 MinIO
        await new Promise((resolve, reject) => {
          const xhr = new XMLHttpRequest()
          xhr.open('PUT', uploadUrl)
          xhr.setRequestHeader('Content-Type', file.type || 'application/octet-stream')
          
          // 设置超时（5分钟）
          xhr.timeout = 5 * 60 * 1000
          
          xhr.upload.onprogress = (event) => {
            if (event.lengthComputable && onProgress) {
              const percent = Math.round((event.loaded / event.total) * 100)
              onProgress(percent, event.loaded, event.total)
            }
          }
          
          xhr.onload = () => {
            if (xhr.status >= 200 && xhr.status < 300) {
              resolve()
            } else {
              reject(new Error(`MinIO 上传失败 (HTTP ${xhr.status})`))
            }
          }
          
          xhr.onerror = () => reject(new Error('MinIO 连接失败，请检查网络'))
          xhr.ontimeout = () => reject(new Error('上传超时，请重试'))
          xhr.send(file)
        })
        
        // Step 3: 确认上传
        const confirmRes = await fetch(`${BASE_URL}/upload/confirm`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
          body: JSON.stringify({
            name: file.name,
            parent_id: targetParentId,
            objectName,
            file_size: file.size,
          }),
        })
        if (!confirmRes.ok) {
          const err = await confirmRes.json().catch(() => ({ detail: '确认上传失败' }))
          throw new Error(err.detail || '确认上传失败')
        }
        return await confirmRes.json()
      }
      
      // 带重试的执行
      let lastError
      for (let attempt = 0; attempt <= retryCount; attempt++) {
        try {
          return await doUpload(attempt)
        } catch (e) {
          lastError = e
          if (attempt < retryCount) {
            const delay = Math.min(1000 * Math.pow(2, attempt), 8000)
            await new Promise(r => setTimeout(r, delay))
          }
        }
      }
      throw lastError
    },

    /**
     * 预签名上传 - 简化版（文件夹已预先创建好，不再处理 relativePath）
     * 三步：1.获取预签名URL → 2.直传MinIO → 3.确认上传创建记录
     */
    presignUploadDirect: async (file, targetFolderId, onProgress, retryCount = 2) => {
      const token = localStorage.getItem('token')
      if (!token) throw new Error('未登录')

      const doUpload = async () => {
        // Step 1: 获取预签名 URL
        const presignRes = await fetch(`${BASE_URL}/upload/presign`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
          body: JSON.stringify({ filename: file.name, parent_id: targetFolderId, file_size: file.size }),
        })
        if (!presignRes.ok) {
          const err = await presignRes.json().catch(() => ({ detail: '获取上传地址失败' }))
          throw new Error(err.detail || '获取上传地址失败')
        }
        const { uploadUrl, objectName } = await presignRes.json()

        // Step 2: 直接上传到 MinIO（带进度）
        await new Promise((resolve, reject) => {
          const xhr = new XMLHttpRequest()
          xhr.open('PUT', uploadUrl)
          xhr.setRequestHeader('Content-Type', file.type || 'application/octet-stream')
          xhr.timeout = 5 * 60 * 1000

          xhr.upload.onprogress = (event) => {
            if (event.lengthComputable && onProgress) {
              const percent = Math.round((event.loaded / event.total) * 100)
              onProgress(percent, event.loaded, event.total)
            }
          }

          xhr.onload = () => {
            if (xhr.status >= 200 && xhr.status < 300) resolve()
            else reject(new Error(`MinIO 上传失败 (HTTP ${xhr.status})`))
          }
          xhr.onerror = () => reject(new Error('MinIO 连接失败，请检查网络'))
          xhr.ontimeout = () => reject(new Error('上传超时，请重试'))
          xhr.send(file)
        })

        // Step 3: 确认上传
        const confirmRes = await fetch(`${BASE_URL}/upload/confirm`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
          body: JSON.stringify({ name: file.name, parent_id: targetFolderId, objectName, file_size: file.size }),
        })
        if (!confirmRes.ok) {
          const err = await confirmRes.json().catch(() => ({ detail: '确认上传失败' }))
          throw new Error(err.detail || '确认上传失败')
        }
        return await confirmRes.json()
      }

      let lastError
      for (let attempt = 0; attempt <= retryCount; attempt++) {
        try {
          return await doUpload()
        } catch (e) {
          lastError = e
          if (attempt < retryCount) {
            await new Promise(r => setTimeout(r, Math.min(1000 * Math.pow(2, attempt), 8000)))
          }
        }
      }
      throw lastError
    },

    /**
     * 代理上传（传统方式，通过后端中转）
     * 保留作为后备方案
     */
    proxyUpload: async (file, parentId, relativePath, onProgress, retryCount = 3) => {
      const token = localStorage.getItem('token')
      const formData = new FormData()
      formData.append('file', file)
      if (parentId) formData.append('parent_id', parentId)
      if (relativePath) formData.append('relative_path', relativePath)
      
      const uploadWithRetry = async (attempt) => {
        return new Promise((resolve, reject) => {
          const xhr = new XMLHttpRequest()
          xhr.open('POST', `${BASE_URL}/upload/proxy`)
          
          if (token) {
            xhr.setRequestHeader('Authorization', `Bearer ${token}`)
          }
          xhr.timeout = 10 * 60 * 1000  // 10分钟超时
          
          xhr.upload.onprogress = (event) => {
            if (event.lengthComputable && onProgress) {
              const percent = Math.round((event.loaded / event.total) * 100)
              onProgress(percent, event.loaded, event.total)
            }
          }
          
          xhr.onload = () => {
            let data
            try {
              data = JSON.parse(xhr.responseText)
            } catch {
              data = { error: xhr.responseText.substring(0, 200) }
            }
            
            if (xhr.status >= 200 && xhr.status < 300) {
              resolve(data)
            } else if (attempt < retryCount) {
              const delay = Math.min(1000 * Math.pow(2, attempt - 1), 8000)
              setTimeout(() => resolve(uploadWithRetry(attempt + 1)), delay)
            } else {
              reject(new Error(data.detail || data.error || '上传失败'))
            }
          }
          
          xhr.onerror = () => {
            if (attempt < retryCount) {
              const delay = Math.min(1000 * Math.pow(2, attempt - 1), 8000)
              setTimeout(() => resolve(uploadWithRetry(attempt + 1)), delay)
            } else {
              reject(new Error('网络错误，上传失败'))
            }
          }
          
          xhr.ontimeout = () => reject(new Error('上传超时，请检查网络后重试'))
          
          xhr.send(formData)
        })
      }
      
      return uploadWithRetry(0)
    },
    
    /**
     * 上传回滚：批量永久删除已创建的文件记录和 MinIO 对象
     */
    undoUpload: async (fileIds) => {
      const token = localStorage.getItem('token')
      const res = await fetch(`${BASE_URL}/upload/undo`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...(token ? { 'Authorization': `Bearer ${token}` } : {}) },
        body: JSON.stringify({ file_ids: fileIds }),
      })
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: '回滚失败' }))
        throw new Error(err.detail || '回滚失败')
      }
      return res.json()
    },
  },
}

export const API_BASE_URL = BASE_URL
