import type { MetadataRoute } from "next";
import { docsRoutes } from "@/lib/docs";
import { absoluteUrl } from "@/lib/seo";

export const dynamic = "force-static";

export default function sitemap(): MetadataRoute.Sitemap {
  return docsRoutes.map((route) => ({
    url: absoluteUrl(route.path),
    changeFrequency: "weekly",
    priority: route.priority,
  }));
}
