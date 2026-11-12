export const themeStorageKey = "aden-theme";

export const themeInitScript = `
  try {
    const theme = localStorage.getItem(${JSON.stringify(themeStorageKey)});
    if (theme === "light" || theme === "dark") {
      document.documentElement.dataset.theme = theme;
    }
  } catch {}
`;
