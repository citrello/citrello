// Shared copy for the style previews.
window.COPY = {
  headline: "Writing is the meal. References are dessert.",
  sub: "Citrello is a free reference manager for Claude, ChatGPT, Word and Overleaf. Your library stays on your computer, and every citation stays live and checked.",
  prompt: "Write the introduction of my offshore wind decommissioning paper using only my Thesis references, leave out anything retracted, and give me a Word file in Elsevier Harvard style.",
  steps: ["Searched your Thesis project: 23 references", "Checked them on Crossref: 1 retracted, left out", "Wrote Introduction.docx with 18 live citations"],
  painTitle: "AI makes up references.",
  painText: "Ask a chatbot for sources and some of them don't exist. With Citrello, Claude and ChatGPT cite only papers in your library, checked against the publisher's record.",
};
document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("[data-copy]").forEach((el) => {
    const v = COPY[el.dataset.copy];
    if (Array.isArray(v)) el.innerHTML = v.map((s) => `<li>${s}</li>`).join("");
    else el.textContent = el.dataset.cut ? v.slice(0, +el.dataset.cut) : v;
  });
});
