"use client";

import { createAuthClient } from "better-auth/react";

export const authClient = createAuthClient({ basePath: "/api/auth" });

export function safeCallbackURL(value: string | null | undefined): string {
  if (
    !value ||
    value.length > 2048 ||
    !value.startsWith("/") ||
    value.startsWith("//") ||
    /[\\\u0000-\u0020\u007f]/.test(value)
  )
    return "/dashboard";
  const origin = "https://diffuzion.invalid";
  try {
    const parsed = new URL(value, origin);
    if (parsed.origin !== origin || /%2f|%5c/i.test(parsed.pathname))
      return "/dashboard";
    return `${parsed.pathname}${parsed.search}${parsed.hash}`;
  } catch {
    return "/dashboard";
  }
}

export function socialSignIn(
  provider: "google" | "github",
  callbackURL?: string,
) {
  return authClient.signIn.social({
    provider,
    callbackURL: safeCallbackURL(callbackURL),
  });
}

export function signOut() {
  return authClient.signOut();
}
