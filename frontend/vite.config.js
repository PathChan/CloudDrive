import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [vue(), tailwindcss()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url))
    }
  },
  server: {
    port: 5174,
    proxy: {
      '/api/cloud-drive': {
        target: 'http://localhost:5000',
        changeOrigin: true
      },
      '/api/auth': {
        target: 'http://localhost:5000',
        changeOrigin: true
      }
    },
    hmr: {
      clientPort: 5174,
      host: 'localhost',
      overlay: true
    }
  }
})
