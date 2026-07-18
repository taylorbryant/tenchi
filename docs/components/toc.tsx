"use client";

import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

type TocHeading = {
  id: string;
  text: string;
  level: 2 | 3;
};

const MIN_HEADINGS = 3;
const ACTIVATION_OFFSET = 120;

export function Toc() {
  const pathname = usePathname();
  const [headings, setHeadings] = useState<TocHeading[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);

  // biome-ignore lint/correctness/useExhaustiveDependencies: re-scan headings on route change
  useEffect(() => {
    const elements = Array.from(
      document.querySelectorAll<HTMLElement>("main h2[id], main h3[id]"),
    );
    if (elements.length < MIN_HEADINGS) {
      setHeadings([]);
      setActiveId(null);
      return;
    }

    setHeadings(
      elements.map((element) => ({
        id: element.id,
        text: element.textContent ?? "",
        level: element.tagName === "H2" ? 2 : 3,
      })),
    );

    const computeActive = () => {
      const atBottom =
        window.innerHeight + window.scrollY >=
        document.documentElement.scrollHeight - 2;
      if (atBottom) {
        setActiveId(elements[elements.length - 1]?.id ?? null);
        return;
      }

      let current = elements[0]?.id ?? null;
      for (const element of elements) {
        if (element.getBoundingClientRect().top <= ACTIVATION_OFFSET) {
          current = element.id;
        } else {
          break;
        }
      }
      setActiveId(current);
    };

    computeActive();

    // IntersectionObserver drives updates during normal scrolling; the
    // rAF-throttled scroll listener covers instant jumps where headings skip
    // the observed band without a threshold crossing.
    const observer = new IntersectionObserver(() => computeActive(), {
      rootMargin: `-${ACTIVATION_OFFSET}px 0px -60% 0px`,
    });
    for (const element of elements) observer.observe(element);

    let frame = 0;
    const onScroll = () => {
      if (frame) return;
      frame = window.requestAnimationFrame(() => {
        frame = 0;
        computeActive();
      });
    };
    window.addEventListener("scroll", onScroll, { passive: true });

    return () => {
      observer.disconnect();
      window.removeEventListener("scroll", onScroll);
      if (frame) window.cancelAnimationFrame(frame);
    };
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
        {headings.map((heading) => {
          const active = heading.id === activeId;
          return (
            <li key={heading.id}>
              <a
                href={`#${heading.id}`}
                onClick={() => setActiveId(heading.id)}
                className={`-ml-px block border-l py-1 leading-snug no-underline transition-colors ${
                  heading.level === 3 ? "pl-7" : "pl-4"
                } ${
                  active
                    ? "border-accent font-medium text-accent"
                    : "border-transparent text-ink-light hover:text-ink"
                }`}
              >
                {heading.text}
              </a>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
