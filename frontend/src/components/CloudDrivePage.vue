<script setup>
import { ref, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { api, API_BASE_URL } from '@/services/api'
import {
  FolderPlus,
  Upload,
  Download,
  Trash2,
  Folder,
  ArrowLeft,
  MoreVertical,
  Pencil,
  HardDrive,
  Check,
  X,
  Eye,
  Search,
  Star,
  RotateCcw,
  Info,
  CheckCheck,
  LogOut,
  Clipboard,
  Copy,
  Scissors,
  Plus,
  Loader,
} from 'lucide-vue-next'

const router = useRouter()

const username = ref(localStorage.getItem('username') || '未登录')

const isMobile = ref(false)
function checkMobile() {
  isMobile.value = window.innerWidth <= 768
}
onMounted(() => {
  checkMobile()
  window.addEventListener('resize', checkMobile)
})
onUnmounted(() => {
  window.removeEventListener('resize', checkMobile)
  document.removeEventListener('click', handleDocumentClick)
})

function goLogout() {
  localStorage.removeItem('token')
  localStorage.removeItem('username')
  router.push('/login')
}

// 状态
const files = ref([])
const totalSize = ref(0)
const currentFolderId = ref(null)
const currentFolderName = ref('')
const breadcrumb = ref([])
const loading = ref(false)
const errorMsg = ref('')

// 新建文件夹
const newFolderName = ref('')
const pendingNewFolder = ref(false)

function startNewFolder() {
  pendingNewFolder.value = true
  newFolderName.value = ''
  nextTick(() => {
    const el = document.querySelector('.drive-new-folder-input')
    if (el) el.focus()
  })
}

// 重命名
const renamingId = ref(null)
const renameName = ref('')
const renameIsFolder = ref(false)
const originalExt = ref('')
const renameOriginalName = ref('') // 原始名称，用于判断是否修改
const renameConflict = ref(false)
let renameInputEl = null
let renameCheckTimer = null

// 新建文件夹冲突检测
const folderConflict = ref(false)
let folderCheckTimer = null

function checkFolderName(name) {
  clearTimeout(folderCheckTimer)
  if (!name.trim() || !pendingNewFolder.value) {
    folderConflict.value = false
    return
  }
  folderCheckTimer = setTimeout(async () => {
    try {
      const result = await api.cloudDrive.checkName(name.trim(), currentFolderId.value ?? 0)
      folderConflict.value = result.conflict
    } catch {
      folderConflict.value = false
    }
  }, 300)
}
watch(newFolderName, (val) => { checkFolderName(val) })

function checkRenameName(name) {
  clearTimeout(renameCheckTimer)
  if (!name.trim() || !renamingId.value) {
    renameConflict.value = false
    return
  }
  renameCheckTimer = setTimeout(async () => {
    try {
      const item = files.value.find(f => f.id === renamingId.value)
      if (!item) return
      const parentId = item.is_folder ? (item.parent_id ?? 0) : (item.folder_id ?? currentFolderId.value ?? 0)
      const result = await api.cloudDrive.checkName(name.trim(), parentId, renamingId.value)
      renameConflict.value = result.conflict
    } catch {
      renameConflict.value = false
    }
  }, 300)
}
watch(renameName, (val) => { checkRenameName(val) })

// 右键菜单
const menuFileId = ref(null)
const menuStyle = ref({})

// 上传状态管理
const uploadFilesInput = ref(null)
const uploadFolderInput = ref(null)
const isUploading = ref(false)
const showUploadMenu = ref(false)
const uploadBtnRef = ref(null)
const uploadMenuStyle = ref({})
const uploadTasks = ref([])           // { id, file, name, relativePath, status, progress, error, fileId }
const showUploadPanel = ref(false)
const uploadCompletedCount = ref(0)
const uploadFailedCount = ref(0)
const uploadTotalCount = ref(0)

let uploadTaskIdCounter = 0
const MAX_CONCURRENT = 8  // 最大并行数

function createUploadTask(file, relativePath) {
  return {
    id: ++uploadTaskIdCounter,
    file,
    name: file.name,
    size: file.size,
    relativePath,
    status: 'pending',    // pending | uploading | success | failed
    progress: 0,
    error: '',
    fileId: null,         // 上传成功后的复合ID
  }
}

function triggerUploadFiles() {
  uploadFilesInput.value?.click()
}

function triggerUploadFolder() {
  uploadFolderInput.value?.click()
}

function toggleUploadMenu() {
  showUploadMenu.value = !showUploadMenu.value
  if (showUploadMenu.value) {
    nextTick(() => {
      const btn = uploadBtnRef.value
      if (btn) {
        const rect = btn.getBoundingClientRect()
        uploadMenuStyle.value = {
          position: 'fixed',
          top: rect.bottom + 4 + 'px',
          right: window.innerWidth - rect.right + 'px',
          zIndex: 10000,
        }
      }
    })
  }
}

// 点击外部关闭上传菜单
function handleClickOutsideUpload(e) {
  if (showUploadMenu.value && !e.target.closest('.drive-upload-dropdown')) {
    showUploadMenu.value = false
  }
}

// 当前标签页
const activeTab = ref('files')

// 搜索
const searchKeyword = ref('')
const isSearching = ref(false)

// 批量选择
const batchMode = ref(false)
const selectedIds = ref(new Set())
const batchDeleting = ref(false)

// 剪切板
const clipboard = ref([])
const clipboardAction = ref(null) // 'copy' | 'cut'

// 文件详情面板
const showDetailPanel = ref(false)
const detailFile = ref(null)
const detailPath = ref([])

// 回收站文件数量
const trashCount = ref(0)
const previewError = ref('')

// 预览面板状态
const previewUrl = ref('')
const previewingFile = ref(null)
const previewLoading = ref(false)
const sidebarCollapsed = ref(false)

// 快捷访问
const quickAccessItems = ref([])

const quickAccessLoading = ref(false)

// 快捷访问文件选择器（模态框）
const showQuickAccessPicker = ref(false)
const pickerFiles = ref([])
const pickerBreadcrumb = ref([])
const pickerCurrentFolderId = ref(null)
const pickerSelectedFile = ref(null)
const pickerLoading = ref(false)

async function loadPickerFiles(folderId) {
  pickerLoading.value = true
  try {
    const params = {}
    if (folderId) params.parent_id = folderId
    const data = await api.cloudDrive.list(params)
    pickerFiles.value = data.files || []
  } catch (e) {
    pickerFiles.value = []
  } finally {
    pickerLoading.value = false
  }
}

async function loadPickerBreadcrumb(folderId) {
  if (!folderId) {
    pickerBreadcrumb.value = []
    return
  }
  try {
    const data = await api.cloudDrive.getBreadcrumb(folderId)
    pickerBreadcrumb.value = data.items || []
  } catch {
    pickerBreadcrumb.value = []
  }
}

function openQuickAccessPicker() {
  showQuickAccessPicker.value = true
  pickerCurrentFolderId.value = null
  pickerSelectedFile.value = null
  loadPickerFiles(null)
  loadPickerBreadcrumb(null)
}

function pickerEnterFolder(file) {
  if (!file.is_folder) return
  pickerCurrentFolderId.value = file.id
  pickerSelectedFile.value = null
  loadPickerFiles(file.id)
  loadPickerBreadcrumb(file.id)
}

function pickerGoBack() {
  if (pickerBreadcrumb.value.length > 1) {
    const parent = pickerBreadcrumb.value[pickerBreadcrumb.value.length - 2]
    pickerCurrentFolderId.value = parent.id
    pickerSelectedFile.value = null
    loadPickerFiles(parent.id)
    loadPickerBreadcrumb(parent.id)
  } else {
    pickerCurrentFolderId.value = null
    pickerSelectedFile.value = null
    loadPickerFiles(null)
    pickerBreadcrumb.value = []
  }
}

function pickerGoToRoot() {
  pickerCurrentFolderId.value = null
  pickerSelectedFile.value = null
  loadPickerFiles(null)
  pickerBreadcrumb.value = []
}

function pickerGoToBreadcrumb(index) {
  const item = pickerBreadcrumb.value[index]
  if (!item) return
  pickerCurrentFolderId.value = item.id
  pickerSelectedFile.value = null
  loadPickerFiles(item.id)
  pickerBreadcrumb.value = pickerBreadcrumb.value.slice(0, index + 1)
}

function pickerSelectFile(file) {
  pickerSelectedFile.value = file
}

async function pickerConfirm() {
  const file = pickerSelectedFile.value
  if (!file) return
  try {
    await api.cloudDrive.addQuickAccess(file.name, file.id)
    showQuickAccessPicker.value = false
    pickerSelectedFile.value = null
    await loadQuickAccess()
  } catch (e) {
    errorMsg.value = e.message || '添加快捷访问失败'
  }
}

async function loadQuickAccess() {
  quickAccessLoading.value = true
  try {
    const data = await api.cloudDrive.listQuickAccess()
    quickAccessItems.value = data.items || []
  } catch (e) {
    quickAccessItems.value = []
  } finally {
    quickAccessLoading.value = false
  }
}

async function removeQuickAccess(id) {
  try {
    await api.cloudDrive.deleteQuickAccess(id)
    await loadQuickAccess()
  } catch (e) {
    errorMsg.value = e.message || '删除快捷访问失败'
  }
}

async function goToQuickAccess(item) {
  if (!item.file_id) return
  if (item.is_folder) {
    // 文件夹：导航进入
    currentFolderId.value = item.file_id
    currentFolderName.value = item.name
    activeTab.value = 'files'
    await loadFiles()
    await loadBreadcrumb()
  } else {
    // 文件：预览
    const file = {
      id: item.file_id,
      name: item.file_name || item.name,
      is_folder: false,
      extension: item.file_name ? '.' + (item.file_name.split('.').pop() || '') : '',
    }
    if (isPreviewable(file)) {
      previewFile(file)
    } else {
      downloadFile(file)
    }
  }
}

// 拖拽移动
const dragOverFolderId = ref(null)
const draggingFileIds = ref(new Set())

function onDragStart(e, file) {
  if (file.is_folder && activeTab.value !== 'files') return
  draggingFileIds.value = new Set([file.id])
  e.dataTransfer.effectAllowed = 'move'
  const img = new Image()
  img.src = 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7'
  e.dataTransfer.setDragImage(img, 0, 0)
  if (renamingId.value === file.id) {
    cancelRename()
  }
}

function onDragEnd() {
  draggingFileIds.value = new Set()
}

function onDragStartBatch(e) {
  if (selectedIds.value.size === 0) return
  draggingFileIds.value = new Set(selectedIds.value)
  e.dataTransfer.effectAllowed = 'move'
}

function onDragOver(e, folder) {
  if (!folder.is_folder) return
  e.preventDefault()
  e.dataTransfer.dropEffect = 'move'
}

function onDragLeave(e, folder) {
  if (!folder.is_folder) return
  if (!e.currentTarget.contains(e.relatedTarget)) {
    dragOverFolderId.value = null
  }
}

function onFileDragOver(e) {
  e.preventDefault()
  e.dataTransfer.dropEffect = 'move'
  dragOverFolderId.value = '__root__'
}

function onFileDragLeave(e) {
  if (!e.currentTarget.contains(e.relatedTarget)) {
    dragOverFolderId.value = null
  }
}

async function onDropHandler(targetId) {
  dragOverFolderId.value = null
  const fileIds = Array.from(draggingFileIds.value)
  if (fileIds.length === 0) return

  // 不要移动到自身或自己的子文件夹
  if (targetId && fileIds.includes(targetId)) return

  try {
    await api.cloudDrive.batchMove(fileIds, targetId)
    draggingFileIds.value = new Set()
    selectedIds.value = new Set()
    batchMode.value = false
    await loadFiles()
  } catch (e) {
    errorMsg.value = e.message || '移动失败'
  }
}

// 拖拽到快捷访问区域：将文件夹添加为快捷访问（不是移动文件）
async function onDropToQuickAccess() {
  dragOverFolderId.value = null
  const fileIds = Array.from(draggingFileIds.value)
  if (fileIds.length !== 1) {
    errorMsg.value = '请拖拽单个文件或文件夹添加到快捷访问'
    return
  }
  const fileId = fileIds[0]
  const file = files.value.find(f => f.id === fileId)
  if (!file) {
    errorMsg.value = '文件不存在'
    return
  }
  try {
    await api.cloudDrive.addQuickAccess(file.name, fileId)
    draggingFileIds.value = new Set()
    await loadQuickAccess()
    errorMsg.value = ''
  } catch (e) {
    errorMsg.value = e.message || '添加快捷访问失败'
  }
}

// 转换功能
const activeConvertPanel = ref(null)

function toggleConvertPanel(panel) {
  activeConvertPanel.value = activeConvertPanel.value === panel ? null : panel
}

// 判断文件是否支持内联预览（图片 + PDF）
function isPreviewable(file) {
  if (!file || file.is_folder) return false
  return isPreviewImage(file) || isPreviewPdf(file)
}

// 判断是否为图片（包含动图）
function isPreviewImage(file) {
  const name = (file.name || '').toLowerCase()
  return ['.jpg', '.jpeg', '.png', '.bmp', '.webp', '.svg', '.ico', '.gif'].some(ext => name.endsWith(ext))
}

// 判断是否为 PDF
function isPreviewPdf(file) {
  const name = (file.name || '').toLowerCase()
  if (name.endsWith('.pdf')) return true
  return (file.mime_type || '').toLowerCase() === 'application/pdf'
}

// 格式化文件大小
function formatSize(bytes) {
  if (bytes === 0 || bytes === null || bytes === undefined) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  if (i < 0 || i >= sizes.length) return '0 B'
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i]
}

