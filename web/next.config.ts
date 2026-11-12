import type { NextConfig } from "next";
import { PHASE_DEVELOPMENT_SERVER } from "next/constants";

export default function config(phase: string): NextConfig {
  if (phase !== PHASE_DEVELOPMENT_SERVER) return { output: "export" };
  const backend = process.env.ADEN_API_URL || "http://127.0.0.1:8000";
  return {
    devIndicators: false,
    experimental: { proxyTimeout: 300_000 },
    async rewrites() {
      return [{ source: "/api/:path*", destination: `${backend}/:path*` }];
    },
  };
}
