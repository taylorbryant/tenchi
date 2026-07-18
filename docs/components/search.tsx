"use client";

import { useRouter } from "next/navigation";
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
        if (!response.ok)
          throw new Error(`Search index failed: ${response.status}`);
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
  const pattern = useMemo(
    () =>
      terms.length > 0
        ? new RegExp(`(${terms.map(escapeRegExp).join("|")})`, "gi")
        : null,
    [terms],
  );
  if (!pattern) return <>{text}</>;
  return (
    <>
      {text.split(pattern).map((part, index) =>
        index % 2 === 1 ? (
          <mark
            // biome-ignore lint/suspicious/noArrayIndexKey: fragments are positional parts of one string
            key={`${part}-${index}`}
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
  return result.entry.headingId
    ? `${result.entry.route}#${result.entry.headingId}`
    : result.entry.route;
}

function SearchDialog({ onClose }: { onClose: () => void }) {
  const router = useRouter();
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
      .then((value) => {
        if (!cancelled) setEntries(value);
      })
      .catch(() => {
        if (!cancelled) setLoadError(true);
      });
    inputRef.current?.focus();
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      cancelled = true;
      document.body.style.overflow = previousOverflow;
    };
  }, []);

  // biome-ignore lint/correctness/useExhaustiveDependencies: scroll when keyboard selection changes
  useEffect(() => {
    listRef.current
      ?.querySelector('[aria-selected="true"]')
      ?.scrollIntoView({ block: "nearest" });
  }, [activeIndex]);

  function openResult(result: SearchResult) {
    onClose();
    router.push(resultHref(result));
  }

  function onKeyDown(event: React.KeyboardEvent) {
    if (event.key === "Escape") {
      event.preventDefault();
      onClose();
      return;
    }
    if (event.key === "Tab") event.preventDefault();
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
      if (result) openResult(result);
    }
  }

  return createPortal(
    // biome-ignore lint/a11y/noStaticElementInteractions: backdrop click mirrors the Escape key
    <div
      className="fixed inset-0 z-[100] overflow-y-auto bg-ink/20 backdrop-blur-[2px]"
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
          <div className="flex items-center gap-3 border-b border-border px-4">
            <Magnifier className="size-4 shrink-0 text-ink-muted" />
            <input
              ref={inputRef}
              value={query}
              onChange={(event) => {
                setQuery(event.target.value);
                setActiveIndex(0);
              }}
              placeholder="Search documentation…"
              role="combobox"
              aria-controls="search-results"
              aria-expanded={results.length > 0}
              aria-activedescendant={
                results.length > 0 ? `search-result-${activeIndex}` : undefined
              }
              autoComplete="off"
              spellCheck={false}
              className="w-full bg-transparent py-3.5 text-sm text-ink outline-none placeholder:text-ink-muted"
            />
            <button
              type="button"
              onClick={onClose}
              className="rounded border border-border bg-surface px-1.5 py-0.5 text-[11px] text-ink-muted"
            >
              esc
            </button>
          </div>
          <div
            ref={listRef}
            id="search-results"
            role="listbox"
            className="max-h-[55vh] overflow-y-auto p-2"
          >
            {loadError && (
              <div className="px-3 py-6 text-center text-sm text-ink-muted">
                Search is unavailable.
              </div>
            )}
            {!loadError && entries === null && (
              <div className="px-3 py-6 text-center text-sm text-ink-muted">
                Loading search…
              </div>
            )}
            {entries && terms.length > 0 && results.length === 0 && (
              <div className="px-3 py-6 text-center text-sm text-ink-muted">
                No results for “{query.trim()}”
              </div>
            )}
            {results.map((result, index) => (
              <div
                key={resultHref(result)}
                id={`search-result-${index}`}
                role="option"
                aria-selected={index === activeIndex}
                tabIndex={-1}
                onMouseMove={() => setActiveIndex(index)}
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => openResult(result)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") openResult(result);
                }}
                className={`cursor-pointer rounded-md px-3 py-2 ${index === activeIndex ? "bg-surface-muted" : ""}`}
              >
                <div className="flex items-baseline gap-2 text-sm">
                  <span className="truncate font-medium text-ink">
                    <Highlighted text={result.entry.heading} terms={terms} />
                  </span>
                  {result.entry.headingId && (
                    <span className="shrink-0 text-xs text-ink-muted">
                      {result.entry.pageTitle}
                    </span>
                  )}
                </div>
                {result.snippet && (
                  <p className="mt-0.5 line-clamp-2 text-xs leading-relaxed text-ink-light">
                    <Highlighted text={result.snippet} terms={terms} />
                  </p>
                )}
              </div>
            ))}
          </div>
          <div className="flex gap-4 border-t border-border bg-surface px-4 py-2 text-[11px] text-ink-muted">
            <span>↑↓ navigate</span>
            <span>↵ open</span>
            <span>esc close</span>
          </div>
        </div>
      </div>
    </div>,
    document.body,
  );
}

function Magnifier({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      className={className}
      aria-hidden="true"
    >
      <circle cx="7" cy="7" r="4.5" />
      <path d="m10.5 10.5 3 3" />
    </svg>
  );
}

export function SearchButton({ className = "" }: { className?: string }) {
  const [open, setOpen] = useState(false);
  const [isMac, setIsMac] = useState(true);
  const buttonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => setIsMac(/Mac|iPhone|iPad/.test(navigator.platform)), []);
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        const triggers = document.querySelectorAll("[data-search-trigger]");
        if (triggers[0] !== buttonRef.current) return;
        event.preventDefault();
        setOpen((value) => !value);
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  return (
    <>
      <button
        ref={buttonRef}
        data-search-trigger
        type="button"
        onClick={() => setOpen(true)}
        aria-haspopup="dialog"
        aria-expanded={open}
        className={`flex items-center gap-2 rounded-lg border border-border bg-surface px-2.5 py-1.5 text-sm text-ink-muted hover:border-ink-muted/50 hover:text-ink-light sm:px-3 ${className}`}
      >
        <Magnifier className="size-3.5" />
        <span className="sr-only sm:not-sr-only">Search</span>
        <kbd className="ml-auto hidden rounded border border-border bg-bg px-1.5 py-0.5 font-sans text-[11px] sm:block">
          {isMac ? "⌘" : "Ctrl"} K
        </kbd>
      </button>
      {open && (
        <SearchDialog
          onClose={() => {
            setOpen(false);
            buttonRef.current?.focus();
          }}
        />
      )}
    </>
  );
}
