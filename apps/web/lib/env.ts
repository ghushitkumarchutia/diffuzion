import "server-only";

function required(name: string): string {
  const value = process.env[name];
  if (!value || value === "replace-me") {
    throw new Error(`Missing required server configuration: ${name}`);
  }
  return value;
}

function parseUrl(name: string, value: string): URL {
  try {
    return new URL(value);
  } catch {
    throw new Error(`${name} must be a valid URL`);
  }
}

function origin(value: string): string {
  const url = parseUrl("PUBLIC_ORIGIN", value);
  if (
    !["http:", "https:"].includes(url.protocol) ||
    url.origin !== value ||
    url.username ||
    url.password ||
    (url.protocol === "http:" &&
      !["localhost", "127.0.0.1", "[::1]"].includes(url.hostname))
  ) {
    throw new Error(
      "PUBLIC_ORIGIN must be an exact HTTPS origin or local HTTP origin",
    );
  }
  return value;
}

export function getServerEnv() {
  const databaseUrl = required("AUTH_DATABASE_URL");
  const database = parseUrl("AUTH_DATABASE_URL", databaseUrl);
  if (database.protocol !== "postgresql:" || !database.hostname) {
    throw new Error("AUTH_DATABASE_URL must be a PostgreSQL connection URL");
  }
  if (process.env.AUTH_SCHEMA && process.env.AUTH_SCHEMA !== "auth") {
    throw new Error("AUTH_SCHEMA must be auth");
  }
  if (database.searchParams.get("options") !== "-csearch_path=auth") {
    throw new Error("AUTH_DATABASE_URL must explicitly select the auth schema");
  }
  const secret = required("BETTER_AUTH_SECRET");
  if (secret.length < 32) {
    throw new Error("BETTER_AUTH_SECRET must contain at least 32 characters");
  }
  return {
    publicOrigin: origin(required("PUBLIC_ORIGIN")),
    authDatabaseUrl: databaseUrl,
    authSchema: "auth" as const,
    betterAuthSecret: secret,
    googleClientId: required("GOOGLE_CLIENT_ID"),
    googleClientSecret: required("GOOGLE_CLIENT_SECRET"),
    githubClientId: required("GITHUB_CLIENT_ID"),
    githubClientSecret: required("GITHUB_CLIENT_SECRET"),
  };
}

export function getPublicEnv() {
  return { publicOrigin: origin(required("PUBLIC_ORIGIN")) };
}
