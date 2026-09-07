import { createBrowserClient } from "@supabase/ssr";

const ANON_KEY_COOKIE = "otclick-supabase-anon-key";
const GOOGLE_AUTH_COOKIE = "otclick-google-auth-enabled";

function readCookie(name: string): string | null {
  if (typeof document === "undefined") return null;
  const prefix = `${encodeURIComponent(name)}=`;
  for (const part of document.cookie.split(";")) {
    const value = part.trim();
    if (!value.startsWith(prefix)) continue;
    return decodeURIComponent(value.slice(prefix.length));
  }
  return null;
}

export function isGoogleAuthEnabled(): boolean {
  return readCookie(GOOGLE_AUTH_COOKIE) === "true";
}

export function createClient() {
  if (typeof window === "undefined") {
    throw new Error("Browser Supabase client can only be created in the browser");
  }
  const anonKey = readCookie(ANON_KEY_COOKIE);
  if (!anonKey) {
    throw new Error("Runtime Supabase configuration is missing; reload the page");
  }

  return createBrowserClient(window.location.origin, anonKey, {
    cookieOptions: { name: "sb-otclick-auth-token" },
  });
}
