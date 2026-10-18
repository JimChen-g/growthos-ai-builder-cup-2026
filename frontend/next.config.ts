import type { NextConfig } from "next";

const requiredPublicFirebaseValues = [
  "NEXT_PUBLIC_FIREBASE_API_KEY",
  "NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN",
  "NEXT_PUBLIC_FIREBASE_PROJECT_ID",
  "NEXT_PUBLIC_FIREBASE_APP_ID",
] as const;

if (process.env.NODE_ENV === "production") {
  const missing = requiredPublicFirebaseValues.filter((name) => !process.env[name]?.trim());
  if (missing.length > 0) {
    throw new Error(`Production build requires Firebase public configuration: ${missing.join(", ")}`);
  }
}

const nextConfig: NextConfig = {
  output: "export",
  trailingSlash: true,
};

export default nextConfig;
