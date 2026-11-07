import { test, expect } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { readFileSync, readlinkSync } from "node:fs";

test("Chromium renderers run inside the operating-system sandbox", async ({
  browser,
  page,
}) => {
  await page.goto("data:text/html,<title>Sandbox verification</title>");
  await expect(page).toHaveTitle("Sandbox verification");
  const session = await browser.newBrowserCDPSession();
  const { processInfo } = await session.send("SystemInfo.getProcessInfo");
  const renderers = processInfo.filter((p) => p.type === "renderer");
  expect(renderers.length).toBeGreaterThan(0);
  if (process.platform === "linux") {
    const main = processInfo.find((p) => p.type === "browser");
    expect(main).toBeDefined();
    for (const process of processInfo) {
      const args = readFileSync(`/proc/${process.id}/cmdline`, "utf8");
      expect(args).not.toContain("--no-sandbox");
      expect(args).not.toContain("--disable-setuid-sandbox");
    }
    for (const renderer of renderers) {
      const status = readFileSync(`/proc/${renderer.id}/status`, "utf8");
      expect(status).toMatch(/^NoNewPrivs:\s+1$/m);
      expect(status).toMatch(/^Seccomp:\s+2$/m);
      expect(readlinkSync(`/proc/${renderer.id}/ns/pid`)).not.toBe(
        readlinkSync(`/proc/${main!.id}/ns/pid`),
      );
    }
  }
  if (process.platform === "darwin") {
    for (const process of processInfo) {
      const args = execFileSync(
        "/bin/ps",
        ["-p", String(process.id), "-o", "args="],
        { encoding: "utf8" },
      );
      expect(args).not.toContain("--no-sandbox");
      expect(args).not.toContain("--disable-setuid-sandbox");
    }
    for (const renderer of renderers) {
      const result = execFileSync(
        "/usr/bin/python3",
        [
          "-c",
          "import ctypes,sys; f=ctypes.CDLL('/usr/lib/libsandbox.dylib').sandbox_check; f.restype=ctypes.c_int; print(f(int(sys.argv[1]),None,0))",
          String(renderer.id),
        ],
        { encoding: "utf8" },
      );
      expect(result.trim()).toBe("1");
    }
  }
});
