/// <reference types="vitest" />
import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

// NovelOS 前端（Sprint 5 一期）
// - 开发代理：/api → http://127.0.0.1:<后端端口>；端口优先级
//   NOVELOS_PORT > NOVELOS_API_PORT > 18081，与 packages/core/config.py:Settings 一致
// - 测试：vitest + jsdom
export default defineConfig(({ mode }) => {
  // V3.9：此前 target 硬编码 18081，README 声称「从 NOVELOS_PORT / NOVELOS_API_PORT 读」
  // 不成立。``loadEnv`` 前缀传 '' 时把 .env* 文件与进程环境变量一并读入（vite dev
  // 启动时的 shell 环境变量同样生效）。
  const env = loadEnv(mode, process.cwd(), '');
  const apiPort = env.NOVELOS_PORT || env.NOVELOS_API_PORT || '18081';

  return {
    plugins: [react()],
    server: {
      host: '127.0.0.1',
      port: 5174,
      strictPort: true,
      proxy: {
        '/api': {
          target: `http://127.0.0.1:${apiPort}`,
          changeOrigin: true,
        },
      },
    },
    test: {
      environment: 'jsdom',
      globals: true,
      setupFiles: ['./src/test/setup.ts'],
      include: ['src/**/*.test.{ts,tsx}'],
    },
  };
});
