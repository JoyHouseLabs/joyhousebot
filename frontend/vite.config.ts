import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import Components from 'unplugin-vue-components/vite'
import { NaiveUiResolver } from 'unplugin-vue-components/resolvers'
import { resolve } from 'path'

export default defineConfig({
  plugins: [
    vue(),
    Components({
      resolvers: [NaiveUiResolver()],
    }),
  ],
  base: '/ui/',
  resolve: {
    alias: {
      '@': resolve(__dirname, 'src'),
    },
  },
  server: {
    port: 5178,
    host: '0.0.0.0',
    proxy: {
      '/api': { target: 'http://127.0.0.1:18790', changeOrigin: true },
      '/plugins-apps': { target: 'http://127.0.0.1:18790', changeOrigin: true },
      '/ws': { target: 'http://127.0.0.1:18790', changeOrigin: true, ws: true },
    },
  },
})
