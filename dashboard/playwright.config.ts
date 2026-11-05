import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./e2e",
  use: {
    baseURL: process.env.TEST_DASHBOARD_URL || "http://127.0.0.1:3000",
    launchOptions: { chromiumSandbox: true },
  },
  workers: 1,
});
