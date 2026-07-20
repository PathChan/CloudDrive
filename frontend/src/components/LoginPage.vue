<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '@/services/api'
import { Eye, EyeOff } from 'lucide-vue-next'

const router = useRouter()

const email = ref('')
const password = ref('')
const loading = ref(false)
const errorMsg = ref('')
const showPassword = ref(false)

onMounted(() => {
  if (localStorage.getItem('token')) {
    router.replace('/')
  }
})

async function handleLogin() {
  errorMsg.value = ''
  if (!email.value || !password.value) {
    errorMsg.value = '请输入邮箱和密码'
    return
  }
  loading.value = true
  try {
    const data = await api.auth.login({ email: email.value, password: password.value })
    localStorage.setItem('token', data.token)
    localStorage.setItem('username', data.user?.username || '')
    localStorage.setItem('user', JSON.stringify(data.user || {}))
    router.replace('/')
  } catch (error) {
    errorMsg.value = error.message || '登录失败'
  } finally {
    loading.value = false
  }
}

// function microsoftLogin() {
//   window.location.href = '/api/auth/microsoft/login'
// }

// LDAP 登录
const showLdap = ref(false)
const ldapUsername = ref('')
const ldapPassword = ref('')
const ldapLoading = ref(false)
const ldapError = ref('')

async function ldapLogin() {
  ldapError.value = ''
  if (!ldapUsername.value || !ldapPassword.value) {
    ldapError.value = '请输入用户名和密码'
    return
  }
  ldapLoading.value = true
  try {
    const data = await api.auth.ldapLogin({
      username: ldapUsername.value,
      password: ldapPassword.value
    })
    localStorage.setItem('token', data.token)
    localStorage.setItem('username', data.user?.username || '')
    localStorage.setItem('user', JSON.stringify(data.user || {}))
    router.replace('/')
  } catch (error) {
    ldapError.value = error.message || 'LDAP 登录失败'
  } finally {
    ldapLoading.value = false
  }
}
</script>

<template>
  <div class="auth-page">
    <div class="auth-card">
      <div class="auth-header">
        <h1 class="auth-logo">iFollow</h1>
        <p class="auth-subtitle">登录你的账号</p>
      </div>

      <div class="auth-body">
        <div class="form-group">
          <label class="form-label">邮箱</label>
          <input
            v-model="email"
            type="email"
            placeholder="输入邮箱"
            class="form-input"
            @keydown.enter="handleLogin"
            autofocus
          />
        </div>

        <div class="form-group">
          <label class="form-label">密码</label>
          <div class="form-password-wrap">
            <input
              v-model="password"
              :type="showPassword ? 'text' : 'password'"
              placeholder="输入密码"
              class="form-input"
              @keydown.enter="handleLogin"
            />
            <button
              type="button"
              class="form-password-toggle"
              @click="showPassword = !showPassword"
            >
              <EyeOff v-if="showPassword" :size="16" />
              <Eye v-else :size="16" />
            </button>
          </div>
        </div>

        <p v-if="errorMsg" class="form-error">{{ errorMsg }}</p>

        <button @click="handleLogin" :disabled="loading" class="form-submit-btn">
          {{ loading ? '登录中...' : '登录' }}
        </button>

        <!--
        <div class="sso-divider">
          <span>或</span>
        </div>

        <button @click="microsoftLogin" class="microsoft-btn">
          <svg class="microsoft-icon" viewBox="0 0 21 21" width="18" height="18">
            <rect x="1" y="1" width="9" height="9" fill="#f25022"/>
            <rect x="11" y="1" width="9" height="9" fill="#7fba00"/>
            <rect x="1" y="11" width="9" height="9" fill="#00a4ef"/>
            <rect x="11" y="11" width="9" height="9" fill="#ffb900"/>
          </svg>
          使用 Microsoft 账号登录
        </button>
        -->

        <button @click="showLdap = true" class="ldap-btn">
          使用 LDAP 域账号登录
        </button>

        <!-- LDAP 弹窗 -->
        <div v-if="showLdap" class="ldap-overlay" @click.self="showLdap = false">
          <div class="ldap-dialog">
            <h3 class="ldap-title">LDAP 域账号登录</h3>
            <div class="form-group">
              <label class="form-label">用户名</label>
              <input v-model="ldapUsername" placeholder="输入域账号" class="form-input" autofocus />
            </div>
            <div class="form-group">
              <label class="form-label">密码</label>
              <input v-model="ldapPassword" type="password" placeholder="域密码" class="form-input" @keydown.enter="ldapLogin" />
            </div>
            <p v-if="ldapError" class="form-error">{{ ldapError }}</p>
            <div class="ldap-actions">
              <button @click="showLdap = false" class="ldap-cancel-btn">取消</button>
              <button @click="ldapLogin" :disabled="ldapLoading" class="ldap-submit-btn">
                {{ ldapLoading ? '登录中...' : '登录' }}
              </button>
            </div>
          </div>
        </div>

        <p class="form-switch-text">
          没有账号？
          <router-link to="/register" class="form-switch-link">去注册</router-link>
        </p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.auth-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--color-bg);
  padding: 20px;
}

