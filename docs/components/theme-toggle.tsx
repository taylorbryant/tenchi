"use client";

import { type Theme, useTheme } from "@/components/use-theme";

const options: Array<{ value: Theme; label: string; icon: React.ReactNode }> = [
  {
    value: "light",
    label: "Light theme",
    icon: (
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
        <circle cx="12" cy="12" r="4" />
        <path d="M12 2v2m0 16v2M4.93 4.93l1.41 1.41m11.32 11.32 1.41 1.41M2 12h2m16 0h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41" />
      </svg>
    ),
  },
  {
    value: "system",
    label: "System theme",
    icon: (
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
        <rect x="3" y="4" width="18" height="13" rx="2" />
        <path d="M8 21h8m-4-4v4" />
      </svg>
    ),
  },
  {
    value: "dark",
    label: "Dark theme",
    icon: (
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
        <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z" />
      </svg>
    ),
  },
];

export function ThemeToggle({ className = "" }: { className?: string }) {
  const { theme, setTheme } = useTheme();
  return (
    <fieldset
      className={`m-0 min-w-0 border-0 p-0 ${className}`}
      aria-label="Theme"
    >
      <legend className="sr-only">Theme</legend>
      <div className="flex items-center gap-0.5 rounded-full border border-border p-0.5">
        {options.map((option) => {
          const active = theme === option.value;
          return (
            <button
              key={option.value}
              type="button"
              aria-pressed={active}
              aria-label={option.label}
              title={option.label}
              onClick={() => setTheme(option.value)}
              className={`flex size-6 touch-manipulation items-center justify-center rounded-full transition-colors ${
                active
                  ? "bg-surface-muted text-ink"
                  : "text-ink-muted hover:text-ink active:text-ink"
              }`}
            >
              {option.icon}
            </button>
          );
        })}
      </div>
    </fieldset>
  );
}
