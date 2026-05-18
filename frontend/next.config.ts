import type { NextConfig } from "next";
import path from "node:path";

const nextConfig: NextConfig = {
  output: "standalone",
  outputFileTracingRoot: path.join(__dirname, ".."),
  async rewrites() {
    const backendTarget = process.env.BACKEND_PROXY_TARGET ?? "http://127.0.0.1:18001";
    return [
      {
        source: "/api/:path*",
        destination: `${backendTarget}/api/:path*`,
      },
      {
        source: "/healthz",
        destination: `${backendTarget}/healthz`,
      },
    ];
  },
};

export default nextConfig;