// 格式化日期
function formatDate(dateStr) {
  if (!dateStr) return ''
  const d = new Date(dateStr)
  return d.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

function formatFullDate(dateStr) {
  if (!dateStr) return ''
  const d = new Date(dateStr)
  return d.toLocaleDateString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

// 根据文件扩展名返回 SVG 图标（与 ChatInput 保持一致）
function getFileIcon(file) {
  const name = (file.name || '').toLowerCase()
  const ext = name.includes('.') ? '.' + name.split('.').pop() : ''

  // 文件夹
  if (file.is_folder) {
    return '<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.93a2 2 0 0 1-1.66-.9l-.82-1.2A2 2 0 0 0 7.59 3H4a2 2 0 0 0-2 2v13c0 1.1.9 2 2 2Z" fill="#fef9c3" stroke="#eab308" stroke-width="1.2"/></svg>'
  }

  // Excel / CSV
  if (['.xlsx', '.xls', '.csv'].includes(ext)) {
    return '<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z" fill="#e8f5e9" stroke="#22c55e" stroke-width="1.2"/><polyline points="14 2 14 8 20 8" stroke="#22c55e" stroke-width="1.2"/><line x1="8" y1="13" x2="16" y2="13" stroke="#22c55e" stroke-width="1.2"/><line x1="8" y1="16" x2="16" y2="16" stroke="#22c55e" stroke-width="1.2"/><line x1="12" y1="13" x2="12" y2="16" stroke="#22c55e" stroke-width="1.2"/></svg>'
  }

  // PPT
  if (['.pptx', '.ppt'].includes(ext)) {
    return '<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z" fill="#fff3e0" stroke="#f97316" stroke-width="1.2"/><polyline points="14 2 14 8 20 8" stroke="#f97316" stroke-width="1.2"/><rect x="7" y="11" width="10" height="6" rx="1" stroke="#f97316" stroke-width="1.2" fill="#ffedd5"/><line x1="12" y1="11" x2="12" y2="17" stroke="#f97316" stroke-width="1.2"/><line x1="7" y1="14" x2="17" y2="14" stroke="#f97316" stroke-width="1.2"/></svg>'
  }

  // Word
  if (['.docx', '.doc'].includes(ext)) {
    return '<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z" fill="#e0f2fe" stroke="#3b82f6" stroke-width="1.2"/><polyline points="14 2 14 8 20 8" stroke="#3b82f6" stroke-width="1.2"/><text x="12" y="18" text-anchor="middle" font-size="10" font-weight="700" fill="#3b82f6" font-family="Arial">W</text></svg>'
  }

  // PDF
  if (['.pdf'].includes(ext)) {
    return '<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z" fill="#fce4ec" stroke="#ef4444" stroke-width="1.2"/><polyline points="14 2 14 8 20 8" stroke="#ef4444" stroke-width="1.2"/><text x="12" y="17" text-anchor="middle" font-size="7" font-weight="700" fill="#ef4444" font-family="Arial">PDF</text></svg>'
  }

  // 图片
  if (['.jpg', '.jpeg', '.png', '.webp', '.svg', '.bmp', '.ico'].includes(ext)) {
    return '<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z" fill="#f0fdf4" stroke="#22c55e" stroke-width="1.2"/><polyline points="14 2 14 8 20 8" stroke="#22c55e" stroke-width="1.2"/><circle cx="9.5" cy="11.5" r="1.5" fill="#22c55e"/><path d="M7 18l3-4 2 2 2-3 3 5" stroke="#22c55e" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"/></svg>'
  }

  // 压缩包
  if (['.zip', '.rar', '.7z', '.tar', '.gz', '.bz2', '.xz', '.iso'].includes(ext)) {
    return '<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z" fill="#efebe9" stroke="#795548" stroke-width="1.2"/><polyline points="14 2 14 8 20 8" stroke="#795548" stroke-width="1.2"/><rect x="8" y="11" width="8" height="6" rx="1" stroke="#795548" stroke-width="1.2"/><line x1="10" y1="11" x2="10" y2="17" stroke="#795548" stroke-width="1.2"/><line x1="14" y1="11" x2="14" y2="17" stroke="#795548" stroke-width="1.2"/></svg>'
  }

  // 默认文件图标
  return '<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z" fill="#f9fafb" stroke="#9ca3af" stroke-width="1.2"/><polyline points="14 2 14 8 20 8" stroke="#9ca3af" stroke-width="1.2"/></svg>'
}

// 导航版本计数器，用于丢弃过期响应
let navVersion = 0

// 加载文件列表
async function loadFiles() {
  const version = ++navVersion
  loading.value = true
  errorMsg.value = ''
  try {
    const params = {}
    if (currentFolderId.value) params.parent_id = currentFolderId.value
    const data = await api.cloudDrive.list(params)
    if (version !== navVersion) return
    files.value = data.files || []
    totalSize.value = data.totalSize || 0
  } catch (e) {
    if (version !== navVersion) return
    errorMsg.value = e.message || '加载失败'
  } finally {
    loading.value = false
  }
}

// 加载面包屑
async function loadBreadcrumb() {
  if (!currentFolderId.value) {
    breadcrumb.value = []
    return
  }
  try {
    const data = await api.cloudDrive.getBreadcrumb(currentFolderId.value)
    breadcrumb.value = data.path || []
  } catch (e) {
    // ignore
  }
}

async function loadTrash() {
  loading.value = true
  errorMsg.value = ''
  try {
    const data = await api.cloudDrive.listTrash()
    files.value = data.files || []
  } catch (e) {
    errorMsg.value = e.message || '加载失败'
  } finally {
    loading.value = false
  }
}

async function loadFavorites() {
  loading.value = true
  errorMsg.value = ''
  try {
    const data = await api.cloudDrive.listFavorites()
    files.value = data.files || []
  } catch (e) {
    errorMsg.value = e.message || '加载失败'
  } finally {
    loading.value = false
  }
}

async function switchTab(tab) {
  activeTab.value = tab
  searchKeyword.value = ''
  isSearching.value = false
  batchMode.value = false
  selectedIds.value = new Set()
  closePreview()
  closeDetail()

  if (tab === 'files') {
    if (breadcrumb.value.length === 0 && currentFolderId.value) {
      await loadBreadcrumb()
    } else if (breadcrumb.value.length > 0 && !currentFolderId.value) {
      breadcrumb.value = []
    }
    await loadFiles()
  } else if (tab === 'favorites') {
    await loadFavorites()
  } else if (tab === 'trash') {
    await loadTrash()
  }
}

async function doSearch() {
  if (!searchKeyword.value.trim()) {
    isSearching.value = false
    switchTab(activeTab.value)
    return
  }
  isSearching.value = true
  loading.value = true
  errorMsg.value = ''
  try {
    const data = await api.cloudDrive.search(searchKeyword.value.trim(), currentFolderId.value ?? 0)
    files.value = data.files || []
  } catch (e) {
    errorMsg.value = e.message || '搜索失败'
  } finally {
    loading.value = false
  }
}

async function clearSearch() {
  searchKeyword.value = ''
  isSearching.value = false
  await loadFiles()
}

// 进入文件夹（单击触发）
async function enterFolder(file) {
  if (!file.is_folder) return
  currentFolderId.value = file.id
  currentFolderName.value = file.name
  activeTab.value = 'files'
  await loadFiles()
  await loadBreadcrumb()
}

// 返回上级文件夹
async function goBack() {
  if (breadcrumb.value.length > 1) {
    // 倒数第二级是上级文件夹（最后一级是当前文件夹）
    const parent = breadcrumb.value[breadcrumb.value.length - 2]
    currentFolderId.value = parent.id
    currentFolderName.value = parent.name
  } else {
    // 只有一级或没有，回到根目录
    currentFolderId.value = null
    currentFolderName.value = ''
  }
  await loadFiles()
  await loadBreadcrumb()
}

// 回到根目录
async function goToRoot() {
  currentFolderId.value = null
  currentFolderName.value = ''
  await loadFiles()
  breadcrumb.value = []
}

// 回到面包屑某级
async function goToBreadcrumb(index) {
  if (index < 0) {
    await goToRoot()
    return
  }
  const item = breadcrumb.value[index]
  currentFolderId.value = item.id
  currentFolderName.value = item.name
  await loadFiles()
  // 保留从根到当前 click 的完整路径
  breadcrumb.value = breadcrumb.value.slice(0, index + 1)
  await loadBreadcrumb()
}

// 点击文件项（文件夹进入，可预览文件预览，其他文件下载）
function handleFileClick(file) {
  if (batchMode.value) {
    toggleSelect(file.id)
    return
  }
  if (file.is_folder && activeTab.value !== 'trash') {
    enterFolder(file)
  } else if (isPreviewable(file)) {
    previewFile(file)
  } else {
    downloadFile(file)
  }
}

// 创建文件夹
async function createFolder() {
  const name = newFolderName.value.trim()
  if (!name) return
  try {
    await api.cloudDrive.createFolder(name, currentFolderId.value)
    newFolderName.value = ''
    pendingNewFolder.value = false
    await loadFiles()
  } catch (e) {
    errorMsg.value = e.message || '创建失败'
  }
}

// ── 上传（预签名 URL 方式，文件直达 MinIO）──

// 上传前冲突检测：检查当前目录下是否有同名文件/文件夹
async function checkUploadConflicts(validFiles, folderId) {
  if (validFiles.length === 0) return null
  try {
    const data = await api.cloudDrive.list(folderId ? { parent_id: folderId } : {})
    const existingFiles = data.files || []

    // 收集所有顶层文件/文件夹名
    const topLevelNames = new Set()
    // 文件夹上传时，顶层目录名
    for (const file of validFiles) {
      if (file.webkitRelativePath) {
        const topDir = file.webkitRelativePath.split('/')[0]
        if (topDir) topLevelNames.add(topDir)
      } else {
        topLevelNames.add(file.name)
      }
    }

    const conflicts = []
    for (const name of topLevelNames) {
      const found = existingFiles.some(f => f.name === name)
      if (found) conflicts.push(name)
    }
    return conflicts.length > 0 ? conflicts : null
  } catch {
    return null // 检测失败不阻塞上传
  }
}

// 开始上传
async function handleUpload(e) {
  const fileList = e.target.files
  if (!fileList || fileList.length === 0) return

  const validFiles = Array.from(fileList).filter(f => !f.name.startsWith('.') && f.name !== '')
  if (validFiles.length === 0) return

  const token = localStorage.getItem('token')
  if (!token) { errorMsg.value = '未登录'; return }

  // 上传前冲突检测
  const conflicts = await checkUploadConflicts(validFiles, currentFolderId.value)
  if (conflicts) {
    const conflictMsg = conflicts.length > 5
      ? `${conflicts.slice(0, 5).join('、')} 等 ${conflicts.length} 个条目已存在`
      : conflicts.join('、') + ' 已存在'
    if (!confirm(`检测到命名冲突：${conflictMsg}。继续上传将覆盖同名文件夹或文件，是否继续？`)) return
  }

  // 第一步：收集所有唯一相对路径，预先一次性创建文件夹结构
  const uniquePaths = new Set()
  const tasks = validFiles.map(file => {
    let relativePath = ''
    if (file.webkitRelativePath) {
      const parts = file.webkitRelativePath.split('/')
      parts.pop()
      relativePath = parts.join('/')
    }
    if (relativePath) uniquePaths.add(relativePath)
    return createUploadTask(file, relativePath)
  })

  // 预先创建所有文件夹，使用缓存避免重复创建同名文件夹
  // folderCache: "parentId:folderName" → folderId
  const folderCache = new Map()
  const pathMap = new Map() // relativePath → 最终 folderId
  try {
    // 按路径深度排序，确保父文件夹先于子文件夹创建
    const sortedPaths = Array.from(uniquePaths).sort((a, b) => {
      const depthA = a.replace(/\\/g, '/').split('/').filter(Boolean).length
      const depthB = b.replace(/\\/g, '/').split('/').filter(Boolean).length
      return depthA - depthB
    })

    for (const relPath of sortedPaths) {
      const parts = relPath.replace(/\\/g, '/').split('/').filter(Boolean)
      let parentId = currentFolderId.value
      for (const folderName of parts) {
        const cacheKey = `${parentId}:${folderName}`
        if (folderCache.has(cacheKey)) {
          // 复用已创建的文件夹
          parentId = folderCache.get(cacheKey)
        } else {
          const folderRes = await fetch(`${API_BASE_URL}/folder`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
            body: JSON.stringify({ name: folderName, parent_id: parentId }),
          })
          if (!folderRes.ok) {
            const errData = await folderRes.json().catch(() => ({ detail: '创建文件夹失败' }))
            throw new Error(errData.detail || '创建文件夹失败')
          }
          const folderData = await folderRes.json()
          parentId = folderData.id
          folderCache.set(cacheKey, parentId)
        }
      }
      pathMap.set(relPath, parentId)
    }
  } catch (e) {
    errorMsg.value = `文件夹创建失败：${e.message}`
    return
  }

  // 第二步：批量上传文件
  uploadTasks.value = tasks
  uploadTotalCount.value = tasks.length
  uploadCompletedCount.value = 0
  uploadFailedCount.value = 0
  showUploadPanel.value = true
  isUploading.value = true
  errorMsg.value = ''

  for (let i = 0; i < tasks.length; i += MAX_CONCURRENT) {
    const batch = tasks.slice(i, i + MAX_CONCURRENT)

    await Promise.allSettled(
      batch.map(task => {
        // 使用预创建的文件夹ID，避免每个上传再各自创建文件夹
        const folderId = task.relativePath ? pathMap.get(task.relativePath) : currentFolderId.value
        return uploadSingleFile(task, folderId)
      })
    )
  }

  // 上传完成
  isUploading.value = false
  await loadFiles()

  if (uploadFilesInput.value) uploadFilesInput.value.value = ''
  if (uploadFolderInput.value) uploadFolderInput.value.value = ''

  const successCount = uploadCompletedCount.value
  const failCount = uploadFailedCount.value
  if (failCount === 0) {
    errorMsg.value = `✅ 上传完成：${successCount} 个文件全部上传成功`
  } else {
    errorMsg.value = `⚠️ 上传完成：${successCount} 个成功，${failCount} 个失败`
  }
}

// 上传单个文件（预签名方式）
async function uploadSingleFile(task, targetFolderId) {
  task.status = 'uploading'
  task.progress = 0
  task.error = ''

  try {
    const result = await api.oss.presignUploadDirect(
      task.file,
      targetFolderId,
      (percent) => {
        task.progress = percent
      },
      2  // 重试次数
    )
    // 上传成功
    task.status = 'success'
    task.progress = 100
    task.fileId = result.id
    uploadCompletedCount.value++
  } catch (e) {
    task.status = 'failed'
    task.error = e.message || '上传失败'
    uploadFailedCount.value++
  }
}

// 重试单个失败文件
async function retryUploadTask(task) {
  if (task.status !== 'failed') return
  uploadFailedCount.value--
  isUploading.value = true
  const folderId = task.relativePath
    ? null  // 需要重新获取文件夹ID
    : currentFolderId.value
  if (!folderId && task.relativePath) {
    task.error = '需重新创建文件夹结构，请使用"重试全部"'
    task.status = 'failed'
    uploadFailedCount.value++
    isUploading.value = false
    return
  }
  await uploadSingleFile(task, folderId)
  if (uploadTasks.value.every(t => t.status !== 'pending' && t.status !== 'uploading')) {
    isUploading.value = false
    await loadFiles()
  }
}

// 重试所有失败文件
async function retryAllFailed() {
  const failedTasks = uploadTasks.value.filter(t => t.status === 'failed')
  if (failedTasks.length === 0) return

  // 重新创建所有文件夹结构（使用缓存避免重复创建）
  const uniquePaths = new Set()
  for (const task of failedTasks) {
    if (task.relativePath) uniquePaths.add(task.relativePath)
  }
  const folderCache = new Map()
  const pathMap = new Map()
  const token = localStorage.getItem('token')
  if (token) {
    // 按路径深度排序
    const sortedPaths = Array.from(uniquePaths).sort((a, b) => {
      const depthA = a.replace(/\\/g, '/').split('/').filter(Boolean).length
      const depthB = b.replace(/\\/g, '/').split('/').filter(Boolean).length
      return depthA - depthB
    })
    for (const relPath of sortedPaths) {
      const parts = relPath.replace(/\\/g, '/').split('/').filter(Boolean)
      let parentId = currentFolderId.value
      for (const folderName of parts) {
        const cacheKey = `${parentId}:${folderName}`
        if (folderCache.has(cacheKey)) {
          parentId = folderCache.get(cacheKey)
        } else {
          const folderRes = await fetch(`${API_BASE_URL}/folder`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
            body: JSON.stringify({ name: folderName, parent_id: parentId }),
          }).catch(() => null)
          if (!folderRes) break
          const folderData = await folderRes.json()
          parentId = folderData.id
          folderCache.set(cacheKey, parentId)
        }
      }
      pathMap.set(relPath, parentId)
    }
  }

  uploadFailedCount.value = 0
  isUploading.value = true

  for (let i = 0; i < failedTasks.length; i += MAX_CONCURRENT) {
    const batch = failedTasks.slice(i, i + MAX_CONCURRENT)
    await Promise.allSettled(
      batch.map(task => {
        const folderId = task.relativePath ? pathMap.get(task.relativePath) : currentFolderId.value
        return folderId ? uploadSingleFile(task, folderId) : Promise.resolve()
      })
    )
  }

  isUploading.value = false
  await loadFiles()
}

