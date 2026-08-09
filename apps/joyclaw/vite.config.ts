import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'path'

export default defineConfig({
  plugins: [vue()],
  base: '/joy/',
  resolve: {
    alias: {
      '@': resolve(import.meta.dirname, 'src'),
    },
  },
  server: {
    host: '0.0.0.0',
    port: 5179,
    proxy: {
      '/v1': { target: 'http://127.0.0.1:18790', changeOrigin: true },
      '/healthz': { target: 'http://127.0.0.1:18790', changeOrigin: true },
      '/readyz': { target: 'http://127.0.0.1:18790', changeOrigin: true },
    },
  },
})
