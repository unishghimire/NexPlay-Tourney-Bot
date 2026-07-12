import { defineConfig } from 'vite'
import base44 from '@base44/vite-plugin'

export default defineConfig({
  plugins: [base44()],
})