.auth-card {
  width: 100%;
  max-width: 400px;
  background: var(--color-bg-raised);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-xl);
  box-shadow: 0 4px 24px rgba(0, 0, 0, 0.06);
  overflow: hidden;
}

.auth-header {
  padding: 32px 28px 20px;
  text-align: center;
}

.auth-logo {
  font-size: 28px;
  font-weight: 700;
  color: var(--color-accent);
  margin: 0;
}

.auth-subtitle {
  margin: 8px 0 0;
  font-size: 14px;
  color: var(--color-text-tertiary);
}

.auth-body {
  padding: 0 28px 28px;
}

.form-group {
  margin-bottom: 16px;
}

.form-label {
  display: block;
  font-size: 13px;
  font-weight: 500;
  color: var(--color-text-secondary);
  margin-bottom: 6px;
}

.form-input {
  width: 100%;
  padding: 10px 14px;
  border: 1px solid var(--color-border);
  background: var(--color-bg);
  color: var(--color-text);
  border-radius: var(--radius-md);
  font-size: 14px;
  outline: none;
  transition: border-color 0.15s ease;
  box-sizing: border-box;
}

.form-input:focus {
  border-color: var(--color-accent);
  box-shadow: 0 0 0 3px var(--color-accent-subtle);
}

.form-password-wrap {
  position: relative;
}

.form-password-wrap .form-input {
  padding-right: 42px;
}

.form-password-toggle {
  position: absolute;
  right: 8px;
  top: 50%;
  transform: translateY(-50%);
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border: none;
  background: transparent;
  color: var(--color-text-tertiary);
  cursor: pointer;
  border-radius: var(--radius-sm);
}

.form-password-toggle:hover {
  color: var(--color-text);
}

.form-error {
  margin: 0 0 12px;
  padding: 8px 12px;
  background: var(--color-danger-subtle);
  color: var(--color-danger);
  border-radius: var(--radius-md);
  font-size: 13px;
}

.form-submit-btn {
  width: 100%;
  padding: 11px 0;
  border: none;
  background: var(--color-accent);
  color: #fff;
  font-size: 15px;
  font-weight: 600;
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: opacity 0.15s ease;
}

.form-submit-btn:hover {
  opacity: 0.9;
}

.form-submit-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.form-switch-text {
  margin: 16px 0 0;
  text-align: center;
  font-size: 13px;
  color: var(--color-text-tertiary);
}

.form-switch-link {
  color: var(--color-accent);
  text-decoration: none;
  font-weight: 500;
}

.form-switch-link:hover {
  text-decoration: underline;
}

.sso-divider {
  display: flex;
  align-items: center;
  margin: 16px 0;
  gap: 12px;
}

.sso-divider::before,
.sso-divider::after {
  content: '';
  flex: 1;
  height: 1px;
  background: var(--color-border);
}

.sso-divider span {
  font-size: 12px;
  color: var(--color-text-tertiary);
  white-space: nowrap;
}

.microsoft-btn {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 10px 0;
  border: 1px solid var(--color-border);
  background: var(--color-bg-raised);
  color: var(--color-text);
  font-size: 14px;
  font-weight: 500;
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: background 0.15s ease, border-color 0.15s ease;
}

.microsoft-btn:hover {
  background: var(--color-bg);
  border-color: var(--color-text-tertiary);
}

.microsoft-icon {
  flex-shrink: 0;
}

.ldap-btn {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 10px 0;
  border: 1px solid var(--color-border);
  background: var(--color-bg-raised);
  color: var(--color-text);
  font-size: 14px;
  font-weight: 500;
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: background 0.15s ease, border-color 0.15s ease;
  margin-top: 10px;
}

.ldap-btn:hover {
  background: var(--color-bg);
  border-color: var(--color-text-tertiary);
}

.ldap-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,0.4);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 100;
}

.ldap-dialog {
  background: var(--color-bg-raised);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-xl);
  padding: 28px;
  width: 360px;
  max-width: 90vw;
  box-shadow: 0 8px 32px rgba(0,0,0,0.12);
}

.ldap-title {
  margin: 0 0 20px;
  font-size: 18px;
  font-weight: 600;
  color: var(--color-text);
  text-align: center;
}

.ldap-actions {
  display: flex;
  gap: 10px;
  margin-top: 6px;
}

.ldap-cancel-btn, .ldap-submit-btn {
  flex: 1;
  padding: 10px 0;
  border: none;
  border-radius: var(--radius-md);
  font-size: 14px;
  cursor: pointer;
  transition: opacity 0.15s;
}

.ldap-cancel-btn {
  background: var(--color-bg);
  color: var(--color-text);
  border: 1px solid var(--color-border);
}

.ldap-submit-btn {
  background: var(--color-accent);
  color: #fff;
}

.ldap-submit-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
</style>