// Applies the saved (or OS) theme before first paint. A separate file, not an inline script: the page's CSP
// allows scripts from this origin only.
try {
  var t = localStorage.getItem("aidoc_theme");
  if (t === "dark" || (!t && matchMedia("(prefers-color-scheme: dark)").matches)) document.documentElement.classList.add("dark");
} catch (e) {}
