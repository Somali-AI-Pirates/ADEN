"use client";

import { useEffect, useState } from "react";
import { themeStorageKey } from "../lib/theme";

export function ThemeSwitch() {
  const [dark, setDark] = useState(false);

  useEffect(() => {
    const system = window.matchMedia("(prefers-color-scheme: dark)");
    const sync = () => {
      const selected = document.documentElement.dataset.theme;
      setDark(selected ? selected === "dark" : system.matches);
    };
    sync();
    system.addEventListener("change", sync);
    return () => system.removeEventListener("change", sync);
  }, []);

  function toggleTheme() {
    const theme = dark ? "light" : "dark";
    document.documentElement.dataset.theme = theme;
    setDark(!dark);
    try {
      localStorage.setItem(themeStorageKey, theme);
    } catch {
      // The theme remains active on the current page.
    }
  }

  return (
    <button
      type="button"
      className="theme-switch"
      role="switch"
      aria-label="다크 모드"
      aria-checked={dark}
      title={dark ? "라이트 모드로 전환" : "다크 모드로 전환"}
      onClick={toggleTheme}
    >
      <span aria-hidden="true">☀</span>
      <span aria-hidden="true">☾</span>
    </button>
  );
}
