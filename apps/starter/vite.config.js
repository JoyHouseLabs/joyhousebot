import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    host: '127.0.0.1',
    port: 5179,
    strictPort: true,
    proxy: {
      '/v2': 'http://127.0.0.1:18790',
      '/control': 'http://127.0.0.1:18790',
      '/healthz': 'http://127.0.0.1:18790',
      '/readyz': 'http://127.0.0.1:18790',
    },
  },
})
