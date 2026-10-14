import { pathToFileURL } from "node:url";
import { betterAuth } from "better-auth";
import { testUtils } from "better-auth/plugins";
import { getServerEnv } from "../../lib/env.ts";

export function assertTestEnvironment() {
  const env = getServerEnv();
  const origin = new URL(env.publicOrigin);
  if (!process.env.APP_DATABASE_URL)
    throw new Error("A dedicated test app database is required");
  const databases = [
    new URL(env.authDatabaseUrl),
    new URL(process.env.APP_DATABASE_URL),
  ];
  const credentials = [
    env.betterAuthSecret,
    env.googleClientId,
    env.googleClientSecret,
    env.githubClientId,
    env.githubClientSecret,
  ];
  for (const database of databases) {
    const socket = database.searchParams.get("host");
    if (
      database.pathname !== "/diffuzion_test" ||
      database.username !== "diffuzion_test"
        || !["test", "diffuzion-test-password"].includes(database.password) ||
      !["localhost", "127.0.0.1", "[::1]"].includes(database.hostname) ||
      (socket !== null && !socket.startsWith("/tmp/diffuzion-"))
    ) {
      throw new Error(
        "Test helpers require an isolated diffuzion_test database and role",
      );
    }
  }
  if (
    process.env.NODE_ENV !== "test" ||
    process.env.DIFFUZION_TEST_MODE !== "1" ||
    origin.protocol !== "http:" ||
    !["localhost", "127.0.0.1"].includes(origin.hostname) ||
    !origin.port ||
    Number(origin.port) < 1024 ||
    credentials.some((value) => !value.startsWith("diffuzion-test-")) ||
    databases[0].hostname !== databases[1].hostname ||
    databases[0].port !== databases[1].port ||
    databases[0].searchParams.get("host") !==
      databases[1].searchParams.get("host")
  ) {
    throw new Error(
      "Test helpers refuse non-test origins, credentials or database bindings",
    );
  }
  return env;
}

export async function createTestAuth() {
  const env = assertTestEnvironment();
  const production = await import("../../lib/auth.ts");
  const options = production.createAuthOptions(env);
  const pool = options.database;
  try {
    const identity = await pool.query<{ database: string; role: string }>(
      "SELECT current_database() AS database, current_user AS role",
    );
    if (
      identity.rows[0]?.database !== "diffuzion_test" ||
      identity.rows[0]?.role !== "diffuzion_test"
    ) {
      throw new Error(
        "Connected database identity is not the isolated test database",
      );
    }
    const migration = await pool.query<{ version_num: string }>(
      "SELECT version_num FROM app.alembic_version",
    );
    if (migration.rows[0]?.version_num !== "0001_initial_app") {
      throw new Error(
        "Apply the initial app migration before creating test sessions",
      );
    }
    const auth = betterAuth({ ...options, plugins: [testUtils()] });
    return {
      auth,
      pool,
      async close() {
        await pool.end();
        if (!production.auth.options.database.ended) await production.auth.options.database.end();
      },
    };
  } catch (error) {
    await pool.end();
        if (!production.auth.options.database.ended) await production.auth.options.database.end();
    throw error;
  }
}

export async function createTestSession(role: "user" | "admin" = "user") {
  const runtime = await createTestAuth();
  try {
    const { test } = await runtime.auth.$context;
    const user = await test.saveUser(
      test.createUser({ name: "Diffuzion Test User", emailVerified: true }),
    );
    try {
      await runtime.pool.query(
        "INSERT INTO app.app_accounts (user_id, role) VALUES ($1, $2)",
        [user.id, role],
      );
      const login = await test.login({ userId: user.id });
      return {
        userId: user.id,
        sessionId: login.session.id,
        cookie: login.headers.get("cookie"),
        cookies: login.cookies,
      };
    } catch (error) {
      await runtime.pool.query(
        "DELETE FROM app.app_accounts WHERE user_id = $1",
        [user.id],
      );
      await test.deleteUser(user.id);
      throw error;
    }
  } finally {
    await runtime.close();
  }
}

export async function revokeTestSession(sessionId: string) {
  if (!/^[A-Za-z0-9_-]{1,256}$/.test(sessionId))
    throw new Error("Invalid test session ID");
  const runtime = await createTestAuth();
  try {
    await runtime.pool.query("DELETE FROM auth.session WHERE id = $1", [
      sessionId,
    ]);
  } finally {
    await runtime.close();
  }
}

export async function deleteTestUser(userId: string) {
  if (!/^[A-Za-z0-9_-]{1,256}$/.test(userId))
    throw new Error("Invalid test user ID");
  const runtime = await createTestAuth();
  try {
    await runtime.pool.query(
      "DELETE FROM app.admin_audit WHERE actor_user_id = $1",
      [userId],
    );
    await runtime.pool.query(
      "DELETE FROM app.app_accounts WHERE user_id = $1",
      [userId],
    );
    const { test } = await runtime.auth.$context;
    await test.deleteUser(userId);
  } finally {
    await runtime.close();
  }
}

async function main() {
  const [command, argument] = process.argv.slice(2);
  if (command === "create" && (argument === "user" || argument === "admin")) {
    process.stdout.write(JSON.stringify(await createTestSession(argument)));
  } else if (command === "revoke" && argument) {
    await revokeTestSession(argument);
  } else if (command === "delete" && argument) {
    await deleteTestUser(argument);
  } else {
    throw new Error(
      "Use create user|admin, revoke SESSION_ID or delete USER_ID",
    );
  }
}

if (
  process.argv[1] &&
  import.meta.url === pathToFileURL(process.argv[1]).href
) {
  main().catch(() => {
    process.stderr.write("Test session command failed\n");
    process.exitCode = 1;
  });
}
