import createMDX from "@next/mdx";
import type { NextConfig } from "next";
import { tenchiDarkTheme } from "./lib/syntax-theme";

const basePath = process.env.NEXT_PUBLIC_BASE_PATH ?? "";

const withMDX = createMDX({
  options: {
    rehypePlugins: [
      "rehype-slug",
      [
        "@shikijs/rehype",
        {
          themes: { light: "github-light", dark: tenchiDarkTheme },
          addLanguageClass: true,
        },
      ],
    ],
    remarkPlugins: ["remark-gfm"],
  },
});

const nextConfig: NextConfig = {
  output: "export",
  basePath,
  pageExtensions: ["ts", "tsx", "md", "mdx"],
  trailingSlash: true,
  images: { unoptimized: true },
};

export default withMDX(nextConfig);
