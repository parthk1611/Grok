/// <reference types="vitest/config" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'node:path';

// The contract schemas and examples live in ../contracts (read-only for BEACON).
// They are imported by relative path, so Vite must be allowed to read the repo root.
const repoRoot = path.resolve(import.meta.dirname, '..');

export default defineConfig({
  plugins: [react()],
  server: { fs: { allow: [repoRoot] } },
  build: { chunkSizeWarningLimit: 1500 },
  test: {
    environment: 'node',
    globals: true,
    setupFiles: ['./src/test-setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
  },
});
