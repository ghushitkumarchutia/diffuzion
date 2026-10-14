import "server-only";
import { betterAuth, type BetterAuthOptions } from "better-auth";
import { Pool } from "pg";
import { getServerEnv } from "./env.ts";

export function createAuthOptions(env = getServerEnv()) {
  const secure =
    process.env.NODE_ENV === "production" ||
    env.publicOrigin.startsWith("https://");
  return {
    appName: "Diffuzion",
    baseURL: env.publicOrigin,
    basePath: "/api/auth",
    secret: env.betterAuthSecret,
    database: new Pool({
      connectionString: env.authDatabaseUrl,
      options: "-c search_path=auth",
      max: 5,
      connectionTimeoutMillis: 5000,
    }),
    trustedOrigins: [env.publicOrigin],
    socialProviders: {
      google: {
        clientId: env.googleClientId,
        clientSecret: env.googleClientSecret,
      },
      github: {
        clientId: env.githubClientId,
        clientSecret: env.githubClientSecret,
      },
    },
    session: {
      expiresIn: 7 * 24 * 60 * 60,
      disableSessionRefresh: true,
      cookieCache: { enabled: false },
    },
    advanced: {
      useSecureCookies: secure,
      disableOriginCheck: false,
      disableCSRFCheck: false,
      defaultCookieAttributes: { httpOnly: true, secure, sameSite: "lax" },
    },
  } satisfies BetterAuthOptions;
}

export const auth = betterAuth(createAuthOptions());
