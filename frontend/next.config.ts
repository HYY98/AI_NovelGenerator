import type { NextConfig } from "next";
import createNextIntlPlugin from "next-intl/plugin";

const withNextIntl = createNextIntlPlugin("./src/i18n/request.ts");
const isProduction = process.env.NODE_ENV === "production";

const nextConfig: NextConfig = {
  reactCompiler: true,
  // 本地开发允许通过 127.0.0.1 / localhost 访问 dev 资源（Next 16 默认拦截跨源 HMR）。
  allowedDevOrigins: ["127.0.0.1", "localhost"],
  typescript: {
    tsconfigPath: isProduction ? "tsconfig.build.json" : "tsconfig.json",
  },
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: "http://127.0.0.1:8300/api/:path*",
      },
    ];
  },
};

export default withNextIntl(nextConfig);
