import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The API runs separately (uvicorn api.app:app --port 8000). Proxying keeps the
// browser same-origin, so no CORS in dev and no API base URL in the client.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ""),
      },
    },
  },
});
