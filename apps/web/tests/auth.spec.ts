import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { expect, test } from "@playwright/test";

const helper = fileURLToPath(new URL("./support/test-sessions.ts", import.meta.url));

function sessionCommand(command: string, argument: string) {
  return execFileSync(process.execPath, ["--conditions=react-server", helper, command, argument], {
    env: { ...process.env, NODE_ENV: "test" }, encoding: "utf8", timeout: 30000,
  });
}

test("test-only session helper refuses production mode", () => {
  expect(() => execFileSync(process.execPath, ["--conditions=react-server", helper, "create", "admin"], {
    env: { ...process.env, NODE_ENV: "production" }, stdio: "pipe", timeout: 10000,
  })).toThrow();
});

test("callback URLs remain local", () => {
  const source = `import assert from 'node:assert/strict';
    import { safeCallbackURL } from './lib/auth-client.ts';
    for (const value of ['https://evil.invalid', '//evil.invalid', '/\\\\evil.invalid', '/\\nunsafe', null]) {
      assert.equal(safeCallbackURL(value), '/dashboard');
    }
    assert.equal(safeCallbackURL('/projects/demo?tab=preview#details'), '/projects/demo?tab=preview#details');`;
  const environment = { ...process.env };
  delete environment.NODE_OPTIONS;
  execFileSync(process.execPath, ["--input-type=module", "-e", source], {
    cwd: fileURLToPath(new URL("..", import.meta.url)), env: environment, stdio: "pipe", timeout: 10000,
  });
});

test.describe("mounted Better Auth handler", () => {
  test.skip(process.env.DIFFUZION_TEST_MODE !== "1", "Requires the isolated migrated test database");

  test("real session resolves and sign-out revokes it", async ({ request }) => {
    const session = JSON.parse(sessionCommand("create", "user")) as { cookie: string; userId: string; sessionId: string };
    try {
      const current = await request.get("/api/auth/get-session?disableCookieCache=true", { headers: { Cookie: session.cookie } });
      expect(current.ok()).toBeTruthy();
      expect((await current.json()).user.id).toBe(session.userId);
      const logout = await request.post("/api/auth/sign-out", {
        headers: { Cookie: session.cookie, Origin: process.env.PUBLIC_ORIGIN! }, data: {},
      });
      expect(logout.ok()).toBeTruthy();
      const revoked = await request.get("/api/auth/get-session?disableCookieCache=true", { headers: { Cookie: session.cookie } });
      expect(await revoked.json()).toBeNull();
    } finally {
      sessionCommand("revoke", session.sessionId);
      sessionCommand("delete", session.userId);
    }
  });

  for (const provider of ["google", "github"] as const) {
    test(`${provider} uses the configured OAuth adapter and state protection`, async ({ request }) => {
      const response = await request.post("/api/auth/sign-in/social", {
        headers: { Origin: process.env.PUBLIC_ORIGIN! }, data: { provider, callbackURL: "/dashboard" },
      });
      expect(response.ok()).toBeTruthy();
      const result = await response.json();
      const url = new URL(result.url);
      expect(url.hostname).toBe(provider === "google" ? "accounts.google.com" : "github.com");
      expect(url.searchParams.get("state")).toBeTruthy();
      expect(url.searchParams.get("redirect_uri")).toBe(`${process.env.PUBLIC_ORIGIN}/api/auth/callback/${provider}`);
      if (provider === "github") expect(url.searchParams.get("scope")).not.toMatch(/\brepo\b/);
    });
  }

  test("untrusted cookie-bearing auth mutation origin is rejected", async ({ request }) => {
    const response = await request.post("/api/auth/sign-in/social", {
      headers: { Origin: "https://evil.invalid", Cookie: "better-auth.session_token=invalid" }, data: { provider: "google", callbackURL: "/dashboard" },
    });
    expect(response.status()).toBe(403);
  });
  test("server rejects an external callback URL", async ({ request }) => {
    const response = await request.post("/api/auth/sign-in/social", {
      headers: { Origin: process.env.PUBLIC_ORIGIN! }, data: { provider: "google", callbackURL: "https://evil.invalid" },
    });
    expect(response.status()).toBe(403);
  });

});
