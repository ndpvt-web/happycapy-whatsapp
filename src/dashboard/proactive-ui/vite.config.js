import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  base: '/proactive/',
  server: {
    proxy: {
      '/api': 'http://localhost:3456',
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
})
