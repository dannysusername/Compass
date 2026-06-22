import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Vite config for Compass's incremental React migration.
//
// Compass is a FastAPI + Jinja app, so React does NOT own the page — it is
// mounted into a <div> that the server-rendered template provides. This config
// therefore runs in "backend integration" mode:
//   - dev:   `npm run dev` starts a hot-reload server on :5173. The Jinja
//            template loads the entry module straight from that server.
//   - build: `npm run build` compiles to ../static/dist so FastAPI can serve
//            the files like any other static asset (no Node needed in prod).
export default defineConfig({
  // Activates JSX compilation + React Fast Refresh (state-preserving HMR).
  plugins: [react()],

  build: {
    // Drop compiled output into the existing Python-served static/ folder.
    outDir: "../static/dist",
    emptyOutDir: true,
    // Write static/dist/.vite/manifest.json mapping source -> hashed filename,
    // which main.py reads to emit the right <script> in production.
    manifest: true,
    rollupOptions: {
      // One entry per migrated page. main.py resolves each via the manifest
      // (vite_asset('src/<page>/main.jsx')).
      input: {
        week: "src/week/main.jsx",
        today: "src/today/main.jsx",
      },
    },
  },

  server: {
    // The page HTML is served by FastAPI (:8000) while modules are served by
    // Vite (:5173). An explicit origin makes Vite emit absolute asset URLs so
    // the two cooperate during development.
    origin: "http://localhost:5173",
  },
});
