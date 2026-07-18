import { notFound } from "next/navigation";
import { PrevNext } from "@/components/prev-next";
import { docsContent } from "@/lib/content";
import { type DocsPath, docsRoutes, getDocsRoute } from "@/lib/docs";
import { createPageMetadata } from "@/lib/seo";

type PageProps = {
  params: Promise<{ slug?: string[] }>;
};

export const dynamicParams = false;

function pathFromSlug(slug: string[] | undefined): string {
  return slug?.length ? `/${slug.join("/")}` : "/";
}

function knownPath(slug: string[] | undefined): DocsPath | undefined {
  const path = pathFromSlug(slug);
  return getDocsRoute(path as DocsPath)?.path;
}

export function generateStaticParams() {
  return docsRoutes.map((route) => ({
    slug: route.path === "/" ? [] : route.path.slice(1).split("/"),
  }));
}

export async function generateMetadata({ params }: PageProps) {
  const path = knownPath((await params).slug);
  if (!path) notFound();
  return createPageMetadata(path);
}

export default async function DocsPage({ params }: PageProps) {
  const path = knownPath((await params).slug);
  if (!path) notFound();
  const Content = docsContent[path];
  return (
    <>
      <Content />
      <PrevNext path={path} />
    </>
  );
}