// 回滚所有成功上传的文件（用于全部/大批量失败时）
async function rollbackUpload() {
  const successIds = uploadTasks.value
    .filter(t => t.status === 'success' && t.fileId)
    .map(t => t.fileId)

  if (successIds.length === 0) return

  if (!confirm(`确定要回滚 ${successIds.length} 个已上传的文件吗？此操作不可撤销。`)) return

  try {
    await api.oss.undoUpload(successIds)
    uploadTasks.value.forEach(t => {
      if (t.status === 'success') {
        t.status = 'failed'
        t.error = '已回滚'
        t.fileId = null
      }
    })
    uploadCompletedCount.value = 0
    uploadFailedCount.value = uploadTasks.value.filter(t => t.status === 'failed').length
    errorMsg.value = `已回滚 ${successIds.length} 个文件`
    await loadFiles()
  } catch (e) {
    errorMsg.value = `回滚失败：${e.message}`
  }
}

// 关闭上传面板
function closeUploadPanel() {
  // 如果有上传中的任务，不允许关闭
  if (uploadTasks.value.some(t => t.status === 'uploading')) return
  showUploadPanel.value = false
}

// 预览文件（页面内右侧面板预览）
// - 图片：直接使用后端代理 URL 内联展示
// - PDF / 有 PDF 版本：使用后端代理 URL 内联展示
async function previewFile(file) {
  previewError.value = ''
  previewUrl.value = ''
  previewLoading.value = true
  previewUrl.value = api.cloudDrive.getPreviewUrl(file.id)
  previewingFile.value = file
}

// 关闭预览面板
function closePreview() {
  previewingFile.value = null
  previewUrl.value = ''
  previewError.value = ''
  previewLoading.value = false
}

// 下载文件（通过后端代理下载，确保线上MinIO内网也能访问）
async function downloadFile(file) {
  try {
    const proxyUrl = api.cloudDrive.getDownloadProxyUrl(file.id, false)
    window.open(proxyUrl, '_blank')
  } catch (e) {
    errorMsg.value = e.message || '下载失败'
  }
}

// 删除文件
async function deleteFile(file) {
  if (!confirm(`确定要删除「${file.name}」吗？${file.is_folder ? '文件夹内所有内容都会被删除。' : ''}`)) return
  try {
    await api.cloudDrive.deleteFile(file.id)
    closeMenu()
    await loadFiles()
    await refreshTrashCount()
  } catch (e) {
    errorMsg.value = e.message || '删除失败'
  }
}

// 回收站恢复
async function restoreFromTrash(file) {
  try {
    await api.cloudDrive.restoreFile(file.id)
    await loadTrash()
    await refreshTrashCount()
  } catch (e) {
    errorMsg.value = e.message || '恢复失败'
  }
}

// 彻底删除
async function permanentDeleteFile(file) {
  if (!confirm(`确定要彻底删除「${file.name}」吗？此操作不可撤销！`)) return
  try {
    await api.cloudDrive.permanentDelete(file.id)
    await loadTrash()
    await refreshTrashCount()
  } catch (e) {
    errorMsg.value = e.message || '删除失败'
  }
}

// 清空回收站
async function emptyTrash() {
  if (!confirm('确定要清空回收站吗？所有文件将被彻底删除，此操作不可撤销！')) return
  try {
    await api.cloudDrive.emptyTrash()
    await loadTrash()
    await refreshTrashCount()
  } catch (e) {
    errorMsg.value = e.message || '清空失败'
  }
}

// 切换收藏
async function toggleFavorite(file) {
  try {
    const newFav = !file.is_favorite
    await api.cloudDrive.toggleFavorite(file.id, newFav)
    file.is_favorite = newFav
    if (activeTab.value === 'favorites' && !newFav) {
      await loadFavorites()
    }
  } catch (e) {
    errorMsg.value = e.message || '操作失败'
  }
}

// 打开文件详情
function openDetail(file) {
  detailFile.value = file
  showDetailPanel.value = true
  // 异步加载路径
  loadDetailPath(file)
}

// 加载文件/文件夹的父目录路径
async function loadDetailPath(file) {
  if (!file) {
    detailPath.value = []
    return
  }
  try {
    if (file.is_folder) {
      // 文件夹：用文件夹的 ID 获取面包屑
      const data = await api.cloudDrive.getBreadcrumb(file.id)
      detailPath.value = data.path || []
    } else {
      // 文件：用父文件夹 ID 获取面包屑
      if (!file.parent_id) {
        detailPath.value = []
        return
      }
      const data = await api.cloudDrive.getBreadcrumb(file.parent_id)
      detailPath.value = data.path || []
    }
  } catch {
    detailPath.value = []
  }
}

// 关闭文件详情
function closeDetail() {
  showDetailPanel.value = false
  detailFile.value = null
  detailPath.value = []
}

// 批量选择切换
function toggleSelect(fileId) {
  const newSet = new Set(selectedIds.value)
  if (newSet.has(fileId)) {
    newSet.delete(fileId)
  } else {
    newSet.add(fileId)
    batchMode.value = true
  }
  selectedIds.value = newSet
  if (selectedIds.value.size === 0) {
    batchMode.value = false
  }
}

// 全选当前列表中的文件
function selectAllFiles() {
  const ids = files.value.map(f => f.id)
  selectedIds.value = new Set(ids)
  batchMode.value = ids.length > 0
}

// 取消全选
function deselectAllFiles() {
  selectedIds.value = new Set()
  batchMode.value = false
}

// 全选/取消全选切换
function toggleSelectAll() {
  if (selectedIds.value.size === files.value.length && files.value.length > 0) {
    deselectAllFiles()
  } else {
    selectAllFiles()
  }
}

// 批量删除
async function batchDeleteFiles() {
  if (selectedIds.value.size === 0 || batchDeleting.value) return
  const count = selectedIds.value.size
  if (!confirm(`确定要删除选中的 ${count} 个文件吗？${count > 1 ? '文件夹内所有内容都会被删除。' : ''}`)) return
  batchDeleting.value = true
  try {
    await api.cloudDrive.batchDelete(Array.from(selectedIds.value))
    selectedIds.value = new Set()
    batchMode.value = false
    await loadFiles()
    await refreshTrashCount()
    errorMsg.value = `✅ 已成功删除 ${count} 个文件`
  } catch (e) {
    errorMsg.value = `批量删除失败：${e.message || '请重试'}`
    // 保留选中状态，让用户可以重试
  } finally {
    batchDeleting.value = false
  }
}

// 批量下载
async function batchDownloadFiles() {
  if (selectedIds.value.size === 0) return
  for (const id of selectedIds.value) {
    const file = files.value.find(f => f.id === id)
    if (file && !file.is_folder) {
      downloadFile(file)
    }
  }
}

// 剪切板操作
function copyToClipboard(file) {
  clipboard.value = [file]
  clipboardAction.value = 'copy'
  closeMenu()
}

function cutToClipboard(file) {
  clipboard.value = [file]
  clipboardAction.value = 'cut'
  closeMenu()
}

function batchCopyToClipboard() {
  if (selectedIds.value.size === 0) return
  clipboard.value = files.value.filter(f => selectedIds.value.has(f.id))
  clipboardAction.value = 'copy'
}

function batchCutToClipboard() {
  if (selectedIds.value.size === 0) return
  clipboard.value = files.value.filter(f => selectedIds.value.has(f.id))
  clipboardAction.value = 'cut'
}

// 粘贴
async function pasteFromClipboard() {
  if (clipboard.value.length === 0) return
  const targetParentId = currentFolderId.value
  try {
    if (clipboardAction.value === 'cut') {
      await api.cloudDrive.batchMove(clipboard.value.map(f => f.id), targetParentId)
    } else {
      await api.cloudDrive.batchCopy(clipboard.value.map(f => f.id), targetParentId)
    }
    clipboard.value = []
    clipboardAction.value = null
    await loadFiles()
  } catch (e) {
    errorMsg.value = e.message || '粘贴失败'
  }
}

function clearClipboard() {
  clipboard.value = []
  clipboardAction.value = null
}

// 显示移动对话框
const showMoveDialog = ref(false)
const moveTargetParentId = ref(null)

function openMoveDialog(file) {
  moveTargetParentId.value = null
  showMoveDialog.value = true
  closeMenu()
}

function openBatchMoveDialog() {
  if (selectedIds.value.size === 0) return
  moveTargetParentId.value = null
  showMoveDialog.value = true
}

async function confirmMove() {
  try {
    const targetId = moveTargetParentId.value
    if (selectedIds.value.size > 0) {
      await api.cloudDrive.batchMove(Array.from(selectedIds.value), targetId)
      selectedIds.value = new Set()
      batchMode.value = false
    }
    await loadFiles()
    showMoveDialog.value = false
  } catch (e) {
    errorMsg.value = e.message || '移动失败'
  }
}

async function confirmSingleMove(fileId) {
  try {
    const targetId = moveTargetParentId.value
    await api.cloudDrive.move(fileId, targetId)
    await loadFiles()
    showMoveDialog.value = false
  } catch (e) {
    errorMsg.value = e.message || '移动失败'
  }
}

// 刷新回收站数量
async function refreshTrashCount() {
  try {
    const data = await api.cloudDrive.listTrash()
    trashCount.value = (data.files || []).length
  } catch (e) {
    // ignore
  }
}

// 开始重命名
function startRename(file) {
  renamingId.value = file.id
  renameIsFolder.value = !!file.is_folder
  renameOriginalName.value = file.name
  if (file.is_folder) {
    renameName.value = file.name
    originalExt.value = ''
  } else {
    const dotIdx = file.name.lastIndexOf('.')
    if (dotIdx > 0) {
      renameName.value = file.name.substring(0, dotIdx)
      originalExt.value = file.name.substring(dotIdx)
    } else {
      renameName.value = file.name
      originalExt.value = ''
    }
  }
  closeMenu()
  nextTick(() => {
    if (renameInputEl) {
      renameInputEl.select()
    }
  })
}

// 确认重命名
async function confirmRename() {
  let name = renameName.value.trim()
  if (!name || !renamingId.value) {
    cancelRename()
    return
  }
  if (!hasRenameChanged(name)) {
    cancelRename()
    return
  }
  if (renameConflict.value) {
    errorMsg.value = '名称已存在'
    cancelRename()
    return
  }
  const hasExt = name.lastIndexOf('.') > 0
  if (!hasExt && originalExt.value) {
    name += originalExt.value
  }
  try {
    const itemType = renameIsFolder.value ? 'folder' : 'file'
    await api.cloudDrive.rename(renamingId.value, name, itemType)
    renamingId.value = null
    originalExt.value = ''
    renameName.value = ''
    renameIsFolder.value = false
    renameOriginalName.value = ''
    errorMsg.value = ''
    await loadFiles()
  } catch (e) {
    errorMsg.value = e.message || '重命名失败'
    cancelRename()
    console.error('重命名失败:', e)
  }
}

// 判断重命名是否有修改
function hasRenameChanged(newName) {
  if (renameIsFolder.value) {
    return newName !== renameOriginalName.value
  }
  const fullNewName = newName.lastIndexOf('.') > 0 ? newName : newName + originalExt.value
  return fullNewName !== renameOriginalName.value
}

// 取消重命名（重置所有编辑状态）
function cancelRename() {
  renamingId.value = null
  renameName.value = ''
  renameIsFolder.value = false
  originalExt.value = ''
  renameOriginalName.value = ''
}

// 点击外部区域退出重命名
function handleDocumentClick(e) {
  if (!renamingId.value) return
  if (renameInputEl && renameInputEl.contains(e.target)) return
  confirmRename()
}

// 右键菜单 / 移动端点击弹出
function openMenu(e, file) {
  menuFileId.value = file.id
  const rect = e.currentTarget.getBoundingClientRect()
  if (isMobile.value) {
    // 移动端：菜单居中显示
    menuStyle.value = {
      position: 'fixed',
      left: '50%',
      top: '50%',
      transform: 'translate(-50%, -50%)',
      zIndex: 9999,
    }
  } else {
    menuStyle.value = {
      position: 'fixed',
      left: rect.right + 4 + 'px',
      top: rect.top + 'px',
      zIndex: 9999,
    }
  }
}

