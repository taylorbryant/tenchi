"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { withBasePath } from "@/lib/base-path";
import {
  type SearchEntry,
  type SearchResult,
  search,
  tokenize,
} from "@/lib/search";

let indexPromise: Promise<SearchEntry[]> | null = null;

function loadSearchIndex(): Promise<SearchEntry[]> {
  if (!indexPromise) {
    indexPromise = fetch(withBasePath("/search-index.json"))
      .then((response) => {
        if (!response.ok) {
          throw new Error(`search index request failed: ${response.status}`);
        }
        return response.json() as Promise<SearchEntry[]>;
      })
      .catch((error) => {
        indexPromise = null;
        throw error;
      });
  }
  return indexPromise;
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function Highlighted({ text, terms }: { text: string; terms: string[] }) {
  const pattern = useMemo(() => {
    if (terms.length === 0) return null;
    return new RegExp(`(${terms.map(escapeRegExp).join("|")})`, "gi");
  }, [terms]);

  if (!pattern) return <>{text}</>;

  const parts = text.split(pattern);
  return (
    <>
      {parts.map((part, index) =>
        index % 2 === 1 ? (
          <mark
            // biome-ignore lint/suspicious/noArrayIndexKey: parts are positional fragments of a single string
            key={index}
            className="bg-transparent font-semibold text-accent"
          >
            {part}
          </mark>
        ) : (
          part
        ),
      )}
    </>
  );
}

function resultHref(result: SearchResult): string {
  const { route, headingId } = result.entry;
  return headingId ? `${route}#${headingId}` : route;
}

function SearchDialog({ onClose }: { onClose: () => void }) {
  const router = useRouter();
  const pathname = usePathname();
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const [query, setQuery] = useState("");
  const [entries, setEntries] = useState<SearchEntry[] | null>(null);
  const [loadError, setLoadError] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const terms = useMemo(() => tokenize(query), [query]);
  const results = useMemo(
    () => (entries ? search(entries, query) : []),
    [entries, query],
  );

  useEffect(() => {
    let cancelled = false;
    loadSearchIndex()
      .then((loaded) => {
        if (!cancelled) setEntries(loaded);
      })
      .catch(() => {
        if (!cancelled) setLoadError(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  // Close if a navigation happens outside the palette while it is open.
  const mountPathname = useRef(pathname);
  useEffect(() => {
    if (pathname !== mountPathname.current) onClose();
  }, [pathname, onClose]);

  useEffect(() => {
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, []);

  // biome-ignore lint/correctness/useExhaustiveDependencies: scroll the active option into view whenever selection or results change
  useEffect(() => {
    const option = listRef.current?.querySelector('[aria-selected="true"]');
    option?.scrollIntoView({ block: "nearest" });
  }, [activeIndex, results]);

  function go(result: SearchResult) {
    onClose();
    router.push(resultHref(result));
  }

  function onKeyDown(event: React.KeyboardEvent) {
    if (event.key === "Escape") {
      event.preventDefault();
      onClose();
      return;
    }
    if (event.key === "Tab") {
      // Keyboard-first palette: keep focus in the input.
      event.preventDefault();
      return;
    }
    if (results.length === 0) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActiveIndex((index) => (index + 1) % results.length);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActiveIndex((index) => (index - 1 + results.length) % results.length);
    } else if (event.key === "Enter") {
      event.preventDefault();
      const result = results[activeIndex] ?? results[0];
      if (result) go(result);
    }
  }

  const activeOptionId =
    results.length > 0 ? `search-option-${activeIndex}` : undefined;
  const showEmptyState =
    entries !== null && terms.length > 0 && results.length === 0;

  let previousLabel: string | null = null;

  // Portal to <body> so ancestors with backdrop-filter or transform cannot
  // become the containing block for the fixed-position overlay.
  return createPortal(
    // biome-ignore lint/a11y/noStaticElementInteractions: backdrop click-to-close mirrors Escape, which stays available
    <div
      className="fixed inset-0 z-[100] overflow-y-auto overscroll-contain bg-ink/20 backdrop-blur-[2px]"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Search documentation"
        className="mx-auto mt-4 w-[calc(100%-2rem)] max-w-xl sm:mt-[18vh]"
        onKeyDown={onKeyDown}
      >
        <div className="overflow-hidden rounded-xl border border-border bg-bg shadow-2xl">
          <div className="flex items-center gap-3 border-b border-border px-4 focus-within:ring-2 focus-within:ring-inset focus-within:ring-accent/40">
            <MagnifierIcon className="size-4 shrink-0 text-ink-muted" />
            <input
              ref={inputRef}
              value={query}
              onChange={(event) => {
                setQuery(event.target.value);
                setActiveIndex(0);
              }}
              type="text"
              name="documentation-search"
              inputMode="search"
              placeholder="Search documentation…"
              spellCheck={false}
              autoComplete="off"
              autoCorrect="off"
              autoCapitalize="off"
              role="combobox"
              aria-expanded={results.length > 0}
              aria-controls="search-listbox"
              aria-activedescendant={activeOptionId}
              aria-autocomplete="list"
              aria-label="Search documentation"
              className="search-dialog-input w-full bg-transparent py-3.5 text-base text-ink placeholder:text-ink-muted sm:text-sm"
            />
            <button
              type="button"
              onClick={onClose}
              className="shrink-0 rounded border border-border bg-surface px-1.5 py-0.5 text-[11px] font-medium text-ink-muted transition-colors hover:text-ink-light"
            >
              esc
            </button>
          </div>
          <div
            ref={listRef}
            id="search-listbox"
            role="listbox"
            aria-label="Search results"
            className="max-h-[60vh] overflow-y-auto overscroll-contain p-2 sm:max-h-[50vh]"
          >
            {loadError && (
              <div className="px-3 py-6 text-center text-sm text-ink-muted">
                Search is unavailable right now.
              </div>
            )}
            {!loadError && entries === null && (
              <div className="px-3 py-6 text-center text-sm text-ink-muted">
                Loading search index…
              </div>
            )}
            {showEmptyState && (
              <div className="px-3 py-6 text-center text-sm text-ink-muted">
                No results for “{query.trim()}”
              </div>
            )}
            {results.map((result, index) => {
              const label = result.entry.sectionLabel;
              const showLabel = label !== previousLabel;
              previousLabel = label;
              const active = index === activeIndex;
              const isIntro = result.entry.headingId === "";
              return (
                <div key={resultHref(result)}>
                  {showLabel && (
                    <div className="px-3 pb-1 pt-3 text-[11px] font-medium uppercase tracking-[0.16em] text-ink-muted first:pt-1.5">
                      {label}
                    </div>
                  )}
                  {/* biome-ignore lint/a11y/useKeyWithClickEvents: keyboard handling lives on the combobox input */}
                  <div
                    id={`search-option-${index}`}
                    role="option"
                    aria-selected={active}
                    tabIndex={-1}
                    onMouseMove={() => setActiveIndex(index)}
                    onMouseDown={(event) => event.preventDefault()}
                    onClick={() => go(result)}
                    className={`cursor-pointer rounded-md px-3 py-2 transition-colors ${
                      active ? "bg-surface-muted" : ""
                    }`}
                  >
                    <div className="flex items-baseline gap-2 text-sm">
                      <span className="truncate font-medium text-ink">
                        <Highlighted
                          text={result.entry.heading}
                          terms={terms}
                        />
                      </span>
                      {!isIntro && (
                        <span className="shrink-0 truncate text-xs text-ink-muted">
                          <Highlighted
                            text={result.entry.pageTitle}
                            terms={terms}
                          />
                        </span>
                      )}
                    </div>
                    {result.snippet && (
                      <p className="mt-0.5 line-clamp-2 text-xs leading-relaxed text-ink-light">
                        <Highlighted text={result.snippet} terms={terms} />
                      </p>
                    )}
                  </div>
                </div>
              );
            })}
          </div>

          <div className="flex items-center gap-4 border-t border-border bg-surface px-4 py-2 text-[11px] text-ink-muted">
            <span>
              <kbd className="font-sans">↑↓</kbd> navigate
            </span>
            <span>
              <kbd className="font-sans">↵</kbd> open
            </span>
            <span>
              <kbd className="font-sans">esc</kbd> close
            </span>
          </div>
        </div>
      </div>
    </div>,
    document.body,
  );
}

function MagnifierIcon({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      className={className}
      aria-hidden="true"
    >
      <circle cx="7" cy="7" r="4.5" />
      <path d="m10.5 10.5 3 3" />
    </svg>
  );
}

export function SearchButton({ className }: { className?: string }) {
  const [open, setOpen] = useState(false);
  const [isMac, setIsMac] = useState(true);
  const buttonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    setIsMac(/Mac|iPhone|iPad/.test(navigator.platform));
  }, []);
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        // When several SearchButtons are mounted (for example mobile header
        // and desktop sidebar), only the first one owns the shortcut so a
        // single dialog opens.
        const triggers = document.querySelectorAll("[data-search-trigger]");
        if (triggers[0] !== buttonRef.current) return;
        event.preventDefault();
        setOpen((value) => !value);
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  function close() {
    setOpen(false);
    buttonRef.current?.focus();
  }

  return (
    <>
      <button
        ref={buttonRef}
        data-search-trigger
        type="button"
        onClick={() => setOpen(true)}
        aria-haspopup="dialog"
        aria-expanded={open}
        className={`flex touch-manipulation items-center gap-2 rounded-lg border border-border bg-surface px-2.5 py-1.5 text-sm text-ink-muted transition-colors hover:border-ink-muted/50 hover:text-ink-light sm:px-3 ${className ?? ""}`}
      >
        <MagnifierIcon className="size-3.5 shrink-0" />
        <span className="sr-only sm:not-sr-only">Search</span>
        <kbd className="ml-auto hidden rounded border border-border bg-bg px-1.5 py-0.5 font-sans text-[11px] font-medium text-ink-muted sm:block">
          {isMac ? "⌘" : "Ctrl"} K
        </kbd>
      </button>
      {open && <SearchDialog onClose={close} />}
    </>
  );
}
