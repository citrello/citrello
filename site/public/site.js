// Hero: the prompt types itself once, then Citrello's steps appear. Style tabs switch the sample citation.
(function () {
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const prompt = document.getElementById("prompt"), steps = [...document.querySelectorAll("#steps li")];
  const file = document.getElementById("file"), replay = document.getElementById("replay");
  const text = prompt.dataset.text;
  let timers = [];
  const later = (fn, ms) => timers.push(setTimeout(fn, ms));

  function finish() {
    prompt.textContent = text; prompt.classList.remove("typing");
    steps.forEach((s) => s.classList.add("on")); file.classList.add("on"); replay.classList.add("on");
  }
  function play() {
    timers.forEach(clearTimeout); timers = [];
    steps.forEach((s) => s.classList.remove("on")); file.classList.remove("on"); replay.classList.remove("on");
    if (reduce) return finish();
    prompt.textContent = ""; prompt.classList.add("typing");
    let i = 0;
    (function type() {
      i += 1 + (Math.random() < 0.3 ? 1 : 0);
      prompt.textContent = text.slice(0, i);
      if (i < text.length) later(type, 22 + Math.random() * 40);
      else {
        prompt.classList.remove("typing");
        steps.forEach((s, k) => later(() => s.classList.add("on"), 600 + k * 900));
        later(() => file.classList.add("on"), 600 + steps.length * 900);
        later(() => replay.classList.add("on"), 900 + steps.length * 900);
      }
    })();
  }
  // start when the card is on screen
  const io = new IntersectionObserver((e) => { if (e[0].isIntersecting) { io.disconnect(); later(play, 400); } }, { threshold: 0.4 });
  io.observe(prompt);
  replay.addEventListener("click", play);

  const STYLES = {
    apa: ["Decommissioning costs are often underestimated <mark>(Topham &amp; McMillan, 2017)</mark>.",
          "Topham, E., &amp; McMillan, D. (2017). Sustainable decommissioning of an offshore wind farm. <i>Renewable Energy, 102</i>, 470–480. https://doi.org/10.1016/j.renene.2016.10.066"],
    ieee: ["Decommissioning costs are often underestimated <mark>[1]</mark>.",
           "[1] E. Topham and D. McMillan, “Sustainable decommissioning of an offshore wind farm,” <i>Renew. Energy</i>, vol. 102, pp. 470–480, 2017."],
    harvard: ["Decommissioning costs are often underestimated <mark>(Topham and McMillan, 2017)</mark>.",
              "Topham, E., McMillan, D., 2017. Sustainable decommissioning of an offshore wind farm. <i>Renew. Energy</i> 102, 470–480."],
    nature: ["Decommissioning costs are often underestimated<mark><sup>1</sup></mark>.",
             "1. Topham, E. &amp; McMillan, D. Sustainable decommissioning of an offshore wind farm. <i>Renew. Energy</i> <b>102</b>, 470–480 (2017)."],
  };
  const tabs = [...document.querySelectorAll(".style-tabs button")];
  function show(id) {
    tabs.forEach((t) => t.setAttribute("aria-selected", String(t.dataset.style === id)));
    document.getElementById("styleText").innerHTML = STYLES[id][0];
    document.getElementById("styleBib").innerHTML = STYLES[id][1];
  }
  tabs.forEach((t) => t.addEventListener("click", () => show(t.dataset.style)));
  show("apa");
})();