function closeMenu() {
  menuFileId.value = null
}

// 点击外部关闭菜单
function handleClickOutside(e) {
  if (!e.target.closest('.drive-menu-wrapper')) {
    closeMenu()
  }
}

onMounted(() => {
  document.addEventListener('click', handleClickOutside)
  document.addEventListener('click', handleClickOutsideUpload)
  document.addEventListener('click', handleDocumentClick)
  loadFiles()
  refreshTrashCount()
  loadQuickAccess()
})
</script>

<template>
  <div class="cloud-drive-page" :class="{ 'has-preview': previewingFile, 'sidebar-collapsed': sidebarCollapsed }">
    <!-- 左侧边栏 -->
    <aside class="drive-sidebar">
      <!-- 侧边栏头部 -->
      <div class="drive-sidebar-header">
        <div class="drive-sidebar-brand">
          <div>
            <img src="/favicon.ico" class="drive-favicon" alt="LiteDoc" />
          </div>
          <span class="drive-sidebar-title">LiteDoc</span>
        </div>
      </div>

      <!-- 导航 -->
      <nav class="drive-sidebar-nav">
        <button @click="switchTab('files')" class="drive-sidebar-nav-item" :class="{ active: activeTab === 'files' }">
          <div class="drive-sidebar-nav-icon"><HardDrive :size="18" /></div>
          <span>我的文件</span>
        </button>
        <button @click="switchTab('favorites')" class="drive-sidebar-nav-item" :class="{ active: activeTab === 'favorites' }">
          <div class="drive-sidebar-nav-icon"><Star :size="18" /></div>
          <span>收藏夹</span>
        </button>
        <button @click="switchTab('trash')" class="drive-sidebar-nav-item" :class="{ active: activeTab === 'trash' }">
          <div class="drive-sidebar-nav-icon"><Trash2 :size="18" /></div>
          <span>回收站</span>
        </button>
      </nav>

      <div class="drive-sidebar-divider"></div>

      <!-- 快捷访问 -->
      <div class="drive-sidebar-group">
        <div class="drive-sidebar-group-header">
          <span class="drive-sidebar-group-title">快捷访问</span>
          <button @click.stop="openQuickAccessPicker" class="drive-sidebar-group-add" title="添加快捷访问">
            <Plus :size="14" />
          </button>
        </div>
        <div class="drive-sidebar-group-items">
          <!-- 拖拽时显示的 "+" 投放区域 -->
          <div
            v-if="draggingFileIds.size > 0"
            class="drive-qa-drop-zone"
            @dragover.prevent="dragOverFolderId = '__qa_container__'"
            @dragleave="dragOverFolderId = null"
            @drop.prevent="onDropToQuickAccess()"
          >
            <Plus :size="14" />
            <span>添加到快捷访问</span>
          </div>
          <div
            v-for="item in quickAccessItems"
            :key="item.id"
            class="drive-sidebar-group-item"
            :class="{ 'drag-over-qa': dragOverFolderId === item.file_id }"
            @click="goToQuickAccess(item)"
            :title="item.name"
            @dragover.stop="onDragOver($event, item)"
            @dragleave.stop="dragOverFolderId = null"
            @drop.stop="item.is_folder && onDropHandler(item.file_id)"
          >
            <div class="drive-sidebar-item-icon" v-html="getFileIcon({ name: item.file_name || item.name, is_folder: item.is_folder })"></div>
            <span class="drive-sidebar-item-label">{{ item.name }}</span>
            <button @click.stop="removeQuickAccess(item.id)" class="drive-sidebar-item-action drive-sidebar-item-action-danger" title="移除">
              <X :size="11" />
            </button>
          </div>
          <div v-if="quickAccessItems.length === 0" class="drive-sidebar-group-empty">
            点击 + 添加快捷访问
          </div>
        </div>
      </div>

      <div class="drive-sidebar-divider"></div>


      <!-- 侧边栏底部：用户信息 + 退出 -->
      <div class="drive-sidebar-footer">
        <span class="drive-sidebar-username">{{ username }}</span>
        <button @click="goLogout" class="drive-sidebar-logout" title="退出登录">
          <LogOut :size="14" />
        </button>
      </div>
    </aside>

    <!-- 快捷访问文件选择器模态框 -->
    <div v-if="showQuickAccessPicker" class="qa-picker-overlay" @click.self="showQuickAccessPicker = false">
      <div class="qa-picker-dialog">
        <div class="qa-picker-header">
          <span class="qa-picker-title">添加快捷访问</span>
          <button @click="showQuickAccessPicker = false" class="qa-picker-close"><X :size="16" /></button>
        </div>

        <!-- 面包屑导航 -->
        <div class="qa-picker-breadcrumb">
          <button @click="pickerGoToRoot" class="qa-picker-bc-btn" :class="{ active: !pickerCurrentFolderId }">根目录</button>
          <template v-for="(item, index) in pickerBreadcrumb" :key="item.id">
            <span class="qa-picker-bc-sep">/</span>
            <button
              @click="pickerGoToBreadcrumb(index)"
              class="qa-picker-bc-btn"
              :class="{ active: index === pickerBreadcrumb.length - 1 }"
            >{{ item.name }}</button>
          </template>
        </div>

        <!-- 文件列表 -->
        <div class="qa-picker-body">
          <div v-if="pickerLoading" class="qa-picker-loading">加载中...</div>
          <div v-else-if="pickerFiles.length === 0" class="qa-picker-empty">此文件夹为空</div>
          <div v-else class="qa-picker-list">
            <div
              v-for="file in pickerFiles"
              :key="file.id"
              class="qa-picker-item"
              :class="{ 'is-selected': pickerSelectedFile?.id === file.id }"
              @click="pickerSelectFile(file)"
              @dblclick="file.is_folder ? pickerEnterFolder(file) : pickerConfirm()"
            >
              <div class="qa-picker-item-icon" v-html="getFileIcon(file)"></div>
              <span class="qa-picker-item-name">{{ file.name }}</span>
              <div class="qa-picker-item-radio" :class="{ checked: pickerSelectedFile?.id === file.id }">
                <div v-if="pickerSelectedFile?.id === file.id" class="qa-picker-item-radio-dot"></div>
              </div>
            </div>
          </div>
        </div>

        <!-- 操作按钮 -->
        <div class="qa-picker-footer">
          <button @click="showQuickAccessPicker = false" class="qa-picker-btn qa-picker-btn-cancel">取消</button>
          <button
            @click="pickerConfirm"
            class="qa-picker-btn qa-picker-btn-confirm"
            :disabled="!pickerSelectedFile"
          >确认添加</button>
        </div>
      </div>
    </div>

    <div class="drive-main">
    <div class="drive-container" :class="{ 'with-preview': previewingFile }">


    <!-- 标签页导航 -->
    <div class="drive-tabs" v-if="false">
      <button
        @click="switchTab('files')"
        class="drive-tab"
        :class="{ active: activeTab === 'files' }"
      >文件</button>
      <button
        @click="switchTab('favorites')"
        class="drive-tab"
        :class="{ active: activeTab === 'favorites' }"
      >
        <Star :size="13" />
        收藏
      </button>
      <button
        @click="switchTab('trash')"
        class="drive-tab"
        :class="{ active: activeTab === 'trash' }"
      >
        <Trash2 :size="13" />
        回收站
      </button>
    </div>

    <!-- 操作栏：搜索 + 操作按钮 -->
    <div class="drive-actions-row" v-if="activeTab === 'files'">
      <div class="drive-search-wrap">
        <Search :size="14" class="drive-search-icon" />
        <input
          v-model="searchKeyword"
          @keyup.enter="doSearch"
          placeholder="搜索文件..."
          class="drive-search-input"
        />
        <button v-if="searchKeyword" @click="clearSearch" class="drive-search-clear">
          <X :size="14" />
        </button>
      </div>

      <div class="drive-actions-buttons">
        <button @click="startNewFolder" class="drive-action-btn">
          <FolderPlus :size="14" />
          <span>新建文件夹</span>
        </button>
        <button
          v-if="clipboard.length > 0"
          @click="pasteFromClipboard"
          class="drive-action-btn drive-action-btn-primary"
        >
          <Clipboard :size="14" />
          <span>粘贴 ({{ clipboard.length }})</span>
        </button>
        <!-- 全选/取消全选 -->
        <button
          v-if="files.length > 0"
          @click="toggleSelectAll"
          class="drive-action-btn"
        >
          <CheckCheck :size="14" v-if="selectedIds.size !== files.length" />
          <X :size="14" v-else />
          <span>{{ selectedIds.size === files.length ? '取消全选' : '全选' }}</span>
        </button>
        <!-- 下载 -->
        <button
          v-if="selectedIds.size > 0"
          @click="batchDownloadFiles"
          class="drive-action-btn"
        >
          <Download :size="14" />
          <span>下载</span>
        </button>
        <!-- 移动 -->
        <button
          v-if="selectedIds.size > 0"
          @click="openBatchMoveDialog"
          class="drive-action-btn"
        >
          <FolderPlus :size="14" />
          <span>移动</span>
        </button>
        <!-- 复制 -->
        <button
          v-if="selectedIds.size > 0"
          @click="batchCopyToClipboard"
          class="drive-action-btn"
        >
          <Copy :size="14" />
          <span>复制</span>
        </button>
        <!-- 剪切 -->
        <button
          v-if="selectedIds.size > 0"
          @click="batchCutToClipboard"
          class="drive-action-btn"
        >
          <Scissors :size="14" />
          <span>剪切</span>
        </button>
        <!-- 删除 - 始终可见，未选择时禁用 -->
        <button
          @click="batchDeleteFiles"
          class="drive-action-btn drive-action-btn-danger"
          :disabled="selectedIds.size === 0 || batchDeleting"
          :title="selectedIds.size === 0 ? '请先选择要删除的文件' : `删除选中的 ${selectedIds.size} 个文件`"
        >
          <Trash2 :size="14" v-if="!batchDeleting" />
          <Loader :size="14" class="icon-spin" v-else />
          <span>{{ batchDeleting ? '删除中...' : selectedIds.size > 0 ? `删除 (${selectedIds.size})` : '删除' }}</span>
        </button>
        <div class="drive-upload-dropdown">
          <button
            ref="uploadBtnRef"
            @click="toggleUploadMenu"
            class="drive-action-btn drive-upload-btn"
            :class="{ uploading: isUploading }"
            :disabled="isUploading"
          >
            <Upload :size="14" />
            <span>{{ isUploading ? '上传中...' : '上传' }}</span>
          </button>
          <Transition name="fade">
             <div v-if="showUploadMenu" class="drive-upload-menu" :style="uploadMenuStyle" @click.stop>
               <button @click="triggerUploadFiles(); showUploadMenu = false" class="drive-upload-menu-item">
                 <Upload :size="14" />
                 <span>上传文件</span>
               </button>
               <button @click="triggerUploadFolder(); showUploadMenu = false" class="drive-upload-menu-item">
                 <FolderPlus :size="14" />
                 <span>上传文件夹</span>
               </button>
             </div>
           </Transition>
          <input
            ref="uploadFilesInput"
            type="file"
            multiple
            @change="handleUpload"
            style="display:none"
            :disabled="isUploading"
          />
          <input
            ref="uploadFolderInput"
            type="file"
            webkitdirectory
            multiple
            @change="handleUpload"
            style="display:none"
            :disabled="isUploading"
          />
        </div>

        <!-- 上传状态面板 -->
        <Transition name="slide-down">
          <div v-if="showUploadPanel" class="drive-upload-panel">
            <div class="drive-upload-panel-header">
              <span class="drive-upload-panel-title">
                上传文件
                <span class="drive-upload-panel-count">{{ uploadCompletedCount }}/{{ uploadTotalCount }}</span>
              </span>
              <button v-if="!isUploading" @click="closeUploadPanel" class="drive-upload-panel-close" title="关闭">
                <X :size="14" />
              </button>
            </div>
            <div class="drive-upload-panel-body">
              <div
                v-for="task in uploadTasks"
                :key="task.id"
                class="drive-upload-panel-item"
                :class="'upload-' + task.status"
              >
                <div class="drive-upload-item-icon">
                  <template v-if="task.status === 'success'"><Check :size="14" class="icon-success" /></template>
                  <template v-else-if="task.status === 'failed'"><X :size="14" class="icon-failed" /></template>
                  <template v-else-if="task.status === 'uploading'"><span class="icon-spin"><Loader :size="14" /></span></template>
                  <template v-else><span class="icon-pending">•</span></template>
                </div>
                <div class="drive-upload-item-info">
                  <div class="drive-upload-item-name" :title="task.name">{{ task.name }}</div>
                  <div class="drive-upload-item-progress" v-if="task.status === 'uploading'">
                    <div class="drive-upload-item-bar">
                      <div class="drive-upload-item-fill" :style="{ width: task.progress + '%' }"></div>
                    </div>
                    <span class="drive-upload-item-percent">{{ task.progress }}%</span>
                  </div>
                  <div class="drive-upload-item-error" v-if="task.status === 'failed'">
                    <span class="error-text">{{ task.error }}</span>
                    <button v-if="!isUploading" @click="retryUploadTask(task)" class="drive-upload-retry-btn" title="重试">
                      <RotateCcw :size="12" />
                      重试
                    </button>
                  </div>
                  <div class="drive-upload-item-success" v-if="task.status === 'success'">
                    <span>上传成功</span>
                  </div>
                </div>
              </div>
            </div>
            <div class="drive-upload-panel-footer" v-if="!isUploading">
              <div class="drive-upload-panel-summary">
                成功 <strong>{{ uploadCompletedCount }}</strong> / 失败 <strong>{{ uploadFailedCount }}</strong>
              </div>
              <div class="drive-upload-panel-actions">
                <button v-if="uploadFailedCount > 0" @click="retryAllFailed" class="drive-action-btn drive-upload-retry-all">
                  <RotateCcw :size="14" />
                  重试全部
                </button>
                <button v-if="uploadFailedCount > 0 && uploadCompletedCount > 0" @click="rollbackUpload" class="drive-action-btn drive-upload-rollback-btn">
                  <Trash2 :size="14" />
                  回滚已上传
                </button>
              </div>
            </div>
          </div>
        </Transition>
      </div>
    </div>

    <!-- 面包屑导航栏 -->
    <div class="drive-breadcrumb-row" v-if="activeTab === 'files'">
      <button
        v-if="!isSearching"
        @click="currentFolderId ? goBack() : null"
        class="drive-back-btn"
        :class="{ 'drive-back-btn-placeholder': !currentFolderId }"
        :disabled="!currentFolderId"
        :title="currentFolderId ? '返回上级' : ''"
      >
        <ArrowLeft :size="16" />
      </button>
      <button @click="goToRoot" class="drive-tool-btn breadcrumb-root-btn" title="根目录">
        <HardDrive :size="16" />
        <span>根目录</span>
      </button>
      <template v-if="breadcrumb.length > 0">
        <span class="drive-breadcrumb-sep">/</span>
        <!-- 如果面包屑太多，折叠中间显示省略号 -->
        <template v-if="breadcrumb.length > 2">
          <span class="drive-breadcrumb-ellipsis">...</span>
          <span class="drive-breadcrumb-sep">/</span>
          <template v-for="(item, idx) in breadcrumb.slice(-2)" :key="item.id">
            <button
              @click="goToBreadcrumb(breadcrumb.length - 2 + idx)"
              class="drive-breadcrumb-item"
              :class="{ 'is-current': idx === breadcrumb.slice(-2).length - 1 }"
              :title="item.name"
            >
              {{ item.name }}
            </button>
            <span v-if="idx < breadcrumb.slice(-2).length - 1" class="drive-breadcrumb-sep">/</span>
          </template>
        </template>
        <template v-else>
          <template v-for="(item, idx) in breadcrumb" :key="item.id">
            <button
              @click="goToBreadcrumb(idx)"
              class="drive-breadcrumb-item"
              :class="{ 'is-current': idx === breadcrumb.length - 1 }"
              :title="item.name"
            >
              {{ item.name }}
            </button>
            <span v-if="idx < breadcrumb.length - 1" class="drive-breadcrumb-sep">/</span>
          </template>
        </template>
      </template>
      <template v-if="currentFolderName && breadcrumb.length === 0">
        <span class="drive-breadcrumb-sep">/</span>
        <span class="drive-breadcrumb-current">{{ currentFolderName }}</span>
      </template>
    </div>

    <!-- 回收站操作栏 -->
    <div class="drive-actions-row" v-if="activeTab === 'trash' && files.length > 0">
      <button @click="emptyTrash" class="drive-action-btn drive-action-btn-danger">
        <Trash2 :size="16" />
        <span>清空回收站</span>
      </button>
    </div>

    <!-- 错误提示 -->
    <div v-if="errorMsg" class="drive-error">{{ errorMsg }}</div>

    <!-- 文件列表 -->
    <div
      class="drive-file-list"
      v-if="!loading"
      @dragover="onFileDragOver"
      @dragleave="onFileDragLeave"
      @drop.prevent
    >
      <div v-if="files.length === 0 && !pendingNewFolder" class="drive-empty">
        <Folder v-if="activeTab === 'files'" :size="48" class="drive-empty-icon" />
        <Star v-else-if="activeTab === 'favorites'" :size="48" class="drive-empty-icon" />
        <Trash2 v-else-if="activeTab === 'trash'" :size="48" class="drive-empty-icon" />
        <p v-if="activeTab === 'files'">此文件夹为空</p>
        <p v-else-if="activeTab === 'favorites'">暂无收藏文件</p>
        <p v-else-if="activeTab === 'trash'">回收站为空</p>
        <p class="drive-empty-hint" v-if="activeTab === 'files'">上传文件或创建文件夹开始使用</p>
        <p class="drive-empty-hint" v-else-if="activeTab === 'favorites'">点击文件旁的星标收藏重要文件</p>
      </div>

      <TransitionGroup name="list" tag="div" class="drive-file-list-inner">
        <!-- 新建文件夹行（在列表中） -->
        <div v-if="pendingNewFolder" key="new-folder" class="drive-file-item drive-file-item-new-folder">
          <div class="drive-checkbox" style="opacity:0">
            <div class="drive-checkbox-box"></div>
          </div>
          <div class="drive-file-icon drive-new-folder-icon" v-html="getFileIcon({ is_folder: true, name: '' })"></div>
          <div class="drive-file-info">
            <div class="drive-new-folder-inline">
              <input
                v-model="newFolderName"
                @keyup.enter="createFolder"
                @keyup.escape="pendingNewFolder = false; newFolderName = ''"
                placeholder="文件夹名称"
                class="drive-input drive-new-folder-input"
                :class="{ 'input-error': folderConflict }"
                autofocus
              />
              <button @click="createFolder" class="drive-icon-btn drive-icon-btn-confirm" :disabled="folderConflict" title="确认">
                <Check :size="14" />
              </button>
              <button @click="pendingNewFolder = false; newFolderName = ''" class="drive-icon-btn" title="取消">
                <X :size="14" />
              </button>
            </div>
            <div v-if="folderConflict" class="drive-name-conflict">名称已存在</div>
          </div>
        </div>

        <div
          v-for="file in files"
          :key="(file.is_folder ? 'F' : 'D') + '-' + file.id"
          class="drive-file-item"
          :class="{
            'is-folder': file.is_folder,
            'is-selected': selectedIds.has(file.id),
            'has-checked': selectedIds.size > 0,
            'is-dragging': draggingFileIds.has(file.id),
          }"
          :draggable="activeTab === 'files' && !batchMode && renamingId !== file.id"
          @click="handleFileClick(file)"
          @dragstart="onDragStart($event, file)"
          @dragend="onDragEnd"
          @dragover="onDragOver($event, file)"
          @dragleave="onDragLeave($event, file)"
          @drop.prevent="file.is_folder && onDropHandler(file.id)"
        >
        <!-- 勾选框：hover 时显示，有选中项时全部显示 -->
        <div class="drive-checkbox" @click.stop="toggleSelect(file.id)">
          <div class="drive-checkbox-box" :class="{ checked: selectedIds.has(file.id) }">
            <Check v-if="selectedIds.has(file.id)" :size="12" />
          </div>
        </div>

        <div class="drive-file-icon" v-html="getFileIcon(file)"></div>

        <div class="drive-file-info">
          <template v-if="renamingId === file.id">
            <input
              :ref="(el) => { if (el) renameInputEl = el }"
              v-model="renameName"
              @keyup.enter="confirmRename"
              @keyup.escape="cancelRename"
              @mousedown.stop
              @click.stop
              class="drive-input drive-rename-input"
              :class="{ 'input-error': renameConflict }"
              autofocus
            />
            <div v-if="renameConflict" class="drive-name-conflict">名称已存在</div>
          </template>
          <template v-else>
            <span class="drive-file-name">{{ file.name }}</span>
            <span class="drive-file-meta" v-if="!file.is_folder && activeTab !== 'trash'">
              <span class="drive-file-type" :class="{ 'previewable': isPreviewable(file) }">
                {{ file.extension || '未知' }}
              </span>
              · {{ formatSize(file.size) }} · {{ formatDate(file.created_at) }} · {{ file.uploader }}
            </span>
            <span class="drive-file-meta" v-else-if="file.is_folder && activeTab !== 'trash'">
              <span class="drive-file-type folder-type">文件夹</span>
              · {{ formatDate(file.created_at) }} · {{ file.uploader }}
            </span>
            <span class="drive-file-meta" v-else-if="activeTab === 'trash'">
              {{ file.is_folder ? '文件夹' : (file.extension || '文件') }} · 删除于 {{ formatDate(file.deleted_at) }}
            </span>
          </template>
        </div>

        <!-- 收藏星标 -->
        <button
          v-if="activeTab !== 'trash' && !batchMode"
          @click.stop="toggleFavorite(file)"
          class="drive-favorite-btn"
          :class="{ favorited: file.is_favorite }"
          title="收藏"
        >
          <Star v-if="file.is_favorite" :size="14" fill="#f59e0b" color="#f59e0b" />
          <Star v-else :size="14" fill="none" />
        </button>

        <div class="drive-file-actions drive-menu-wrapper" v-if="!batchMode">
          <!-- 回收站操作 -->
          <template v-if="activeTab === 'trash'">
            <button @click.stop="restoreFromTrash(file)" class="drive-file-action-btn" title="恢复">
              <RotateCcw :size="16" />
            </button>
            <button @click.stop="permanentDeleteFile(file)" class="drive-file-action-btn" title="彻底删除">
              <Trash2 :size="16" />
            </button>
          </template>
          <!-- 正常文件操作 -->
          <template v-else>
            <button
              @click.stop="file.is_folder ? enterFolder(file) : (isPreviewable(file) ? previewFile(file) : downloadFile(file))"
              class="drive-file-action-btn"
              :title="file.is_folder ? '打开' : (isPreviewable(file) ? '预览' : '下载')"
            >
              <Eye v-if="!file.is_folder && isPreviewable(file)" :size="16" />
              <Download v-else-if="!file.is_folder" :size="16" />
              <Folder v-else :size="16" />
            </button>
            <button @click.stop="openMenu($event, file)" class="drive-file-action-btn" title="更多">
              <MoreVertical :size="16" />
            </button>
          </template>

          <!-- 右键菜单 -->
          <div v-if="menuFileId === file.id" class="drive-menu" :style="menuStyle" @click.stop>
            <div v-if="isMobile" class="drive-menu-overlay" @click="closeMenu"></div>
            <div class="drive-menu-content" :class="{ 'drive-menu-content-mobile': isMobile }">
            <button @click="startRename(file)" class="drive-menu-item">
              <Pencil :size="13" />
              <span>重命名</span>
            </button>
            <button @click="openMoveDialog(file)" class="drive-menu-item">
              <FolderPlus :size="13" />
              <span>移动</span>
            </button>
            <button @click="copyToClipboard(file)" class="drive-menu-item">
              <Copy :size="13" />
              <span>复制</span>
            </button>
            <button @click="cutToClipboard(file)" class="drive-menu-item">
              <Scissors :size="13" />
              <span>剪切</span>
            </button>
            <button @click="previewFile(file)" class="drive-menu-item" v-if="!file.is_folder && isPreviewable(file)">
              <Eye :size="13" />
              <span>预览</span>
            </button>
            <button @click="downloadFile(file)" class="drive-menu-item" v-if="!file.is_folder">
              <Download :size="13" />
              <span>下载</span>
            </button>
            <button @click="openDetail(file)" class="drive-menu-item">
              <Info :size="13" />
              <span>详情</span>
            </button>
            <div class="drive-menu-divider"></div>
            <button @click="deleteFile(file)" class="drive-menu-item drive-menu-item-danger">
              <Trash2 :size="13" />
              <span>删除</span>
            </button>
            </div>
          </div>
        </div>
      </div>
      </TransitionGroup>
    </div>

    <!-- 加载中 -->
    <div v-if="loading" class="drive-loading">
      <div class="drive-spinner"></div>
      <span>加载中...</span>
    </div>
    </div>

    <!-- 预览面板 -->
    <Transition name="preview-slide">
      <div v-if="previewingFile" class="drive-preview-panel">
        <div class="drive-preview-header">
          <span class="drive-preview-filename">{{ previewingFile.name }}</span>
          <div class="drive-preview-header-actions">
            <button @click="downloadFile(previewingFile)" class="drive-preview-header-btn" title="下载">
              <Download :size="16" />
            </button>
            <button @click="closePreview" class="drive-preview-header-btn" title="关闭">
              <X :size="18" />
            </button>
          </div>
        </div>
        <div class="drive-preview-body">
          <!-- 加载中（覆盖层，不阻止底层元素渲染） -->
          <div v-if="previewLoading && !previewError" class="drive-preview-loading">
            <div class="drive-spinner"></div>
            <span>加载中...</span>
          </div>
          <!-- 加载失败 -->
          <div v-if="previewError" class="drive-preview-error">
            <span>{{ previewError }}</span>
          </div>
          <!-- 图片预览（始终渲染，通过 v-show 控制显示） -->
          <img
            v-if="previewUrl && isPreviewImage(previewingFile) && !previewError"
            :src="previewUrl"
            :alt="previewingFile.name"
            class="drive-preview-image"
            :class="{ 'is-loading': previewLoading }"
            @load="previewLoading = false"
            @error="previewError = '图片加载失败'; previewLoading = false"
          />
          <!-- PDF / 其他文件统一用 iframe（始终渲染） -->
          <iframe
            v-if="previewUrl && !isPreviewImage(previewingFile) && !previewError"
            :src="previewUrl"
            class="drive-preview-iframe"
            :class="{ 'is-loading': previewLoading }"
            frameborder="0"
            @load="previewLoading = false"
            @error="previewError = '加载失败'; previewLoading = false"
          ></iframe>
        </div>
      </div>
    </Transition>
    </div>

    <!-- 文件详情面板 -->
    <Transition name="modal-fade">
      <div v-if="showDetailPanel" class="drive-modal-overlay" @click.self="closeDetail">
        <div class="drive-modal-dialog drive-detail-dialog">
          <div class="drive-modal-header">
            <h3>文件详情</h3>
            <button @click="closeDetail" class="drive-modal-close"><X :size="18" /></button>
          </div>
          <div class="drive-modal-body">
            <div class="drive-detail-grid" v-if="detailFile">
              <div class="drive-detail-row">
                <span class="drive-detail-label">文件名</span>
                <span class="drive-detail-value">{{ detailFile.name }}</span>
              </div>
              <div class="drive-detail-row drive-detail-row-path" v-if="detailPath.length > 0">
                <span class="drive-detail-label">路径</span>
                <span class="drive-detail-value">
                  <span class="drive-path-root">根目录</span>
                  <template v-for="(p, pIdx) in detailPath" :key="p.id">
                    <span class="drive-path-sep">›</span>
                    <span class="drive-path-item">{{ p.name }}</span>
                  </template>
                </span>
              </div>
              <div class="drive-detail-row">
                <span class="drive-detail-label">类型</span>
                <span class="drive-detail-value">{{ detailFile.is_folder ? '文件夹' : '文件' }}</span>
              </div>
              <div class="drive-detail-row" v-if="!detailFile.is_folder">
                <span class="drive-detail-label">大小</span>
                <span class="drive-detail-value">{{ formatSize(detailFile.size) }}</span>
              </div>
              <div class="drive-detail-row" v-if="detailFile.mime_type">
                <span class="drive-detail-label">MIME类型</span>
                <span class="drive-detail-value">{{ detailFile.mime_type }}</span>
              </div>
              <div class="drive-detail-row">
                <span class="drive-detail-label">上传者</span>
                <span class="drive-detail-value">{{ detailFile.uploader }}</span>
              </div>
              <div class="drive-detail-row">
                <span class="drive-detail-label">创建时间</span>
                <span class="drive-detail-value">{{ formatFullDate(detailFile.created_at) }}</span>
              </div>
              <div class="drive-detail-row">
                <span class="drive-detail-label">修改时间</span>
                <span class="drive-detail-value">{{ formatFullDate(detailFile.updated_at) }}</span>
              </div>
              <div class="drive-detail-row" v-if="detailFile.is_favorite">
                <span class="drive-detail-label">收藏</span>
                <span class="drive-detail-value drive-detail-fav">已收藏</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </Transition>

    <!-- 移动对话框 -->
    <Transition name="modal-fade">
      <div v-if="showMoveDialog" class="drive-modal-overlay" @click.self="showMoveDialog = false">
        <div class="drive-modal-dialog drive-move-dialog">
          <div class="drive-modal-header">
            <h3>移动到</h3>
            <button @click="showMoveDialog = false" class="drive-modal-close"><X :size="18" /></button>
          </div>
          <div class="drive-modal-body">
            <div class="drive-move-options">
              <button
                @click="moveTargetParentId = null"
                class="drive-move-option"
                :class="{ active: moveTargetParentId === null }"
              >根目录</button>
              <button
                v-for="item in breadcrumb"
                :key="item.id"
                @click="moveTargetParentId = item.id"
                class="drive-move-option"
                :class="{ active: moveTargetParentId === item.id }"
              >
                <Folder :size="14" />
                {{ item.name }}
              </button>
            </div>
          </div>
          <div class="drive-modal-footer">
            <button @click="showMoveDialog = false" class="drive-action-btn">取消</button>
            <button @click="confirmMove" class="drive-action-btn drive-btn-primary">确认移动</button>
          </div>
        </div>
      </div>
    </Transition>

    </div>
