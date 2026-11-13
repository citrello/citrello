// A stand-in for Office.js (Word), for testing Citrello's Word add-in in an ordinary browser.
// The document is one paragraph made of text pieces and content controls; the cursor is a position in it (or inside
// a control). Only what the add-in uses is here. Tests drive it with window.__doc (type, put the cursor, read it).
(function () {
  "use strict";
  const segs = [];            // {k: "t", t} | {k: "cc", id, tag, title, appearance, runs: [{t, font}]}
  let anchor = null;          // the cursor: {seg (a text piece), off}, so edits before it don't move it
  let inCC = null;            // ... or inside this content control
  let nextId = 1;
  const handlers = [];
  const settingsStore = {};

  const ccText = (s) => s.runs.map((r) => r.t).join("");
  const segLen = (s) => (s.k === "t" ? s.t.length : ccText(s).length);
  const flat = () => segs.map((s) => (s.k === "t" ? s.t : ccText(s))).join("");
  // paragraphs: the text between "\n"s (a reference list moved into a control keeps its "\n"s)
  let paraXml = [];           // Word's XML of each paragraph of a loaded document (getOoxml)
  let fileBytes = null;       // the loaded .docx (getFileAsync)
  const trackLog = [];
  const opened = [];          // pages the add-in opened in the browser (Office.context.ui.openBrowserWindow)
  function paraSpans() {
    const t = flat(), out = [];
    let s = 0;
    for (let i = 0; i <= t.length; i++) if (i === t.length || t[i] === "\n") { out.push([s, i]); s = i + 1; }
    return out;
  }
  const startOf = (seg) => { let p = 0; for (const s of segs) { if (s === seg) return p; p += segLen(s); } return -1; };
  function splitAt(pos) {  // a piece boundary at pos -> index of the piece starting there
    let p = 0;
    for (let i = 0; i < segs.length; i++) {
      const s = segs[i], n = segLen(s);
      if (pos === p) return i;
      if (pos < p + n) {
        if (s.k !== "t") throw new Error("mock: can't split a content control");
        const cut = pos - p, second = { k: "t", t: s.t.slice(cut) };
        s.t = s.t.slice(0, cut);
        segs.splice(i + 1, 0, second);
        if (anchor && anchor.seg === s && anchor.off > cut) anchor = { seg: second, off: anchor.off - cut };
        return i + 1;
      }
      p += n;
    }
    return segs.length;
  }
  function removeRange(s, e) {  // text only
    const i = splitAt(s), j = splitAt(e);
    const gone = segs.splice(i, j - i);
    if (anchor && gone.includes(anchor.seg)) setCursor(s);
    return i;
  }
  const cursorPos = () => (inCC ? startOf(inCC) : startOf(anchor.seg) + anchor.off);
  function setCursor(pos, cc) {
    inCC = cc || null;
    if (cc) return;
    const i = splitAt(pos), seg = { k: "t", t: "" };
    segs.splice(i, 0, seg);
    anchor = { seg, off: 0 };
  }
  const fire = () => handlers.forEach((h) => setTimeout(() => h({ type: "documentSelectionChanged" })));

  class Font { constructor(o) { this._o = o; } }
  ["italic", "bold", "superscript", "subscript", "smallCaps"].forEach((k) => Object.defineProperty(Font.prototype, k, {
    get() { return !!this._o[k]; }, set(v) { this._o[k] = v; } }));

  class Range {
    constructor(s, e, inCC) { this.s = s; this.e = e; this.inCC = inCC || null; }
    load() { return this; }
    get text() { return flat().slice(this.s, this.e); }
    get isNullObject() { return false; }
    get parentContentControlOrNullObject() { return this.inCC ? new CC(this.inCC) : NULL_CC; }
    get paragraphs() {
      const sp = paraSpans().find(([a, b]) => this.s >= a && this.s <= b) || [0, flat().length];
      return { getFirst: () => new Range(sp[0], sp[1]) };
    }
    insertOoxml(pkg, where) {  // the paragraph's runs and content controls from Word's XML
      const xml = new DOMParser().parseFromString(pkg, "application/xml");
      const W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main";
      const p = xml.getElementsByTagNameNS(W, "p")[0];
      const out = [];
      const texts = (el) => [...el.getElementsByTagNameNS(W, "t")].map((t) => t.textContent).join("");
      for (const el of p.children) {
        if (el.localName === "sdt") {
          const tag = el.getElementsByTagNameNS(W, "tag")[0], alias = el.getElementsByTagNameNS(W, "alias")[0];
          out.push({ k: "cc", id: nextId++, tag: tag ? tag.getAttributeNS(W, "val") : "", title: alias ? alias.getAttributeNS(W, "val") : "",
                     appearance: "", runs: [{ t: texts(el), font: {} }] });
        } else if (el.localName === "r" || el.localName === "hyperlink") out.push({ k: "t", t: texts(el) });
        else if (el.localName === "fldSimple") out.push({ k: "t", t: texts(el) });
      }
      const i = removeRange(this.s, this.e);
      segs.splice(i, 0, ...out);
      return this;
    }
    getRange(where) {
      if (where === "Start") return new Range(this.s, this.s, this.inCC);
      if (where === "End" || where === "After") return new Range(this.e, this.e, this.inCC);
      return new Range(this.s, this.e, this.inCC);
    }
    expandTo(r) { return new Range(Math.min(this.s, r.s), Math.max(this.e, r.e)); }
    search(str) {
      const t = this.text, items = [];
      for (let i = t.indexOf(str); i >= 0; i = t.indexOf(str, i + 1)) items.push(new Range(this.s + i, this.s + i + str.length));
      return { items, load() { return this; } };
    }
    insertText(t, where) {
      if (this.inCC) return new CC(this.inCC).insertText(t, where);
      let at = where === "Start" ? this.s : this.e;
      if (where === "Replace") { removeRange(this.s, this.e); at = this.s; }
      if (!inCC && at === cursorPos()) {  // typing at the cursor: the cursor moves along
        anchor.seg.t = anchor.seg.t.slice(0, anchor.off) + t + anchor.seg.t.slice(anchor.off);
        anchor.off += t.length;
      } else segs.splice(splitAt(at), 0, { k: "t", t });
      return new Range(at, at + t.length);
    }
    insertContentControl() {  // around the range's text (a collapsed range: an empty control)
      const text = flat().slice(this.s, this.e);
      const i = this.s === this.e ? splitAt(this.s) : removeRange(this.s, this.e);
      const seg = { k: "cc", id: nextId++, tag: "", title: "", appearance: "", runs: text ? [{ t: text, font: {} }] : [] };
      segs.splice(i, 0, seg);
      return new CC(seg);
    }
    select() { setCursor(this.s, this.inCC); fire(); }
  }

  class CC {
    constructor(seg) { this.seg = seg; }
    load() { return this; }
    get isNullObject() { return false; }
    get id() { return this.seg.id; }
    get tag() { return this.seg.tag; } set tag(v) { this.seg.tag = v; }
    get title() { return this.seg.title; } set title(v) { this.seg.title = v; }
    get appearance() { return this.seg.appearance; } set appearance(v) { this.seg.appearance = v; }
    get text() { return ccText(this.seg).replace(/\n/g, "\r"); }
    clear() { this.seg.runs = []; }
    insertText(t, where) {
      const run = { t, font: {} };
      if (where === "Replace") this.seg.runs = [run];
      else if (where === "Start") this.seg.runs.unshift(run);
      else this.seg.runs.push(run);
      const s = startOf(this.seg);
      const r = new Range(s, s + t.length, this.seg);
      r.font = new Font(run.font);
      return r;
    }
    getRange(where) {
      const s = startOf(this.seg), e = s + segLen(this.seg);
      if (where === "After" || where === "End") return new Range(e, e);
      if (where === "Before" || where === "Start") return new Range(s, s);
      return new Range(s, e, this.seg);
    }
    select() { setCursor(0, this.seg); fire(); }
    delete() { const s = startOf(this.seg), i = segs.indexOf(this.seg); if (i >= 0) segs.splice(i, 1); if (inCC === this.seg) setCursor(s); }
  }
  const NULL_CC = { isNullObject: true, load() { return this; }, tag: "" };

  class Para extends Range {
    constructor(s, e, k) { super(s, e); this.k = k; }
    getRange(where) { return where === "Content" || where === "Whole" ? new Range(this.s, this.e) : super.getRange(where); }
    getOoxml() { return { value: paraXml[this.k] || "" }; }
  }
  let tracking = "Off";
  const ctx = {
    document: {
      get changeTrackingMode() { return tracking; },
      set changeTrackingMode(v) { tracking = v; trackLog.push(v); },
      load() { return this; },
      body: { get paragraphs() { return { items: paraSpans().map(([a, b], k) => new Para(a, b, k)), load() { return this; } }; } },
      getSelection: () => { const p = cursorPos(); return new Range(p, p, inCC); },
      get contentControls() {
        return { items: segs.filter((s) => s.k === "cc").map((s) => new CC(s)), load() { return this; } };
      },
    },
    sync: () => new Promise((r) => setTimeout(r, 1)),
  };

  window.Word = { run: async (fn) => fn(ctx) };
  window.Office = {
    HostType: { Word: "Word" },
    EventType: { DocumentSelectionChanged: "documentSelectionChanged" },
    FileType: { Compressed: "compressed" },
    onReady(cb) { const info = { host: "Word", platform: "PC" }; setTimeout(() => cb && cb(info)); return Promise.resolve(info); },
    context: { requirements: { isSetSupported: () => true }, ui: { openBrowserWindow: (url) => opened.push(url) }, document: {
      getFileAsync(type, opts, cb) {
        const data = fileBytes || new Uint8Array();
        cb({ status: "succeeded", value: { sliceCount: 1, getSliceAsync: (i, cb2) => cb2({ status: "succeeded", value: { data } }), closeAsync() {} } });
      },
      addHandlerAsync(type, h, cb) { handlers.push(h); cb && cb({ status: "succeeded" }); },
      settings: { get: (k) => (k in settingsStore ? settingsStore[k] : null), set: (k, v) => { settingsStore[k] = v; },
                  saveAsync: (cb) => cb && cb({ status: "succeeded" }) },
    } },
  };

  // for tests
  setCursor(0);
  window.__doc = {
    type(t) {
      if (inCC) setCursor(startOf(inCC) + segLen(inCC));
      new Range(cursorPos(), cursorPos()).insertText(t, "End"); fire();
    },
    text: flat,
    end() { setCursor(flat().length); fire(); },
    clickControl(n) { setCursor(0, segs.filter((s) => s.k === "cc")[n]); fire(); },
    controls: () => segs.filter((s) => s.k === "cc").map((s) => ({ tag: s.tag, title: s.title, text: ccText(s),
      runs: s.runs.map((r) => ({ t: r.t, ...r.font })) })),
    settings: settingsStore,
    trackLog,
    opened,
    tracking: () => tracking,
    load(doc) {  // a real document: its .docx (base64) and each paragraph's text and XML, as Word would give them
      segs.length = 0;
      fileBytes = Uint8Array.from(atob(doc.file), (c) => c.charCodeAt(0));
      paraXml = doc.paragraphs.map((p) => p.xml);
      segs.push({ k: "t", t: doc.paragraphs.map((p) => p.text).join("\n") });
      setCursor(flat().length);
    },
  };
})();
