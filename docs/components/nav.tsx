"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { LogoMark } from "@/components/logo-mark";
import { SearchButton } from "@/components/search";
import { ThemeToggle } from "@/components/theme-toggle";
import { docsSections } from "@/lib/docs";

function isActive(pathname: string, href: string): boolean {
  return href === "/"
    ? pathname === "/"
    : pathname === href || pathname.startsWith(`${href}/`);
}

function NavLinks({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  return (
    <div className="flex flex-col gap-7">
      {docsSections.map((section) => (
        <div key={section.label} className="flex flex-col gap-2">
          <div className="px-3 text-[11px] font-medium uppercase tracking-[0.16em] text-ink-muted">
            {section.label}
          </div>
          <div className="flex flex-col gap-0.5">
            {section.routes.map((route) => (
              <Link
                key={route.path}
                href={route.path}
                onClick={onNavigate}
                className={`rounded-md px-3 py-1.5 text-sm no-underline transition-colors ${isActive(pathname, route.path) ? "bg-accent/10 font-medium text-accent" : "text-ink-light hover:bg-surface-muted hover:text-ink"}`}
              >
                {"navLabel" in route ? route.navLabel : route.title}
              </Link>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

function SidebarMeta({ version }: { version?: string }) {
  return (
    <div className="flex items-center justify-between gap-2 border-t border-border pt-4">
      <div className="flex items-center gap-1.5">
        <a
          href="https://github.com/taylorbryant/tenchi"
          title="Tenchi on GitHub"
          className="flex size-7 items-center justify-center rounded-md text-ink-muted hover:bg-surface-muted hover:text-ink"
        >
          <span className="sr-only">Tenchi on GitHub</span>
          <svg
            viewBox="0 0 24 24"
            fill="currentColor"
            className="size-4"
            aria-hidden="true"
          >
            <path d="M12 .7a11.5 11.5 0 0 0-3.6 22.4c.6.1.8-.2.8-.5v-2c-3.3.7-4-1.4-4-1.4-.5-1.4-1.3-1.7-1.3-1.7-1.1-.7.1-.7.1-.7 1.2.1 1.8 1.2 1.8 1.2 1 1.8 2.7 1.3 3.4 1 .1-.8.4-1.3.7-1.6-2.7-.3-5.5-1.3-5.5-5.8 0-1.3.5-2.4 1.2-3.2-.1-.3-.5-1.5.1-3.2 0 0 1-.3 3.4 1.2a11.8 11.8 0 0 1 6.2 0c2.4-1.6 3.4-1.2 3.4-1.2.6 1.7.2 2.9.1 3.2.8.8 1.2 1.9 1.2 3.2 0 4.5-2.8 5.5-5.5 5.8.4.4.8 1.1.8 2.2v3.2c0 .3.2.6.8.5A11.5 11.5 0 0 0 12 .7Z" />
          </svg>
        </a>
        <a
          href="https://pypi.org/project/tenchi/"
          title="Tenchi on PyPI"
          className="flex size-7 items-center justify-center rounded-md font-mono text-xs font-semibold text-ink-muted hover:bg-surface-muted hover:text-ink"
        >
          <span className="sr-only">Tenchi on PyPI</span>Py
        </a>
        {version && (
          <span className="rounded-full border border-border px-2 py-0.5 font-mono text-[11px] text-ink-muted">
            v{version}
          </span>
        )}
      </div>
      <ThemeToggle />
    </div>
  );
}

export function Nav({ version }: { version?: string }) {
  const [open, setOpen] = useState(false);
  const pathname = usePathname();
  // biome-ignore lint/correctness/useExhaustiveDependencies: close the mobile menu after navigation
  useEffect(() => setOpen(false), [pathname]);
  useEffect(() => {
    if (!open) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previous;
    };
  }, [open]);

  return (
    <>
      <nav className="sticky top-0 z-50 border-b border-border bg-bg/90 backdrop-blur-sm xl:hidden">
        <div className="mx-auto flex max-w-3xl items-center justify-between px-6 py-4">
          <Link
            href="/"
            className="flex items-center gap-2 text-sm font-semibold text-ink no-underline"
          >
            <LogoMark />
            Tenchi
          </Link>
          <div className="flex items-center gap-3">
            <SearchButton className="-my-1" />
            <ThemeToggle />
            <button
              type="button"
              onClick={() => setOpen((value) => !value)}
              className="-mr-1 flex flex-col gap-1 p-1"
              aria-label={open ? "Close menu" : "Open menu"}
              aria-expanded={open}
            >
              <span
                className={`block h-0.5 w-4 bg-ink transition-transform ${open ? "translate-y-[3px] rotate-45" : ""}`}
              />
              <span
                className={`block h-0.5 w-4 bg-ink transition-transform ${open ? "-translate-y-[3px] -rotate-45" : ""}`}
              />
            </button>
          </div>
        </div>
      </nav>
      {open && (
        <div className="fixed inset-x-0 bottom-0 top-[57px] z-40 overflow-y-auto border-t border-border bg-bg/95 backdrop-blur-sm xl:hidden">
          <div className="mx-auto max-w-3xl px-6 py-5">
            <NavLinks onNavigate={() => setOpen(false)} />
            <div className="mt-7">
              <SidebarMeta version={version} />
            </div>
          </div>
        </div>
      )}
      <aside className="fixed inset-y-0 left-0 z-40 hidden w-64 flex-col border-r border-border bg-bg/80 px-6 py-7 backdrop-blur-sm xl:flex">
        <Link
          href="/"
          className="flex items-center gap-2 text-sm font-semibold text-ink no-underline"
        >
          <LogoMark />
          Tenchi
        </Link>
        <SearchButton className="mt-6" />
        <div className="mt-6 flex-1 overflow-y-auto pb-6">
          <NavLinks />
        </div>
        <SidebarMeta version={version} />
      </aside>
    </>
  );
}
