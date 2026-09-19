import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
export default defineConfig({
  plugins: [
    react(),
    {
      name: "development-csp",
      transformIndexHtml(html, ctx) {
        return ctx.server
          ? html.replace(
              "script-src 'self'",
              "script-src 'self' 'unsafe-inline'",
            )
          : html;
      },
    },
  ],
  base: "./",
  server: { port: 5178, strictPort: true },
});