</template>

<style scoped>
.cloud-drive-page {
  height: 100%;
  display: flex;
  flex-direction: row;
  background: var(--color-bg);
  overflow: hidden;
}

/* 预览面板打开时，主区域撑满 */
.cloud-drive-page.has-preview {
  align-items: stretch;
}

/* ===== 左侧边栏 ===== */
.drive-sidebar {
  width: 240px;
  min-width: 240px;
  height: 100%;
  display: flex;
  flex-direction: column;
  background: var(--color-bg-subtle);
  border-right: 1px solid var(--color-border);
  overflow: hidden;
  flex-shrink: 0;
  position: relative;
  z-index: 10;
}

/* 侧边栏头部 */
.drive-sidebar-header {
  padding: 16px 16px 12px;
  flex-shrink: 0;
}

.drive-sidebar-brand {
  display: flex;
  align-items: center;
  gap: 10px;
}

.drive-sidebar-title {
  font-size: 16px;
  font-weight: 650;
  color: var(--color-text);
  letter-spacing: -0.3px;
}

/* 导航 */
.drive-sidebar-nav {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 0 8px 8px;
  flex-shrink: 0;
}

.drive-sidebar-nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 12px;
  border: none;
  background: transparent;
  color: var(--color-text-secondary);
  border-radius: var(--radius-md);
  cursor: pointer;
  font-size: 14px;
  font-weight: 450;
  text-align: left;
  transition: all 0.15s ease;
  width: 100%;
  position: relative;
}

