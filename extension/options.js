// Where the toolbar button sends papers: the web app (default) or the desktop app.
chrome.storage.local.get("target").then(({ target }) => {
  document.querySelector(`input[value="${target === "desktop" ? "desktop" : "web"}"]`).checked = true;
});
document.addEventListener("change", (e) => {
  if (e.target.name !== "target") return;
  chrome.storage.local.set({ target: e.target.value }).then(() => { document.getElementById("saved").textContent = "Saved."; });
});

// the notice on papers' pages (on unless turned off; it also needs the extension to be allowed to read pages)
chrome.storage.local.get("notice").then(({ notice }) => { document.getElementById("notice").checked = notice !== false; });
document.getElementById("notice").addEventListener("change", async (e) => {
  const on = e.target.checked;
  if (on && !(await chrome.permissions.contains({ origins: ["<all_urls>"] })) && !(await chrome.permissions.request({ origins: ["<all_urls>"] }))) {
    e.target.checked = false;
    return;
  }
  await chrome.storage.local.set({ notice: on });
  document.getElementById("saved").textContent = "Saved.";
});
