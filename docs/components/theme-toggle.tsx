"use client";

import { type Theme, useTheme } from "@/components/use-theme";

const options: Array<{ value: Theme; label: string; icon: React.ReactNode }> = [
  {
    value: "light",
    label: "Light theme",
    icon: <span aria-hidden="true">☼</span>,
  },
  {
    value: "system",
    label: "System theme",
    icon: <span aria-hidden="true">◫</span>,
  },
  {
    value: "dark",
    label: "Dark theme",
    icon: <span aria-hidden="true">☾</span>,
  },
];

export function ThemeToggle({ className = "" }: { className?: string }) {
  const { theme, setTheme } = useTheme();
  return (
    <fieldset className={`m-0 min-w-0 border-0 p-0 ${className}`}>
      <legend className="sr-only">Theme</legend>
      <div className="flex items-center gap-0.5 rounded-full border border-border p-0.5">
        {options.map((option) => (
          <button
            key={option.value}
            type="button"
            aria-pressed={theme === option.value}
            aria-label={option.label}
            title={option.label}
            onClick={() => setTheme(option.value)}
            className={`flex size-6 items-center justify-center rounded-full text-xs transition-colors ${
              theme === option.value
                ? "bg-surface-muted text-ink"
                : "text-ink-muted hover:text-ink"
            }`}
          >
            {option.icon}
          </button>
        ))}
      </div>
    </fieldset>
  );
}
