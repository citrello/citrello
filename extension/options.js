// Where the toolbar button sends papers: the web app (default) or the desktop app.
chrome.storage.local.get("target").then(({ target }) => {
  document.querySelector(`input[value="${target === "desktop" ? "desktop" : "web"}"]`).checked = true;
});
document.addEventListener("change", (e) => {
  if (e.target.name !== "target") return;
  chrome.storage.local.set({ target: e.target.value }).then(() => { document.getElementById("saved").textContent = "Saved."; });
});
