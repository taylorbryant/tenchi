"use client";

import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

type Heading = { id: string; text: string; level: 2 | 3 };

export function Toc() {
  const pathname = usePathname();
  const [headings, setHeadings] = useState<Heading[]>([]);
  const [active, setActive] = useState<string | null>(null);

  // biome-ignore lint/correctness/useExhaustiveDependencies: re-scan rendered headings after navigation
  useEffect(() => {
    const elements = Array.from(
      document.querySelectorAll<HTMLElement>("main h2[id], main h3[id]"),
    );
    if (elements.length < 3) {
      setHeadings([]);
      return;
    }
    setHeadings(
      elements.map((element) => ({
        id: element.id,
        text: element.textContent ?? "",
        level: element.tagName === "H2" ? 2 : 3,
      })),
    );

    const compute = () => {
      let current = elements[0]?.id ?? null;
      for (const element of elements) {
        if (element.getBoundingClientRect().top <= 120) current = element.id;
        else break;
      }
      setActive(current);
    };
    compute();
    window.addEventListener("scroll", compute, { passive: true });
    return () => window.removeEventListener("scroll", compute);
  }, [pathname]);

  if (headings.length === 0) return null;
  return (
    <nav
      aria-label="On this page"
      className="fixed inset-y-0 right-0 hidden w-64 overflow-y-auto px-6 py-20 xl:block"
    >
      <div className="text-[11px] font-medium uppercase tracking-[0.16em] text-ink-muted">
        On this page
      </div>
      <ul className="mt-3 flex flex-col gap-0.5 border-l border-border text-sm">
        {headings.map((heading) => (
          <li key={heading.id}>
            <a
              href={`#${heading.id}`}
              className={`-ml-px block border-l py-1 leading-snug no-underline transition-colors ${heading.level === 3 ? "pl-7" : "pl-4"} ${active === heading.id ? "border-accent font-medium text-accent" : "border-transparent text-ink-light hover:text-ink"}`}
            >
              {heading.text}
            </a>
          </li>
        ))}
      </ul>
    </nav>
  );
}