.drive-sidebar-nav-item:hover {
  background: var(--color-bg-hover);
  color: var(--color-text);
}

.drive-sidebar-nav-item.active {
  background: var(--color-accent-subtle);
  color: var(--color-accent);
  font-weight: 500;
}

.drive-sidebar-nav-item.active::before {
  content: '';
  position: absolute;
  left: -8px;
  top: 50%;
  transform: translateY(-50%);
  width: 3px;
  height: 20px;
  border-radius: 0 3px 3px 0;
  background: var(--color-accent);
}

.drive-sidebar-nav-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  flex-shrink: 0;
}

.drive-sidebar-badge {
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 18px;
  height: 18px;
  padding: 0 5px;
  border-radius: 9px;
  background: var(--color-danger);
  color: #fff;
  font-size: 10px;
  font-weight: 600;
}

/* 分割线 */
.drive-sidebar-divider {
  height: 1px;
  background: var(--color-border);
  margin: 4px 12px;
  flex-shrink: 0;
}

/* 分组区域 */
.drive-sidebar-group {
  display: flex;
  flex-direction: column;
  padding: 0 8px;
  flex-shrink: 0;
  overflow: hidden;
}

.drive-sidebar-group-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 12px 4px;
}

.drive-sidebar-group-title {
  font-size: 11px;
  font-weight: 600;
  color: var(--color-text-tertiary);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.drive-sidebar-group-add {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border: none;
  background: transparent;
  color: var(--color-text-tertiary);
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.12s ease;
  opacity: 0;
}

.drive-sidebar-group:hover .drive-sidebar-group-add {
  opacity: 1;
}

.drive-sidebar-group-add:hover {
  background: var(--color-bg-hover);
  color: var(--color-accent);
}

/* 快捷访问添加表单 */
.drive-sidebar-add-form {
  padding: 4px 12px 8px;
}

.drive-sidebar-add-form-inner {
  display: flex;
  align-items: center;
  gap: 4px;
  background: var(--color-bg-raised);
  border: 1px solid var(--color-accent);
  border-radius: var(--radius-sm);
  padding: 2px;
}

.drive-sidebar-input {
  flex: 1;
  min-width: 0;
  padding: 5px 8px;
  border: none;
  background: transparent;
  color: var(--color-text);
  font-size: 12px;
  outline: none;
}

.drive-sidebar-add-confirm,
.drive-sidebar-add-cancel {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border: none;
  background: transparent;
  color: var(--color-text-tertiary);
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.1s ease;
  flex-shrink: 0;
}

.drive-sidebar-add-confirm {
  color: var(--color-accent);
}

.drive-sidebar-add-confirm:hover {
  background: var(--color-accent-subtle);
}

.drive-sidebar-add-cancel:hover {
  background: var(--color-bg-hover);
  color: var(--color-text);
}

/* 分组项目 */
.drive-sidebar-group-items {
  display: flex;
  flex-direction: column;
  gap: 1px;
  padding: 2px 0;
}

.drive-sidebar-group-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 12px;
  border: none;
  background: transparent;
  color: var(--color-text-secondary);
  border-radius: var(--radius-sm);
  cursor: pointer;
  font-size: 13px;
  text-align: left;
  transition: all 0.12s ease;
  width: 100%;
}

.drive-sidebar-group-item:hover {
  background: var(--color-bg-hover);
  color: var(--color-text);
}

.drive-sidebar-group-item.drag-over-qa {
  background: var(--color-accent-subtle) !important;
  color: var(--color-accent) !important;
}

.drive-qa-drop-zone {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  margin: 4px 8px;
  border-radius: 6px;
  border: 2px dashed var(--color-accent);
  color: var(--color-accent);
  font-size: 12px;
  cursor: default;
}

.drive-sidebar-item-icon {
  flex-shrink: 0;
  width: 20px;
  height: 20px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.drive-sidebar-item-icon :deep(svg) {
  width: 18px;
  height: 18px;
}

.drive-sidebar-item-label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.drive-sidebar-item-action {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  border: none;
  background: transparent;
  color: var(--color-text-tertiary);
  border-radius: 4px;
  cursor: pointer;
  opacity: 0;
  transition: all 0.1s ease;
  flex-shrink: 0;
}

.drive-sidebar-group-item:hover .drive-sidebar-item-action {
  opacity: 1;
}

.drive-sidebar-item-action:hover {
  background: var(--color-bg-active);
  color: var(--color-text);
}

.drive-sidebar-item-action-danger:hover {
  background: var(--color-danger-subtle);
  color: var(--color-danger);
}

.drive-sidebar-item-edit-input {
  flex: 1;
  min-width: 0;
  padding: 3px 6px;
  border: 1px solid var(--color-accent);
  background: var(--color-bg-raised);
  color: var(--color-text);
  border-radius: 4px;
  font-size: 12px;
  outline: none;
}

.drive-sidebar-group-empty {
  padding: 6px 12px;
  font-size: 11px;
  color: var(--color-text-tertiary);
  font-style: italic;
}

/* 转换项 */
.drive-sidebar-convert-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  border: none;
  background: transparent;
  color: var(--color-text-secondary);
  border-radius: var(--radius-sm);
  cursor: pointer;
  font-size: 13px;
  text-align: left;
  transition: all 0.12s ease;
  width: 100%;
}

.drive-sidebar-convert-item:hover {
  background: var(--color-bg-hover);
  color: var(--color-text);
}

.drive-sidebar-convert-item.active {
  background: var(--color-accent-subtle);
  color: var(--color-accent);
}

.drive-sidebar-convert-icon {
  flex-shrink: 0;
  opacity: 0.7;
}

.drive-sidebar-convert-item:hover .drive-sidebar-convert-icon {
  opacity: 1;
}

.drive-sidebar-convert-item.active .drive-sidebar-convert-icon {
  opacity: 1;
}

/* 转换展开面板 */
.drive-sidebar-convert-panel {
  overflow: hidden;
}

.drive-sidebar-convert-panel-inner {
  padding: 8px 12px 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.drive-sidebar-convert-desc {
  font-size: 12px;
  line-height: 1.5;
  color: var(--color-text-tertiary);
  margin: 0;
}

.drive-sidebar-convert-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 7px 14px;
  border: none;
  background: var(--color-accent);
  color: #fff;
  border-radius: var(--radius-sm);
  cursor: pointer;
  font-size: 12px;
  font-weight: 500;
  transition: opacity 0.12s ease;
  width: 100%;
}

.drive-sidebar-convert-btn:hover {
  opacity: 0.9;
}

/* 转换面板滑入动画 */
.convert-slide-enter-active {
  transition: all 0.2s ease;
}
.convert-slide-leave-active {
  transition: all 0.15s ease;
}
.convert-slide-enter-from,
.convert-slide-leave-to {
  opacity: 0;
  max-height: 0;
}
.convert-slide-enter-to,
.convert-slide-leave-from {
  opacity: 1;
  max-height: 120px;
}

/* 侧边栏底部 */
.drive-sidebar-footer {
  margin-top: auto;
  padding: 12px 16px;
  border-top: 1px solid var(--color-border);
  display: flex;
  align-items: center;
  flex-shrink: 0;
}

.drive-sidebar-logout {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: 1px solid var(--color-danger);
  background: transparent;
  color: var(--color-danger);
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: all 0.12s ease;
  flex-shrink: 0;
}

.drive-sidebar-logout:hover {
  background: var(--color-danger);
  color: #fff;
}

.drive-sidebar-username {
  flex: 1;
  font-size: 13px;
  color: var(--color-text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  margin-right: 8px;
}

/* 主区域：列表 + 预览面板 */
.drive-main {
  flex: 1;
  min-width: 0;
  height: 100%;
  display: flex;
  overflow: hidden;
}

/* 内容容器，有侧边栏时自适应宽度 */
.drive-container {
  width: 75%;
  max-width: 1100px;
  min-width: 400px;
  height: 100%;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  transition: width 0.3s ease, max-width 0.3s ease, margin 0.3s ease;
  flex-shrink: 0;
  margin: 0 auto;
}

/* 预览面板打开时，列表容器左移靠边 */
.drive-container.with-preview {
  width: 60%;
  max-width: 750px;
  margin: 0;
}

/* 顶部工具栏 */
.drive-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  border-bottom: 1px solid var(--color-border);
  flex-shrink: 0;
}

.drive-toolbar-left {
  display: flex;
  align-items: center;
  gap: 4px;
}

.drive-favicon {
  height: 24px;
  width: 24px;
  border-radius: 4px;
}

.drive-toolbar-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--color-text);
  margin-left: 4px;
}

/* 返回上级按钮 */
.drive-back-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border: 1px solid var(--color-border);
  background: var(--color-bg-raised);
  color: var(--color-text-secondary);
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: all 0.12s ease;
  margin-right: 4px;
}

.drive-back-btn:hover {
  background: var(--color-bg-hover);
  color: var(--color-text);
  border-color: var(--color-accent);
}

.drive-back-btn:disabled {
  cursor: default;
}

.drive-back-btn-placeholder {
  opacity: 0.2;
  border-color: transparent;
  background: transparent;
  pointer-events: none;
}

.drive-tool-btn {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  border: none;
  background: transparent;
  border-radius: var(--radius-md);
  cursor: pointer;
  font-size: 14px;
  font-weight: 600;
  transition: background 0.12s ease;
}

.drive-tool-btn:hover {
  background: var(--color-accent-subtle);
}

/* 面包屑行 */
.drive-breadcrumb-row {
  display: flex;
  align-items: center;
  gap: 2px;
  padding: 4px 16px 8px;
  flex-shrink: 0;
  overflow: hidden;
}

.drive-breadcrumb-row .drive-back-btn {
  flex-shrink: 0;
}

.drive-breadcrumb-row .breadcrumb-root-btn {
  flex-shrink: 0;
}

.drive-breadcrumb-ellipsis {
  color: var(--color-text-tertiary);
  font-size: 13px;
  padding: 0 2px;
  letter-spacing: 1px;
  cursor: default;
}

.drive-breadcrumb-sep {
  color: var(--color-text-tertiary);
  font-size: 14px;
  margin: 0 2px;
  flex-shrink: 0;
}

.drive-breadcrumb-item {
  padding: 4px 8px;
  border: none;
  background: transparent;
  color: var(--color-text-secondary);
  border-radius: var(--radius-sm);
  cursor: pointer;
  font-size: 13px;
  transition: all 0.12s ease;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 120px;
}

.drive-breadcrumb-item:hover {
  background: var(--color-bg-hover);
  color: var(--color-text);
}

.drive-breadcrumb-item.is-current {
  color: var(--color-text);
  font-weight: 500;
  max-width: 200px;
}

.drive-breadcrumb-current {
  color: var(--color-text);
  font-size: 13px;
  font-weight: 500;
}

.drive-toolbar-right {
  display: flex;
  align-items: center;
  gap: 8px;
}

.drive-logout-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: 1px solid var(--color-border);
  background: transparent;
  color: var(--color-text-tertiary);
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: all 0.12s ease;
  margin-left: 6px;
}
.drive-logout-btn:hover {
  background: var(--color-danger-subtle);
  color: var(--color-danger);
  border-color: var(--color-danger);
}

