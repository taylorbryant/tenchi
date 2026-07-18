"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { docsRoutes } from "@/lib/docs";

function Card({
  label,
  title,
  href,
  align,
}: {
  label: string;
  title: string;
  href: string;
  align: "left" | "right";
}) {
  return (
    <Link
      href={href}
      className={`group flex flex-col gap-1 rounded-lg border border-border px-4 py-3 no-underline transition-colors hover:border-accent/40 hover:bg-accent/5 ${
        align === "right" ? "items-end text-right" : "items-start text-left"
      }`}
    >
      <span className="text-xs text-ink-muted">{label}</span>
      <span className="text-sm font-medium text-ink transition-colors group-hover:text-accent">
        {title}
      </span>
    </Link>
  );
}

export function PrevNext() {
  const pathname = usePathname();
  const index = docsRoutes.findIndex((route) => route.path === pathname);

  if (index === -1) return null;

  const previous = index > 0 ? docsRoutes[index - 1] : undefined;
  const next =
    index < docsRoutes.length - 1 ? docsRoutes[index + 1] : undefined;

  if (!previous && !next) return null;

  return (
    <nav
      aria-label="Pagination"
      className="mt-16 grid grid-cols-1 gap-3 sm:grid-cols-2"
    >
      {previous ? (
        <Card
          label="Previous"
          title={previous.title}
          href={previous.path}
          align="left"
        />
      ) : (
        <div className="hidden sm:block" />
      )}
      {next ? (
        <Card label="Next" title={next.title} href={next.path} align="right" />
      ) : (
        <div className="hidden sm:block" />
      )}
    </nav>
  );
}
