import { defineConfig } from "vite";
import { viteSingleFile } from "vite-plugin-singlefile";

// 打包成單一 HTML：MCP App 在沙箱 iframe 內執行，外部資產載不進來
export default defineConfig({
  plugins: [viteSingleFile()],
  build: {
    rollupOptions: { input: "calendar.html" },
    outDir: "dist",
    emptyOutDir: true,
  },
});
