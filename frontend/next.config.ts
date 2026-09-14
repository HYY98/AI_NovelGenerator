import type { NextConfig } from "next";
import createNextIntlPlugin from "next-intl/plugin";

const withNextIntl = createNextIntlPlugin("./src/i18n/request.ts");
const isProduction = process.env.NODE_ENV === "production";

const nextConfig: NextConfig = {
  reactCompiler: true,
  typescript: {
    tsconfigPath: isProduction ? "tsconfig.build.json" : "tsconfig.json",
  },
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: "http://127.0.0.1:8200/api/:path*",
      },
    ];
  },
};

export default withNextIntl(nextConfig);
