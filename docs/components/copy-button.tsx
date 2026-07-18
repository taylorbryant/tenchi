"use client";

import { useEffect, useRef, useState } from "react";

export function CopyButton() {
  const buttonRef = useRef<HTMLButtonElement>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!copied) return;
    const timeout = window.setTimeout(() => setCopied(false), 1600);
    return () => window.clearTimeout(timeout);
  }, [copied]);

  async function copy() {
    const code = buttonRef.current
      ?.closest("[data-code-block]")
      ?.querySelector("pre");
    if (!code) return;

    try {
      await navigator.clipboard.writeText(code.innerText.trimEnd());
      setCopied(true);
    } catch {
      // Clipboard may be unavailable; fail silently.
    }
  }

  return (
    <button
      ref={buttonRef}
      type="button"
      onClick={copy}
      aria-label={copied ? "Copied" : "Copy code"}
      title={copied ? "Copied" : "Copy code"}
      className={`flex size-7 items-center justify-center rounded-md border border-border bg-bg/70 backdrop-blur-sm transition-[color,background-color,border-color,opacity] focus-visible:opacity-100 ${
        copied
          ? "text-accent opacity-100"
          : "text-ink-muted opacity-0 hover:text-ink group-hover:opacity-100"
      }`}
    >
      {copied ? (
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          className="size-3.5"
          aria-hidden="true"
        >
          <path d="M20 6 9 17l-5-5" />
        </svg>
      ) : (
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          className="size-3.5"
          aria-hidden="true"
        >
          <rect x="9" y="9" width="13" height="13" rx="2" />
          <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
        </svg>
      )}
    </button>
  );
}