/* 合并操作栏：搜索 + 按钮 */
.drive-actions-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  padding: 6px 12px;
  border-bottom: 1px solid var(--color-border);
  flex-shrink: 0;
}

.drive-search-wrap {
  display: flex;
  align-items: center;
  gap: 8px;
  background: var(--color-bg-subtle);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  padding: 0 10px;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
  flex-shrink: 0;
  width: 240px;
}
.drive-search-wrap:focus-within {
  border-color: var(--color-accent);
  box-shadow: 0 0 0 3px var(--color-accent-subtle);
}
.drive-search-wrap .drive-search-icon {
  color: var(--color-text-tertiary);
  flex-shrink: 0;
}
.drive-search-wrap .drive-search-input {
  flex: 1;
  min-width: 0;
  padding: 6px 0;
  border: none;
  background: transparent;
  color: var(--color-text);
  font-size: 13px;
  outline: none;
}
.drive-search-wrap .drive-search-clear {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border: none;
  background: transparent;
  color: var(--color-text-tertiary);
  cursor: pointer;
  border-radius: 50%;
  flex-shrink: 0;
}
.drive-search-wrap .drive-search-clear:hover {
  background: var(--color-bg-hover);
  color: var(--color-text);
}

.drive-actions-buttons {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
  overflow-x: auto;
  -webkit-overflow-scrolling: touch;
}

/* 操作按钮 */
.drive-action-btn {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 5px 10px;
  border: none;
  background: transparent;
  color: var(--color-text-secondary);
  border-radius: var(--radius-md);
  cursor: pointer;
  font-size: 12px;
  transition: all 0.12s ease;
  white-space: nowrap;
}

.drive-action-btn:hover {
  background: var(--color-bg-hover);
  color: var(--color-text);
}

.drive-upload-btn {
  cursor: pointer;
}

.drive-upload-btn.uploading {
  opacity: 0.6;
  pointer-events: none;
}

.drive-input {
  padding: 7px 10px;
  border: 1px solid var(--color-accent);
  background: var(--color-bg);
  color: var(--color-text);
  border-radius: var(--radius-md);
  font-size: 13px;
  outline: none;
  width: 180px;
  transition: border-color 0.15s ease;
}

.drive-input:focus {
  border-color: var(--color-accent);
  box-shadow: 0 0 0 2px var(--color-accent-subtle);
}

.drive-rename-input {
  width: 200px;
  padding: 4px 8px;
}

.drive-icon-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: 1px solid var(--color-border);
  background: var(--color-bg-raised);
  color: var(--color-text-secondary);
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: all 0.12s ease;
}

.drive-icon-btn:hover {
  background: var(--color-bg-hover);
  color: var(--color-text);
}

.drive-icon-btn-confirm {
  color: var(--color-accent);
  border-color: var(--color-accent);
}

/* 错误提示 */
.drive-error {
  margin: 8px 16px;
  padding: 8px 12px;
  background: var(--color-danger-subtle);
  color: var(--color-danger);
  border-radius: var(--radius-md);
  font-size: 13px;
  flex-shrink: 0;
}

/* 文件列表 */
.drive-file-list {
  flex: 1;
  overflow-y: auto;
  padding: 8px 16px;
}

.drive-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 60px 20px;
  color: var(--color-text-tertiary);
}

.drive-empty-icon {
  opacity: 0.3;
  margin-bottom: 12px;
}

.drive-empty p {
  margin: 0;
  font-size: 14px;
}

.drive-empty-hint {
  font-size: 12px !important;
  margin-top: 4px !important;
  opacity: 0.6;
}

.drive-file-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 12px;
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: background 0.12s ease;
  position: relative;
}

.drive-file-item:hover {
  background: var(--color-bg-hover);
}

.drive-file-icon {
  flex-shrink: 0;
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 6px;
}

.drive-file-icon :deep(svg) {
  width: 28px;
  height: 28px;
  display: block;
}

.drive-file-info {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.drive-file-name {
  font-size: 14px;
  color: var(--color-text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.drive-file-meta {
  font-size: 11px;
  color: var(--color-text-tertiary);
}

.drive-file-type {
  display: inline-block;
  padding: 1px 5px;
  border-radius: 3px;
  font-size: 10px;
  font-weight: 500;
  background: var(--color-bg-secondary);
  color: var(--color-text-secondary);
}

.drive-file-type.previewable {
  background: rgba(34, 197, 94, 0.15);
  color: #22c55e;
}

.drive-file-type.folder-type {
  background: rgba(59, 130, 246, 0.15);
  color: #3b82f6;
}

.drive-file-actions {
  display: flex;
  align-items: center;
  gap: 2px;
  opacity: 0;
  transition: opacity 0.12s ease;
}

.drive-file-item:hover .drive-file-actions {
  opacity: 1;
}

.drive-file-action-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border: none;
  background: transparent;
  color: var(--color-text-tertiary);
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: all 0.12s ease;
}

.drive-file-action-btn:hover {
  background: var(--color-bg-subtle);
  color: var(--color-text);
}

/* 右键菜单 */
.drive-menu-wrapper {
  position: relative;
}

.drive-menu {
  min-width: 140px;
  background: var(--color-bg-raised);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-lg);
  padding: 4px;
}

.drive-menu-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 8px 12px;
  border: none;
  background: transparent;
  color: var(--color-text-secondary);
  border-radius: var(--radius-sm);
  cursor: pointer;
  font-size: 13px;
  text-align: left;
  transition: all 0.1s ease;
  white-space: nowrap;
}

.drive-menu-item:hover {
  background: var(--color-bg-hover);
  color: var(--color-text);
}

.drive-menu-item-danger:hover {
  background: var(--color-danger-subtle);
  color: var(--color-danger);
}

.drive-menu-divider {
  height: 1px;
  background: var(--color-border-subtle);
  margin: 4px 8px;
}

/* 加载动画 */
.drive-loading {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  color: var(--color-text-tertiary);
  font-size: 14px;
}

.drive-spinner {
  width: 32px;
  height: 32px;
  border: 3px solid var(--color-border);
  border-top-color: var(--color-accent);
  border-radius: 50%;
  animation: drive-spin 0.8s linear infinite;
}

@keyframes drive-spin {
  to { transform: rotate(360deg); }
}

/* ========== 平板端适配（max-width: 1024px） ========== */
@media (max-width: 1024px) {
  .drive-sidebar {
    width: 200px;
    min-width: 200px;
  }

  .drive-container {
    min-width: 0;
    width: 65%;
  }

  .drive-container.with-preview {
    width: 45%;
    max-width: 500px;
  }

  .drive-actions-row {
    gap: 8px;
    padding: 8px 12px;
  }

  .drive-search-wrap {
    flex: 1;
    width: auto;
    min-width: 120px;
  }

  .drive-actions-buttons {
    order: 2;
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
    gap: 4px;
  }

  .drive-action-btn {
    padding: 6px 10px;
    font-size: 12px;
    white-space: nowrap;
  }

  .drive-action-btn span {
    display: inline;
  }

  .drive-breadcrumb-item {
    font-size: 12px;
    max-width: 80px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .drive-breadcrumb-row {
    padding: 4px 12px 8px;
  }
}

/* ========== 移动端适配（max-width: 768px） ========== */
@media (max-width: 768px) {
  .cloud-drive-page {
    padding: 0;
  }

  /* 移动端侧边栏隐藏，通过覆盖层显示 */
  .drive-sidebar {
    display: none;
  }

  /* 容器占满宽度 */
  .drive-container {
    width: 100%;
    max-width: 100%;
    min-width: 0;
  }

  .drive-container.with-preview {
    width: 100%;
    max-width: 100%;
    min-width: 0;
  }

  /* 工具栏：允许换行，缩小间距 */
  .drive-toolbar {
    padding: 8px 10px;
    flex-wrap: wrap;
    gap: 6px;
  }

  .drive-toolbar-left {
    flex: 0;
  }

  .drive-toolbar-right {
    width: auto;
    justify-content: flex-end;
  }

  .drive-toolbar-title {
    display: none;
  }

  /* 操作栏：移动端 */
  .drive-actions-row {
    flex-wrap: wrap;
    padding: 8px 10px;
    gap: 8px;
  }

  .drive-search-wrap {
    flex: 1;
    width: auto;
    min-width: 0;
  }

  .drive-actions-buttons {
    order: 2;
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
  }

  /* 面包屑：缩小字号，允许截断 */
  .drive-breadcrumb-item {
    font-size: 12px;
    padding: 3px 5px;
    max-width: 80px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .drive-breadcrumb-sep {
    font-size: 12px;
    margin: 0 1px;
  }

  .drive-breadcrumb-current {
    font-size: 12px;
    max-width: 100px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  /* 操作按钮区 */
  .drive-action-btn {
    padding: 10px 12px;
    font-size: 13px;
    min-height: 40px;
  }

  .drive-action-btn span {
    display: inline;
  }

  .drive-new-folder-row {
    flex: 1;
  }

  .drive-input {
    width: 100%;
    max-width: 160px;
    font-size: 14px;
    padding: 10px 12px;
  }

  .drive-rename-input {
    width: 140px;
    padding: 6px 8px;
  }

  .drive-icon-btn {
    width: 36px;
    height: 36px;
  }

  /* 文件列表 */
  .drive-file-list {
    padding: 4px 8px;
  }

  .drive-file-item {
    padding: 12px 10px;
    gap: 10px;
  }

  /* 移动端文件操作按钮始终显示 */
  .drive-file-actions {
    opacity: 1;
    gap: 4px;
  }

  .drive-file-action-btn {
    width: 36px;
    height: 36px;
  }

  .drive-file-icon {
    width: 36px;
    height: 36px;
  }

  .drive-file-icon :deep(svg) {
    width: 30px;
    height: 30px;
  }

  .drive-file-name {
    font-size: 14px;
  }

  .drive-file-meta {
    font-size: 11px;
  }

  /* 空状态 */
  .drive-empty {
    padding: 40px 16px;
  }

  .drive-empty p {
    font-size: 13px;
  }

  /* 移动端菜单：居中弹窗样式 */
  .drive-menu {
    min-width: 200px;
    border-radius: var(--radius-lg);
    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
  }

  .drive-menu-overlay {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.4);
    z-index: -1;
  }

  .drive-menu-content-mobile {
    position: relative;
    z-index: 1;
    background: var(--color-bg-raised);
    border-radius: var(--radius-lg);
    padding: 8px;
  }

  .drive-menu-item {
    padding: 12px 16px;
    font-size: 15px;
    gap: 10px;
  }

  .drive-menu-divider {
    margin: 6px 10px;
  }

  /* 加载动画 */
  .drive-loading {
    font-size: 13px;
  }

  .drive-spinner {
    width: 28px;
    height: 28px;
  }
}

/* ========== 预览面板 ========== */
.drive-preview-panel {
  flex: 1;
  min-width: 0;
  height: 100%;
  display: flex;
  flex-direction: column;
  background: var(--color-bg);
  border-left: 1px solid var(--color-border);
  overflow: hidden;
}

.drive-preview-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  border-bottom: 1px solid var(--color-border);
  flex-shrink: 0;
  background: var(--color-bg-raised);
}

.drive-preview-filename {
  font-size: 14px;
  font-weight: 500;
  color: var(--color-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
  min-width: 0;
  margin-right: 12px;
}

.drive-preview-header-actions {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
}

.drive-preview-header-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border: none;
  background: transparent;
  color: var(--color-text-tertiary);
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: all 0.12s ease;
}

.drive-preview-header-btn:hover {
  background: var(--color-bg-hover);
  color: var(--color-text);
}

.drive-preview-body {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: auto;
  padding: 16px;
}

.drive-preview-loading,
.drive-preview-error {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  color: var(--color-text-tertiary);
  font-size: 14px;
}

.drive-preview-error {
  color: var(--color-danger);
}

.drive-preview-image {
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
  border-radius: var(--radius-md);
}

.drive-preview-iframe {
  width: 100%;
  height: 100%;
  border: none;
  border-radius: var(--radius-md);
}

/* 预览面板滑入动画 */
.preview-slide-enter-active {
  transition: all 0.3s ease;
}
.preview-slide-leave-active {
  transition: all 0.2s ease;
}
.preview-slide-enter-from {
  opacity: 0;
  transform: translateX(20px);
}
.preview-slide-leave-to {
  opacity: 0;
  transform: translateX(20px);
}

/* 移动端：预览面板全屏覆盖 */
@media (max-width: 768px) {
  .drive-container.with-preview {
    display: none;
  }

  .drive-preview-panel {
    position: fixed;
    inset: 0;
    z-index: 1000;
    border-left: none;
  }

  .drive-preview-body {
    padding: 12px;
  }
}
/* 标签页 */
.drive-tabs {
  display: flex;
  gap: 0;
  padding: 0 16px;
  border-bottom: 1px solid var(--color-border);
  flex-shrink: 0;
}
.drive-tab {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 10px 16px;
  border: none;
  background: transparent;
  color: var(--color-text-tertiary);
  font-size: 13px;
  cursor: pointer;
  border-bottom: 2px solid transparent;
  transition: all 0.12s ease;
  position: relative;
}
.drive-tab:hover {
  color: var(--color-text-secondary);
}
.drive-tab.active {
  color: var(--color-accent);
  border-bottom-color: var(--color-accent);
  font-weight: 500;
}
.drive-tab-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 18px;
  height: 18px;
  padding: 0 5px;
  border-radius: 9px;
  background: var(--color-danger);
  color: #fff;
  font-size: 10px;
  font-weight: 600;
}

/* 批量选择复选框 */
.drive-checkbox-box {
  width: 18px;
  height: 18px;
  border: 2px solid var(--color-border);
  border-radius: 4px;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.12s ease;
  cursor: pointer;
}
.drive-checkbox-box.checked {
  background: var(--color-accent);
  border-color: var(--color-accent);
  color: #fff;
}
.drive-file-item.is-selected {
  background: var(--color-accent-subtle);
}

/* 收藏按钮 */
.drive-favorite-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: none;
  background: transparent;
  color: var(--color-text-tertiary);
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: all 0.12s ease;
  flex-shrink: 0;
}
.drive-favorite-btn.favorited {
  color: #f59e0b;
  background: rgba(245, 158, 11, 0.1);
}

