// Light / dark theme: sets data-theme on <html> (read by style.css) and handles the navbar switch.
// Loaded in <head> without defer so the theme is applied before the first paint.
// The choice is saved in localStorage; without a saved choice the system setting decides.

/** Return the saved theme ("light" or "dark"), or the system preference when nothing is saved. */
function preferredTheme() {
  try {
    const saved = localStorage.getItem("theme");
    if (saved === "light" || saved === "dark") return saved;
  } catch (error) { /* storage unavailable */ }
  return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

document.documentElement.dataset.theme = preferredTheme();

document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("theme-toggle").addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "light" ? "dark" : "light";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("theme", next); } catch (error) { /* storage unavailable */ }
  });
});
