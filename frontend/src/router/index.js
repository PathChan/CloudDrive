import { createRouter, createWebHistory } from 'vue-router'
import CloudDrivePage from '@/components/CloudDrivePage.vue'
import LoginPage from '@/components/LoginPage.vue'
import RegisterPage from '@/components/RegisterPage.vue'

const router = createRouter({
  history: createWebHistory('/'),
  routes: [
    {
      path: '/login',
      name: 'login',
      component: LoginPage,
    },
    {
      path: '/register',
      name: 'register',
      component: RegisterPage,
    },
    {
      path: '/',
      name: 'home',
      component: CloudDrivePage,
      meta: { requiresAuth: true },
    },
  ],
})

router.beforeEach((to) => {
  // 处理 SSO 登录回调携带的 token
  if (to.query.token) {
    localStorage.setItem('token', to.query.token)
    // 清除 URL 中的 token 参数
    return { path: to.path, query: {} }
  }

  const token = localStorage.getItem('token')
  if (to.meta.requiresAuth && !token) {
    return '/login'
  }
  if ((to.path === '/login' || to.path === '/register') && token) {
    return '/'
  }
})

export default router