/* 名称冲突检测 */
.drive-name-conflict {
  font-size: 12px;
  color: #e74c3c;
  margin-top: 2px;
}
.drive-input.input-error {
  border-color: #e74c3c;
  box-shadow: 0 0 0 2px rgba(231, 76, 60, 0.15);
}

/* 模态框 */
.drive-modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
  backdrop-filter: blur(2px);
}
.drive-modal-dialog {
  background: var(--color-bg-raised);
  border-radius: var(--radius-lg);
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.2);
  width: 90%;
  max-width: 440px;
  max-height: 90vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.drive-modal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 18px;
  border-bottom: 1px solid var(--color-border);
  flex-shrink: 0;
}
.drive-modal-header h3 {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--color-text);
}
.drive-modal-close {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border: none;
  background: transparent;
  color: var(--color-text-tertiary);
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: all 0.12s ease;
}
.drive-modal-close:hover {
  background: var(--color-bg-hover);
  color: var(--color-text);
}
.drive-modal-body {
  padding: 18px;
  overflow-y: auto;
  flex: 1;
}
.drive-modal-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: 12px 18px;
  border-top: 1px solid var(--color-border);
  flex-shrink: 0;
}
.drive-form-group {
  margin-bottom: 14px;
}
.drive-form-group label {
  display: block;
  font-size: 12px;
  color: var(--color-text-secondary);
  margin-bottom: 4px;
  font-weight: 500;
}

/* 详情面板 */
.drive-detail-dialog {
  max-width: 380px;
}
.drive-detail-grid {
  display: flex;
  flex-direction: column;
  gap: 0;
}
.drive-detail-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 0;
  border-bottom: 1px solid var(--color-border-subtle);
}
.drive-detail-row:last-child {
  border-bottom: none;
}
.drive-detail-label {
  font-size: 13px;
  color: var(--color-text-tertiary);
  flex-shrink: 0;
}
.drive-detail-value {
  font-size: 13px;
  color: var(--color-text);
  text-align: right;
  word-break: break-all;
  max-width: 60%;
}
.drive-detail-fav {
  color: #f59e0b;
  font-weight: 500;
}
.drive-detail-row-path {
  flex-direction: column;
  align-items: flex-start;
  gap: 4px;
}
.drive-detail-row-path .drive-detail-label {
  align-self: flex-start;
}
.drive-detail-row-path .drive-detail-value {
  text-align: left;
  max-width: 100%;
  font-size: 12px;
  color: var(--color-text-tertiary);
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 2px;
}
.drive-path-root {
  color: var(--color-text-tertiary);
}
.drive-path-sep {
  color: var(--color-border);
  margin: 0 2px;
}
.drive-path-item {
  color: var(--color-text-secondary);
}

/* 操作按钮样式 */
.drive-action-btn.active {
  background: var(--color-accent-subtle);
  color: var(--color-accent);
}
.drive-action-btn-danger {
  color: var(--color-danger);
}
.drive-action-btn-danger:hover {
  background: var(--color-danger-subtle);
}
.drive-action-btn-danger:disabled {
  opacity: 0.4;
  cursor: not-allowed;
  pointer-events: auto;
}
.drive-btn-primary {
  background: var(--color-accent) !important;
  color: #fff !important;
}
.drive-btn-primary:hover {
  opacity: 0.9;
}

/* 模态框过渡动画 */
.modal-fade-enter-active {
  transition: all 0.2s ease;
}
.modal-fade-leave-active {
  transition: all 0.15s ease;
}
.modal-fade-enter-from,
.modal-fade-leave-to {
  opacity: 0;
}
.modal-fade-enter-from .drive-modal-dialog,
.modal-fade-leave-to .drive-modal-dialog {
  transform: scale(0.95);
}

/* 新建文件夹行：内联显示 */
.drive-new-folder-inline {
  display: flex;
  align-items: center;
  gap: 6px;
}

.drive-new-folder-input {
  width: 160px;
  padding: 6px 10px;
  margin-right: auto;
}

.drive-file-item-new-folder {
  background: var(--color-bg-hover) !important;
  border: 1px dashed var(--color-border);
  border-radius: var(--radius-md);
  margin-bottom: 4px;
}

.drive-new-folder-icon :deep(svg) {
  width: 28px;
  height: 28px;
}

/* TransitionGroup 列表动画：新项目滑入 + 已有项目下移 */
.list-enter-active {
  transition: all 0.25s ease;
}
.list-leave-active {
  transition: all 0.15s ease;
  position: absolute;
}
.list-move {
  transition: transform 0.25s ease;
}
.list-enter-from {
  opacity: 0;
  transform: translateY(-12px);
}
.list-leave-to {
  opacity: 0;
  transform: translateY(-12px);
}

.drive-file-list-inner {
  position: relative;
}

/* 勾选框默认隐藏，hover 时显示，有选中项时全部显示 */
.drive-checkbox {
  flex-shrink: 0;
  padding: 4px;
  opacity: 0;
  transition: opacity 0.12s ease;
}

.drive-file-item:hover .drive-checkbox,
.drive-file-item.has-checked .drive-checkbox {
  opacity: 1;
}

.drive-file-item.has-checked .drive-checkbox {
  opacity: 1;
}

/* 拖拽样式 */
.drive-file-item.is-dragging {
  opacity: 0.4;
}

/* 粘贴按钮 */
.drive-action-btn-primary {
  background: var(--color-accent) !important;
  color: #fff !important;
  border-color: var(--color-accent) !important;
}
.drive-action-btn-primary:hover {
  opacity: 0.9;
}

/* 上传下拉菜单 */
.drive-upload-dropdown {
  position: relative;
}
.drive-upload-dropdown .drive-upload-menu {
  min-width: 140px;
  background: var(--color-bg-raised);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-lg);
  padding: 4px;
}
.drive-upload-menu-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 8px 12px;
  border: none;
  background: transparent;
  color: var(--color-text-secondary);
  border-radius: var(--radius-sm);
  cursor: pointer;
  font-size: 13px;
  text-align: left;
  transition: all 0.1s ease;
  white-space: nowrap;
}
.drive-upload-menu-item:hover {
  background: var(--color-bg-hover);
  color: var(--color-text);
}

/* 上传状态面板 */
.drive-upload-panel {
  position: fixed;
  right: 24px;
  bottom: 24px;
  width: 380px;
  max-height: 420px;
  background: var(--color-bg-raised);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-lg);
  z-index: 1000;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.drive-upload-panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 14px;
  border-bottom: 1px solid var(--color-border);
  background: var(--color-bg-secondary);
}
.drive-upload-panel-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--color-text);
  display: flex;
  align-items: center;
  gap: 8px;
}
.drive-upload-panel-count {
  font-size: 12px;
  color: var(--color-text-tertiary);
  font-weight: 400;
}
.drive-upload-panel-close {
  background: none;
  border: none;
  color: var(--color-text-tertiary);
  cursor: pointer;
  padding: 4px;
  border-radius: var(--radius-sm);
  display: flex;
  align-items: center;
}
.drive-upload-panel-close:hover {
  background: var(--color-bg-hover);
  color: var(--color-text);
}
.drive-upload-panel-body {
  flex: 1;
  overflow-y: auto;
  padding: 4px 0;
}
.drive-upload-panel-item {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 8px 14px;
  border-bottom: 1px solid var(--color-border-light, #f0f0f0);
  font-size: 12px;
}
.drive-upload-panel-item:last-child {
  border-bottom: none;
}
.drive-upload-item-icon {
  flex-shrink: 0;
  width: 18px;
  height: 18px;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-top: 1px;
}
.icon-success { color: var(--color-success, #22c55e); }
.icon-failed { color: var(--color-danger, #ef4444); }
.icon-pending { color: var(--color-text-tertiary); font-size: 16px; }
.icon-spin { display: inline-flex; }
.icon-spin svg { animation: spin 1s linear infinite; }
@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }

.drive-upload-item-info {
  flex: 1;
  min-width: 0;
}
.drive-upload-item-name {
  font-size: 12px;
  color: var(--color-text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  margin-bottom: 2px;
}
.drive-upload-item-progress {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 4px;
}
.drive-upload-item-bar {
  flex: 1;
  height: 4px;
  background: var(--color-bg-tertiary);
  border-radius: 2px;
  overflow: hidden;
}
.drive-upload-item-fill {
  height: 100%;
  background: var(--color-primary);
  border-radius: 2px;
  transition: width 0.15s ease;
}
.drive-upload-item-percent {
  font-size: 11px;
  color: var(--color-text-tertiary);
  min-width: 32px;
  text-align: right;
}
.drive-upload-item-error {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 2px;
  flex-wrap: wrap;
}
.drive-upload-item-error .error-text {
  font-size: 11px;
  color: var(--color-danger, #ef4444);
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.drive-upload-item-success {
  font-size: 11px;
  color: var(--color-success, #22c55e);
  margin-top: 2px;
}
.drive-upload-retry-btn {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  font-size: 11px;
  color: var(--color-primary);
  background: none;
  border: 1px solid var(--color-primary);
  border-radius: var(--radius-sm);
  padding: 1px 6px;
  cursor: pointer;
  white-space: nowrap;
  transition: all 0.1s ease;
}
.drive-upload-retry-btn:hover {
  background: var(--color-primary);
  color: white;
}
.drive-upload-panel-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 14px;
  border-top: 1px solid var(--color-border);
  background: var(--color-bg-secondary);
}
.drive-upload-panel-summary {
  font-size: 12px;
  color: var(--color-text-secondary);
}
.drive-upload-panel-summary strong {
  font-weight: 600;
}
.drive-upload-panel-actions {
  display: flex;
  gap: 6px;
}
.drive-upload-retry-all {
  font-size: 12px !important;
  padding: 4px 10px !important;
}
.drive-upload-rollback-btn {
  font-size: 12px !important;
  padding: 4px 10px !important;
  color: var(--color-danger, #ef4444) !important;
  border-color: var(--color-danger, #ef4444) !important;
}
.drive-upload-rollback-btn:hover {
  background: var(--color-danger, #ef4444) !important;
  color: white !important;
}

.slide-down-enter-active,
.slide-down-leave-active {
  transition: all 0.3s ease;
}
.slide-down-enter-from,
.slide-down-leave-to {
  opacity: 0;
  transform: translateY(-10px);
}

/* 下拉菜单淡入淡出 */
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.15s ease;
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}

/* 移动对话框 */
.drive-move-dialog {
  max-width: 360px;
}
.drive-move-options {
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: 300px;
  overflow-y: auto;
}
.drive-move-option {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  border: 1px solid var(--color-border);
  background: var(--color-bg);
  color: var(--color-text-secondary);
  border-radius: var(--radius-md);
  cursor: pointer;
  font-size: 13px;
  transition: all 0.12s ease;
  text-align: left;
  width: 100%;
}
.drive-move-option:hover {
  background: var(--color-bg-hover);
  color: var(--color-text);
}
.drive-move-option.active {
  background: var(--color-accent-subtle);
  color: var(--color-accent);
  border-color: var(--color-accent);
}

/* 快捷访问文件选择器模态框 */
.qa-picker-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1100;
  backdrop-filter: blur(2px);
}

.qa-picker-dialog {
  background: var(--color-bg-raised);
  border-radius: var(--radius-lg);
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.2);
  width: 90%;
  max-width: 480px;
  max-height: 80vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.qa-picker-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 20px;
  border-bottom: 1px solid var(--color-border);
  flex-shrink: 0;
}

.qa-picker-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--color-text);
}

.qa-picker-close {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: none;
  background: transparent;
  color: var(--color-text-tertiary);
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: all 0.12s ease;
}

.qa-picker-close:hover {
  background: var(--color-bg-hover);
  color: var(--color-text);
}

.qa-picker-breadcrumb {
  display: flex;
  align-items: center;
  gap: 2px;
  padding: 10px 20px;
  border-bottom: 1px solid var(--color-border-subtle);
  flex-shrink: 0;
  overflow-x: auto;
}

.qa-picker-bc-btn {
  flex-shrink: 0;
  padding: 4px 8px;
  border: none;
  background: transparent;
  color: var(--color-text-secondary);
  font-size: 13px;
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: all 0.1s ease;
  white-space: nowrap;
}

.qa-picker-bc-btn:hover {
  background: var(--color-bg-hover);
  color: var(--color-text);
}

.qa-picker-bc-btn.active {
  color: var(--color-text);
  font-weight: 500;
  cursor: default;
}

.qa-picker-bc-sep {
  color: var(--color-text-tertiary);
  font-size: 13px;
  flex-shrink: 0;
}

.qa-picker-body {
  flex: 1;
  overflow-y: auto;
  min-height: 200px;
  max-height: 350px;
}

.qa-picker-loading,
.qa-picker-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 200px;
  color: var(--color-text-tertiary);
  font-size: 14px;
}

.qa-picker-list {
  display: flex;
  flex-direction: column;
  padding: 4px 0;
}

.qa-picker-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 20px;
  cursor: pointer;
  transition: background 0.1s ease;
  border-left: 3px solid transparent;
}

.qa-picker-item:hover {
  background: var(--color-bg-hover);
}

.qa-picker-item.is-selected {
  background: var(--color-accent-subtle);
  border-left-color: var(--color-accent);
}

.qa-picker-item-icon {
  width: 24px;
  height: 24px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
}

.qa-picker-item-icon :deep(svg) {
  width: 20px;
  height: 20px;
}

.qa-picker-item-name {
  flex: 1;
  font-size: 14px;
  color: var(--color-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.qa-picker-item-radio {
  width: 18px;
  height: 18px;
  border: 2px solid var(--color-border);
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  transition: border-color 0.12s ease;
}

.qa-picker-item-radio.checked {
  border-color: var(--color-accent);
}

.qa-picker-item-radio-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--color-accent);
}

.qa-picker-footer {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  padding: 12px 20px;
  border-top: 1px solid var(--color-border);
  flex-shrink: 0;
}

.qa-picker-btn {
  padding: 8px 18px;
  border: none;
  border-radius: var(--radius-sm);
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.12s ease;
}

.qa-picker-btn-cancel {
  background: var(--color-bg-subtle);
  color: var(--color-text-secondary);
  border: 1px solid var(--color-border);
}

.qa-picker-btn-cancel:hover {
  background: var(--color-bg-hover);
  color: var(--color-text);
}

.qa-picker-btn-confirm {
  background: var(--color-accent);
  color: #fff;
}

.qa-picker-btn-confirm:hover:not(:disabled) {
  opacity: 0.9;
}

.qa-picker-btn-confirm:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>