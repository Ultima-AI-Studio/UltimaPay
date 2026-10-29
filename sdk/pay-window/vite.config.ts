import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 5178,
    proxy: {
      // Ultima Pay 服务端（聚合支付 API）
      '/api/pay': {
        target: 'http://127.0.0.1:8061',
        changeOrigin: true,
      },
    },
  },
});
