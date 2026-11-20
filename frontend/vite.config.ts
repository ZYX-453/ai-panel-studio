import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 开发期把 /api 代理到后端，前端代码里不写死后端地址，
// 这样打包产物可以挂在任意域名下（需求要求端口可自定义）。
export default defineConfig({
  plugins: [react()],
  server: {
    // 显式绑 IPv4：默认只监听 ::1，浏览器写 127.0.0.1 会连不上，
    // 而文档、CORS 白名单、截图脚本里用的都是 127.0.0.1
    host: '127.0.0.1',
    port: 5173,
    // 端口被占就直接报错，不要自动跳到 5174：
    // 文档、后端 CORS 白名单、截图脚本里写的都是 5173，
    // 悄悄换端口会让人对着一个"看起来一样但其实是旧实例"的页面排查半天。
    strictPort: true,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    // 首屏要求 2s 内 FCP，把依赖拆开避免单个 chunk 过大
    rollupOptions: {
      output: {
        manualChunks: {
          react: ['react', 'react-dom'],
        },
      },
    },
  },
})
