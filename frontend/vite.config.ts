import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    setupFiles: './src/tests/setup.ts',
    css: true,
    // Bundle the real UI library once instead of loading its deep module tree
    // separately in every jsdom worker on Windows.
    deps: { optimizer: { client: { enabled: true, include: ['antd'] } } },
    // Windows cold-start I/O can exhaust Vitest's worker startup deadline when
    // every jsdom environment starts together. Reuse one worker there.
    maxWorkers: process.platform === 'win32' ? 1 : undefined,
    pool: process.platform === 'win32' ? 'vmThreads' : 'forks',
  },
})
