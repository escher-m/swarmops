import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { resolve } from 'node:path'

/* Builds the React islands into dashboard/static/islands/.
 *
 * Deliberately NOT an SPA. The Jinja templates keep routing, authentication and
 * the page shell; this bundle is loaded by pages that mount a specific island
 * and by nothing else. Output filenames are fixed rather than hashed so the
 * templates can reference them without a manifest lookup on every render.
 *
 * Everything is bundled and served from our own origin, so the dashboard's
 * no-third-party-requests property survives adding React. */
export default defineConfig({
  plugins: [react(), tailwindcss()],
  /* Vite's library mode does not substitute process.env.NODE_ENV inside
   * dependencies, so React ships its DEVELOPMENT build -- warning strings,
   * devtools hooks and all. That alone was most of a megabyte. Defining it
   * explicitly is the documented fix and is easy to miss because the build
   * succeeds either way; only the size gives it away. */
  define: {
    'process.env.NODE_ENV': JSON.stringify('production'),
  },
  build: {
    outDir: resolve(import.meta.dirname, '../static/islands'),
    emptyOutDir: true,
    lib: {
      entry: resolve(import.meta.dirname, 'src/main.jsx'),
      name: 'SwarmOpsIslands',
      formats: ['es'],
      fileName: () => 'islands.js',
    },
    rollupOptions: {
      output: { assetFileNames: 'islands.[ext]' },
    },
    target: 'es2020',
    sourcemap: false,
  },
})
