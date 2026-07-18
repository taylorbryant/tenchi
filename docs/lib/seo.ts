import type { Metadata } from "next";
import {
  type DocsPath,
  defaultSiteUrl,
  docsRoutes,
  getDocsRoute,
  siteDescription,
  siteName,
} from "@/lib/docs";

export { defaultSiteUrl, docsRoutes, siteDescription, siteName };

export function getSiteUrl(): string {
  return (process.env.NEXT_PUBLIC_SITE_URL ?? defaultSiteUrl).replace(
    /\/$/,
    "",
  );
}

export function absoluteUrl(path: string): string {
  return `${getSiteUrl()}${path === "/" ? "" : path}`;
}

export function createPageMetadata(path: DocsPath): Metadata {
  const route = getDocsRoute(path);
  if (!route) throw new Error(`Missing docs route metadata for ${path}`);

  const url = absoluteUrl(route.path);
  return {
    title: path === "/" ? { absolute: route.title } : route.title,
    description: route.description,
    alternates: { canonical: url },
    openGraph: {
      type: "website",
      title: route.title,
      description: route.description,
      url,
      siteName,
    },
    twitter: {
      card: "summary",
      title: route.title,
      description: route.description,
    },
  };
}
