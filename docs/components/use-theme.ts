"use client";

import { useCallback, useEffect, useState } from "react";

export type Theme = "light" | "dark" | "system";
const storageKey = "tenchi-docs-theme";
const darkQuery = "(prefers-color-scheme: dark)";
const changeEvent = "tenchi-docs-theme-change";

function storedTheme(): Theme {
  try {
    const value = localStorage.getItem(storageKey);
    if (value === "light" || value === "dark" || value === "system")
      return value;
  } catch {
    // Persistence is optional.
  }
  return "system";
}

function applyTheme(theme: Theme): void {
  const dark =
    theme === "dark" ||
    (theme === "system" && window.matchMedia(darkQuery).matches);
  document.documentElement.classList.toggle("dark", dark);
}

export function useTheme() {
  const [theme, setThemeState] = useState<Theme>("system");

  useEffect(() => {
    const sync = () => {
      const value = storedTheme();
      setThemeState(value);
      applyTheme(value);
    };
    sync();
    window.addEventListener(changeEvent, sync);
    window.addEventListener("storage", sync);
    return () => {
      window.removeEventListener(changeEvent, sync);
      window.removeEventListener("storage", sync);
    };
  }, []);

  useEffect(() => {
    if (theme !== "system") return;
    const query = window.matchMedia(darkQuery);
    const onChange = () => applyTheme("system");
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, [theme]);

  const setTheme = useCallback((value: Theme) => {
    setThemeState(value);
    applyTheme(value);
    try {
      localStorage.setItem(storageKey, value);
    } catch {
      // Persistence is optional.
    }
    window.dispatchEvent(new Event(changeEvent));
  }, []);

  return { theme, setTheme };
}
