import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
//
// V2.0 Wave C 任务三：端口统一收敛。后端端口从 ``NOVELOS_PORT`` / ``NOVELOS_API_PORT`` 读，
// 默认 18081（与 packages/core/config.py Settings.api_port 对齐）；旧 8000 改为兼容值。
const NOVELOS_BACKEND_PORT = process.env.NOVELOS_PORT
  || process.env.NOVELOS_API_PORT
  || '18081';

export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': {
        target: `http://127.0.0.1:${NOVELOS_BACKEND_PORT}`,
        changeOrigin: true,
      },
    },
  },
})