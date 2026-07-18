"use client";

import { useCallback, useEffect, useState } from "react";

export type Theme = "light" | "dark" | "system";

export const THEME_STORAGE_KEY = "tenchi-docs-theme";

const DARK_QUERY = "(prefers-color-scheme: dark)";
const CHANGE_EVENT = "tenchi-docs-theme-change";

function storedTheme(): Theme {
  try {
    const value = localStorage.getItem(THEME_STORAGE_KEY);
    if (value === "light" || value === "dark" || value === "system") {
      return value;
    }
  } catch {
    // localStorage may be unavailable; fall back to system.
  }
  return "system";
}

function applyTheme(theme: Theme): void {
  const dark =
    theme === "dark" ||
    (theme === "system" && window.matchMedia(DARK_QUERY).matches);
  document.documentElement.classList.toggle("dark", dark);
}

export interface UseThemeResult {
  theme: Theme;
  setTheme: (theme: Theme) => void;
}

/**
 * Light/dark/system theme with localStorage persistence. The inline bootstrap
 * script in app/layout.tsx applies the initial class before paint; this hook
 * keeps it in sync afterwards and follows OS changes while in system mode.
 */
export function useTheme(): UseThemeResult {
  const [theme, setThemeState] = useState<Theme>("system");

  // Apply only values read from storage or chosen explicitly — never the
  // pre-hydration default, which would flash the wrong theme when a second
  // toggle instance mounts (e.g. opening the mobile nav).
  useEffect(() => {
    const sync = () => {
      const stored = storedTheme();
      setThemeState(stored);
      applyTheme(stored);
    };
    sync();

    // Keep every mounted toggle (mobile bar, sidebar) on the same value.
    window.addEventListener(CHANGE_EVENT, sync);
    window.addEventListener("storage", sync);
    return () => {
      window.removeEventListener(CHANGE_EVENT, sync);
      window.removeEventListener("storage", sync);
    };
  }, []);

  useEffect(() => {
    if (theme !== "system") return;

    const query = window.matchMedia(DARK_QUERY);
    const onChange = () => applyTheme("system");
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, [theme]);

  const setTheme = useCallback((next: Theme) => {
    setThemeState(next);
    applyTheme(next);
    try {
      localStorage.setItem(THEME_STORAGE_KEY, next);
    } catch {
      // Persistence is best-effort.
    }
    window.dispatchEvent(new Event(CHANGE_EVENT));
  }, []);

  return { theme, setTheme };
}
