import type { MetadataRoute } from "next";
import { withBasePath } from "@/lib/base-path";
import { siteDescription } from "@/lib/docs";

export const dynamic = "force-static";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "Tenchi documentation",
    short_name: "Tenchi",
    description: siteDescription,
    start_url: withBasePath("/"),
    display: "standalone",
    background_color: "#ffffff",
    theme_color: "#047857",
    icons: [
      { src: withBasePath("/icon.svg"), sizes: "any", type: "image/svg+xml" },
    ],
  };
}
