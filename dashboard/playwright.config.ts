import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./e2e",
  webServer:
    process.env.TEST_START_SERVER === "1"
      ? {
          command:
            "cp -R .next/static .next/standalone/.next/ && PORT=3017 HOSTNAME=127.0.0.1 node .next/standalone/server.js",
          url: "http://127.0.0.1:3017/login",
          reuseExistingServer: false,
        }
      : undefined,
  use: {
    baseURL: process.env.TEST_DASHBOARD_URL || "http://127.0.0.1:3000",
    channel: process.env.CI ? "chrome" : undefined,
    launchOptions: { chromiumSandbox: true },
  },
  workers: 1,
});
