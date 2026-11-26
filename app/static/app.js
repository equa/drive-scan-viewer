"use strict";

// ---------------------------------------------------------------- helpers

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const h = (tag, attrs = {}, ...kids) => {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "style") el.style.cssText = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid != null) el.append(kid);
  return el;
};
const SVGNS = "http://www.w3.org/2000/svg";
const s = (tag, attrs = {}) => {
  const el = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  return el;
};

const UNITS = ["B", "kB", "MB", "GB", "TB", "PB"];
function fmtSize(b) {
  let i = 0;
  while (b >= 1000 && i < UNITS.length - 1) { b /= 1000; i++; }
  return (i === 0 ? b : b.toFixed(b < 10 ? 2 : 1)) + " " + UNITS[i];
}
const fmtInt = (n) => n.toLocaleString("en-US").replace(/,/g, " ");
function fmtPct(f) {
  const p = f * 100;
  return (p >= 99.995 ? "100" : p.toFixed(2)) + "%";
}
function fmtAge(ts) {
  if (!ts) return "–";
  const days = (Date.now() / 1000 - ts) / 86400;
  if (days < 1) return "Today";
  if (days < 2) return "1 day";
  if (days < 31) return Math.floor(days) + " days";
  const months = Math.floor(days / 30.44);
  if (months < 12) return months === 1 ? "1 month" : months + " months";
  const years = Math.floor(days / 365.25);
  return years === 1 ? "1 year" : years + " years";
}
const fmtDate = (ts) => (ts ? new Date(ts * 1000).toISOString().slice(0, 10) : "unknown");
// age buckets, edges in years - keep in sync with AGE_YEARS in server.py
const AGE_YEARS = [1, 2, 4, 8, 10];
const AGE_LABELS = ["< 1 year", "1–2 years", "2–4 years", "4–8 years", "8–10 years", "> 10 years"];
const AGE_COLORS = ["#ffeda0", "#fed976", "#feb24c", "#fd8d3c", "#e8452c", "#b10026"];
function ageBin(ts) {
  const years = (Date.now() / 1000 - ts) / (365.25 * 86400);
  let b = 0;
  while (b < AGE_YEARS.length && years >= AGE_YEARS[b]) b++;
  return b;
}
const swatch = (b) => h("span", { class: "swatch", style: `background:${AGE_COLORS[b]}` });

const items = (n) => fmtInt(n) + (n === 1 ? " item" : " items");

let toastTimer;
function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), 3500);
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    // clipboard API needs https; fall back for plain http on the LAN
    const ta = h("textarea", { style: "position:fixed;opacity:0" }, text);
    document.body.append(ta);
    ta.select();
    document.execCommand("copy");
    ta.remove();
  }
  toast("Copied: " + text);
}

let pending = 0;
let busyTimer;
async function api(path, params = {}) {
  const qs = new URLSearchParams(params).toString();
  pending++;
  clearTimeout(busyTimer);
  busyTimer = setTimeout(() => ($("#busy").hidden = pending === 0), 250);
  try {
    const res = await fetch(`/api/${path}?${qs}`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || res.statusText);
    return data;
  } finally {
    if (--pending === 0) {
      clearTimeout(busyTimer);
      $("#busy").hidden = true;
    }
  }
}

// ---------------------------------------------------------------- state

const state = {
  scans: [],        // from /api/scans
  sel: [],          // selected scan names
  ref: "",          // current node
  depth: 5,
  color: "folders", // ring colouring: "folders" or "age"
  ring: null,       // last /api/ring response
  tab: "folders",
  sort: { key: "s", asc: false },
  node: null,       // /api/node response for current ref
  expanded: new Set(),
  childCache: new Map(),
  arcs: new Map(),  // ref -> svg path
  dirty: { types: true, age: true, cleanup: true },
  find: { kind: "files", older: "0", ext: "", q: "" },
};
let navSeq = 0;

const allSelected = () => state.sel.length === state.scans.length;
const baseParams = () => ({ scans: state.sel.join(",") });

// ---------------------------------------------------------------- URL hash

function writeHash() {
  const p = new URLSearchParams();
  // omit when all are selected, so links pick up scans added later
  if (!allSelected()) p.set("scans", state.sel.join(","));
  if (state.depth !== 5) p.set("depth", state.depth);
  if (state.color !== "folders") p.set("c", state.color);
  const crumbs = state.node?.crumbs || [];
  const scanCrumbs = crumbs.filter((c) => c.r);
  if (scanCrumbs.length) {
    const scan = scanCrumbs[0].r.replace(/:\d+$/, "");
    p.set("at", scan + ":" + scanCrumbs.slice(1).map((c) => c.n).join("/"));
  }
  const hash = "#" + p.toString();
  if (location.hash !== hash) history.pushState(null, "", hash);
}

function readHash() {
  const p = new URLSearchParams(location.hash.slice(1));
  return {
    scans: (p.get("scans") || "").split(",").filter(Boolean),
    depth: parseInt(p.get("depth") || "5", 10),
    color: p.get("c") === "age" ? "age" : "folders",
    at: p.get("at"),
  };
}

async function applyHash() {
  const hs = readHash();
  const known = new Set(state.scans.map((x) => x.name));
  let sel = hs.scans.filter((n) => known.has(n));
  if (!sel.length) sel = state.scans.map((x) => x.name);  // default: all scans
  if (sel.join(",") !== state.sel.join(",")) resetCaches();
  state.sel = sel;
  state.depth = [3, 4, 5, 6, 8, 10].includes(hs.depth) ? hs.depth : 5;
  state.color = hs.color;
  syncControls();
  if (!sel.length) return renderEmpty();
  let ref = "";
  if (hs.at) {
    try {
      ref = (await api("resolve", { ...baseParams(), path: hs.at })).ref;
    } catch (e) {
      toast(e.message);
    }
  }
  await navigate(ref, false);
}

// ---------------------------------------------------------------- scans menu

async function loadScans() {
  state.scans = (await api("scans")).scans;
  renderScanMenu();
}

function renderScanMenu() {
  const menu = $("#scanMenu");
  menu.replaceChildren();
  if (!state.scans.length) {
    menu.append(h("div", { class: "empty" }, "No *.json files in the data directory"));
  }
  for (const sc of state.scans) {
    const meta = sc.loaded
      ? `${sc.root} · ${fmtSize(sc.dsize)} · ${fmtInt(sc.entries)} entries` +
        (sc.timestamp ? ` · scanned ${fmtDate(sc.timestamp)}` : "")
      : `${fmtSize(sc.bytes)} JSON · not loaded yet`;
    menu.append(
      h("label", {},
        h("input", { type: "checkbox", value: sc.name, checked: state.sel.includes(sc.name) }),
        h("span", {}, sc.name,
          h("span", { class: "meta" }, meta),
          sc.error ? h("span", { class: "meta err" }, sc.error) : null)));
  }
  menu.append(h("div", { class: "actions" },
    h("button", { onclick: () => setChecks(true) }, "All"),
    h("button", { onclick: () => setChecks(false) }, "None"),
    h("span", { class: "grow" }),
    h("button", { onclick: applyScanSelection }, "Apply")));
  syncControls();
}

function setChecks(on) {
  $$("#scanMenu input").forEach((c) => (c.checked = on));
}

function applyScanSelection() {
  const sel = $$("#scanMenu input:checked").map((c) => c.value);
  $("#scanMenu").hidden = true;
  if (!sel.length) return toast("Select at least one scan");
  if (sel.join(",") === state.sel.join(",")) return;
  state.sel = sel;
  resetCaches();
  navigate("");
}

function syncControls() {
  const n = state.sel.length;
  $("#scanButton").textContent =
    n === 0 ? "Select scans…" : n === 1 ? state.sel[0] : allSelected() ? `All scans (${n})` : `${n} scans selected`;
  $("#depth").value = String(state.depth);
  $$(".seg [data-color]").forEach((b) => b.classList.toggle("on", b.dataset.color === state.color));
}

function resetCaches() {
  state.childCache.clear();
  state.expanded.clear();
  state.dirty = { types: true, age: true, cleanup: true };
}

// ---------------------------------------------------------------- navigation

async function navigate(ref, push = true) {
  const seq = ++navSeq;
  let node, ring;
  try {
    [node, ring] = await Promise.all([
      api("node", { ...baseParams(), ref }),
      api("ring", { ...baseParams(), ref, depth: state.depth }),
    ]);
  } catch (e) {
    toast(e.message);
    return;
  }
  if (seq !== navSeq) return;
  const changed = state.node?.node.r !== node.node.r;
  state.ref = node.node.r;
  state.node = node;
  state.childCache.set(node.node.r, node);
  if (changed) {
    state.expanded.clear();
    state.dirty = { types: true, age: true, cleanup: true };
  }
  renderCrumbs();
  renderTree();
  state.ring = ring;
  renderRing(ring);
  renderLegend();
  renderScanInfo();
  refreshTab();
  if (push) writeHash();
  // scans may have been loaded by this request - refresh menu metadata
  if (state.scans.some((x) => state.sel.includes(x.name) && !x.loaded)) loadScans().catch(() => {});
}

function renderEmpty() {
  $("#tree").replaceChildren(h("div", { class: "empty" }, "Select one or more scans to start."));
  $("#ring").replaceChildren();
  $("#crumbs").replaceChildren();
}

function renderScanInfo() {
  const ts = state.node.scanned;
  $("#scanInfo").textContent = ts ? `Scanned ${fmtDate(ts)} (${fmtAge(ts).toLowerCase()} ago)`.replace("(today ago)", "(today)") : "";
}

function renderCrumbs() {
  const nav = $("#crumbs");
  nav.replaceChildren();
  state.node.crumbs.forEach((c, i) => {
    if (i) nav.append(h("span", { class: "sep" }, "›"));
    nav.append(h("a", { onclick: () => navigate(c.r) }, c.n));
  });
  if (state.node.path) {
    nav.append(h("button", { class: "btn copy", title: state.node.path, onclick: () => copyText(state.node.path) }, "Copy path"));
  }
  $$(".here").forEach((el) => (el.textContent = state.node.node.n));
}

function goUp() {
  const c = state.node?.crumbs;
  if (c && c.length > 1) navigate(c[c.length - 2].r);
}

// ---------------------------------------------------------------- folder tree

function sortKids(kids) {
  const { key, asc } = state.sort;
  const out = [...kids];
  out.sort((a, b) => {
    const x = a[key], y = b[key];
    const c = key === "n" ? String(x).localeCompare(String(y), undefined, { numeric: true, sensitivity: "base" }) : x - y;
    return asc ? c : -c;
  });
  return out;
}

function flags(n) {
  const out = [];
  if (n.e) out.push(h("span", { class: "flag err", title: "Some content could not be read; sizes may be too low" }, "!"));
  if (n.x) out.push(h("span", { class: "flag", title: "Excluded from the scan" }, "excluded"));
  if (n.h) out.push(h("span", { class: "flag", title: "Hard link; size counted at another path" }, "link"));
  return out;
}

function row(n, level, total, top = false) {
  const canExpand = n.d && n.k && !top;
  const open = state.expanded.has(n.r);
  const frac = total ? n.s / total : 0;
  const el = h("div", {
      class: `row ${n.d ? "dir" : "file"} ${top ? "top" : ""}`,
      "data-ref": n.r,
      onmouseenter: () => highlight(n.r, true),
      onmouseleave: () => highlight(n.r, false),
    },
    h("span", { class: "name", style: `padding-left:${level * 16}px` },
      h("span", { class: "tw", onclick: canExpand ? () => toggle(n.r) : null }, canExpand ? (open ? "▼" : "▶") : ""),
      top ? null : h("span", { class: "pct", title: fmtPct(frac) + " of " + state.node.node.n },
        h("i", { style: `width:${Math.max(frac * 100, 0)}%` }), h("span", {}, fmtPct(frac))),
      h("span", {
        class: "label", title: n.n,
        onclick: n.d && !top ? () => navigate(n.r) : null,
      }, n.n),
      ...flags(n)),
    h("span", { class: "c-size" }, fmtSize(n.s)),
    h("span", { class: "c-items" }, n.d ? items(n.i - 1) : ""),
    h("span", { class: "c-mod", title: fmtDate(n.m) }, state.color === "age" && n.m ? swatch(ageBin(n.m)) : null, fmtAge(n.m)));
  return el;
}

function renderTree() {
  const tree = $("#tree");
  const scroll = tree.scrollTop;
  const total = state.node.node.s;
  const out = [row(state.node.node, 0, total, true)];
  const walk = (data, level) => {
    for (const k of sortKids(data.children)) {
      out.push(row(k, level, total));
      if (state.expanded.has(k.r) && state.childCache.has(k.r)) walk(state.childCache.get(k.r), level + 1);
    }
    if (data.more) {
      out.push(h("div", { class: "more", style: `padding-left:${level * 16 + 34}px` },
        `… and ${fmtInt(data.more.count)} smaller items (${fmtSize(data.more.s)}) not listed`));
    }
  };
  walk(state.node, 1);
  if (!state.node.children.length) out.push(h("div", { class: "empty" }, "This folder is empty."));
  tree.replaceChildren(...out);
  tree.scrollTop = scroll;
  $$(".thead [data-sort]").forEach((el) => {
    el.classList.toggle("sorted", el.dataset.sort === state.sort.key);
    el.classList.toggle("asc", state.sort.asc);
  });
}

async function toggle(ref) {
  if (state.expanded.has(ref)) {
    state.expanded.delete(ref);
    return renderTree();
  }
  if (!state.childCache.has(ref)) {
    try {
      state.childCache.set(ref, await api("node", { ...baseParams(), ref }));
    } catch (e) {
      return toast(e.message);
    }
  }
  state.expanded.add(ref);
  renderTree();
}

// ---------------------------------------------------------------- ring chart

const VB = 1000, CX = 500, CY = 500, R = 492, R0 = 118;

function arcPath(r0, r1, a0, a1) {
  if (a1 - a0 >= 2 * Math.PI - 1e-6) {
    // full ring: two half annuli
    return arcPath(r0, r1, a0, a0 + Math.PI) + arcPath(r0, r1, a0 + Math.PI, a0 + 2 * Math.PI);
  }
  const p = (r, a) => `${(CX + r * Math.sin(a)).toFixed(2)},${(CY - r * Math.cos(a)).toFixed(2)}`;
  const large = a1 - a0 > Math.PI ? 1 : 0;
  return `M${p(r1, a0)}A${r1},${r1} 0 ${large} 1 ${p(r1, a1)}` +
    `L${p(r0, a1)}A${r0},${r0} 0 ${large} 0 ${p(r0, a0)}Z`;
}

function arcColor(n, mid, level) {
  if (n.o) return `hsl(0 0% ${level % 2 ? 46 : 52}%)`;
  if (state.color === "age") return n.m ? AGE_COLORS[ageBin(n.m)] : "hsl(0 0% 50%)";
  // baobab-style: hue follows the angle, deeper rings get darker
  const hue = (mid / (2 * Math.PI)) * 320 + 130;
  const light = Math.max(54 - (level - 1) * 5, 28);
  return n.d ? `hsl(${hue % 360} 68% ${light}%)` : `hsl(${hue % 360} 28% ${light + 6}%)`;
}

function renderRing(root) {
  const svg = $("#ring");
  svg.setAttribute("viewBox", `0 0 ${VB} ${VB}`);
  svg.replaceChildren();
  state.arcs.clear();
  const depth = state.depth;
  const ringW = (R - R0) / depth;
  const total = root.s || 1;
  const g = s("g");
  const draw = (node, a0, level) => {
    let a = a0;
    for (const c of node.c || []) {
      const span = (c.s / total) * 2 * Math.PI;
      if (span > 0.0015) {
        const r0 = R0 + (level - 1) * ringW, r1 = r0 + ringW;
        const path = s("path", {
          d: arcPath(r0, r1, a, a + span),
          fill: arcColor(c, a + span / 2, level),
          class: c.o ? "other" : c.d ? "dir" : "file",
        });
        if (c.m && !c.o) path.dataset.age = ageBin(c.m);
        path.addEventListener("mousemove", (ev) => showTip(ev, c, node));
        path.addEventListener("mouseenter", () => highlight(c.r, true));
        path.addEventListener("mouseleave", () => { hideTip(); highlight(c.r, false); });
        if (c.d && c.k && c.r) path.addEventListener("click", () => { hideTip(); navigate(c.r); });
        g.append(path);
        if (c.r) state.arcs.set(c.r, path);
        if (level < depth) draw(c, a, level + 1);
      }
      a += span;
    }
  };
  draw(root, 0, 1);
  svg.append(g);

  const up = state.node.crumbs.length > 1;
  const center = s("circle", { cx: CX, cy: CY, r: R0 - 6, class: "center" });
  center.addEventListener("click", goUp);
  const title = s("title");
  title.textContent = up ? "Go up one level" : root.n;
  center.append(title);
  svg.append(center);
  const t1 = s("text", { x: CX, y: CY + 14, class: "ctext" });
  t1.textContent = fmtSize(root.s);
  const t2 = s("text", { x: CX, y: CY + 50, class: "csub" });
  t2.textContent = up ? "▲ up" : "";
  const t0 = s("text", { x: CX, y: CY - 34, class: "csub" });
  t0.textContent = root.n.length > 16 ? root.n.slice(0, 15) + "…" : root.n;
  svg.append(t0, t1, t2);
}

async function renderLegend() {
  const box = $("#legend");
  box.hidden = state.color !== "age";
  if (box.hidden) return;
  const draw = (ages) => {
    const total = ages ? ages.reduce((a, b) => a + b, 0) || 1 : 1;
    box.replaceChildren(
      h("div", { class: "title" }, "Last modified"),
      ...AGE_LABELS.map((label, b) => h("div", {
          class: "item",
          title: ages ? `${fmtPct(ages[b] / total)} of the files' space here is ${label.replace("<", "less than").replace(">", "more than")} old` : "",
          onmouseenter: () => $$("#ring path").forEach((p) => p.classList.toggle("dim", p.dataset.age !== String(b))),
          onmouseleave: () => $$("#ring path.dim").forEach((p) => p.classList.remove("dim")),
        },
        swatch(b), h("span", {}, label), h("span", { class: "v" }, ages ? fmtSize(ages[b]) : ""))),
      h("div", { class: "note" }, "Slices show the newest change anywhere inside. Sizes count each file by its own age."));
  };
  const ref = state.ref;
  draw(statsData?.key === statsKey() ? statsData.ages : null);
  try {
    const st = await loadStats();
    if (state.ref === ref && state.color === "age") draw(st.ages);
  } catch (e) {
    toast(e.message);
  }
}

function highlight(ref, on) {
  if (!ref) return;
  state.arcs.get(ref)?.classList.toggle("hl", on);
  $$(`.row[data-ref="${CSS.escape(ref)}"]`).forEach((r) => r.classList.toggle("hl", on));
}

function showTip(ev, n, parent) {
  const tip = $("#tooltip");
  const total = state.node.node.s || 1;
  tip.replaceChildren(
    h("b", {}, n.n),
    h("div", {}, fmtSize(n.s), h("span", { class: "k" }, ` · ${fmtPct(n.s / total)} of ${state.node.node.n}`)),
    parent && parent.r !== state.node.node.r && parent.s
      ? h("div", { class: "k" }, `${fmtPct(n.s / parent.s)} of ${parent.n}`) : null,
    n.d ? h("div", { class: "k" }, items(n.i - 1)) : null,
    n.m ? h("div", { class: "k" }, `Last modified ${fmtDate(n.m)} (${fmtAge(n.m).toLowerCase()})`) : null);
  tip.hidden = false;
  const pad = 14;
  const { innerWidth: W, innerHeight: H } = window;
  const r = tip.getBoundingClientRect();
  tip.style.left = Math.min(ev.clientX + pad, W - r.width - 8) + "px";
  tip.style.top = Math.min(ev.clientY + pad, H - r.height - 8) + "px";
}
const hideTip = () => ($("#tooltip").hidden = true);

// ---------------------------------------------------------------- tabs

function setTab(tab) {
  state.tab = tab;
  $$(".tabs button").forEach((b) => b.classList.toggle("on", b.dataset.tab === tab));
  $$(".tab").forEach((t) => (t.hidden = t.id !== "tab-" + tab));
  refreshTab();
}

let statsData = null;
let statsPending = null;
const statsKey = () => state.sel.join(",") + "|" + state.ref;
async function loadStats() {
  const key = statsKey();
  if (statsData?.key === key) return statsData;
  if (statsPending?.key !== key) {
    const p = api("stats", { ...baseParams(), ref: state.ref }).then((st) => (statsData = { key, ...st }));
    p.catch(() => {}).finally(() => { if (statsPending?.p === p) statsPending = null; });
    statsPending = { key, p };
  }
  return statsPending.p;
}
async function refreshTab() {
  const tab = state.tab;
  if (!state.node || tab === "folders" || !state.dirty[tab]) return;
  state.dirty[tab] = false;
  try {
    if (tab === "types" || tab === "age") {
      const st = await loadStats();
      tab === "types" ? renderTypes(st) : renderAge(st);
    } else if (tab === "cleanup") {
      await runFind();
    }
  } catch (e) {
    state.dirty[tab] = true;
    toast(e.message);
  }
}

function renderTypes(st) {
  const box = $("#types");
  const max = Math.max(...st.exts.map((e) => e.s), 1);
  const total = st.exts.reduce((a, e) => a + e.s, 0) || 1;
  box.replaceChildren(...st.exts.map((e) =>
    h("div", {
        class: "bar-row" + (e.o ? " other" : ""),
        title: e.o ? "" : `Show largest .${e.ext || "(none)"} files`,
        onclick: e.o ? null : () => showType(e.ext),
      },
      h("span", { class: "k" }, e.o ? e.ext : e.ext ? "." + e.ext : "(no extension)"),
      h("span", { class: "track" }, h("i", { style: `width:${(e.s / max) * 100}%` })),
      h("span", { class: "v", title: fmtPct(e.s / total) }, fmtSize(e.s)),
      h("span", { class: "n" }, fmtInt(e.count) + " files"))));
  if (!st.exts.length) box.replaceChildren(h("div", { class: "empty" }, "No files here."));
}

function showType(ext) {
  state.find = { ...state.find, kind: "files", ext: ext || "", q: "" };
  syncFindControls();
  state.dirty.cleanup = true;
  setTab("cleanup");
}

function renderAge(st) {
  const box = $("#age");
  const years = st.years.filter((y) => y.year > 0);
  if (!years.length) return box.replaceChildren(h("div", { class: "empty" }, "No files here."));
  const now = new Date().getFullYear();
  const first = Math.min(...years.map((y) => y.year));
  const bins = [];
  for (let y = first; y <= Math.max(now, years[years.length - 1].year); y++) {
    bins.push(years.find((b) => b.year === y) || { year: y, s: 0, count: 0 });
  }
  const W = 640, H = 260, L = 8, B = 22, T = 10;
  const max = Math.max(...bins.map((b) => b.s), 1);
  const bw = (W - L * 2) / bins.length;
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: "none" });
  svg.append(s("line", { x1: L, x2: W - L, y1: H - B, y2: H - B, class: "grid" }));
  bins.forEach((b, i) => {
    const hgt = (b.s / max) * (H - B - T);
    const x = L + i * bw;
    if (b.s > 0) {
      const r = s("rect", {
        x: x + Math.min(1, bw * 0.1), y: H - B - Math.max(hgt, 1),
        width: Math.max(bw - 2, 1), height: Math.max(hgt, 1), rx: Math.min(3, bw / 4), class: "col",
      });
      r.addEventListener("mousemove", (ev) => {
        const tip = $("#tooltip");
        tip.replaceChildren(h("b", {}, String(b.year)), h("div", {}, fmtSize(b.s)), h("div", { class: "k" }, fmtInt(b.count) + " files"));
        tip.hidden = false;
        tip.style.left = ev.clientX + 14 + "px";
        tip.style.top = ev.clientY + 14 + "px";
      });
      r.addEventListener("mouseleave", hideTip);
      svg.append(r);
    }
    const every = Math.ceil(bins.length / 10);
    if ((b.year % every === 0) || i === bins.length - 1 && bins.length < 4) {
      const t = s("text", { x: x + bw / 2, y: H - 6, "text-anchor": "middle" });
      t.textContent = b.year;
      svg.append(t);
    }
  });
  const total = years.reduce((a, y) => a + y.s, 0) || 1;
  const olderThan = (n) => years.filter((y) => y.year < now - n + 1).reduce((a, y) => a + y.s, 0);
  const tile = (label, v) => h("div", {}, h("b", {}, fmtSize(v)), h("span", {}, `${label} · ${fmtPct(v / total)}`));
  box.replaceChildren(
    svg,
    h("div", { class: "age-sum" },
      tile(`untouched since before ${now - 1}`, olderThan(2)),
      tile(`untouched since before ${now - 4}`, olderThan(5)),
      tile(`untouched since before ${now - 9}`, olderThan(10))));
}

// ---------------------------------------------------------------- cleanup tab

function syncFindControls() {
  $$("[data-kind]").forEach((b) => b.classList.toggle("on", b.dataset.kind === state.find.kind));
  $("#older").value = state.find.older;
  $("#ext").value = state.find.ext;
  $("#q").value = state.find.q;
  $("#ext").disabled = state.find.kind === "dirs";
}

let findSeq = 0;
async function runFind() {
  const f = state.find;
  if (f.kind === "dirs" && f.older === "0") {
    f.older = "1825";
    syncFindControls();
  }
  const seq = ++findSeq;
  const res = await api("find", { ...baseParams(), ref: state.ref, kind: f.kind, older: f.older, ext: f.ext, q: f.q, limit: 300 });
  if (seq !== findSeq) return;
  const total = state.node.node.s || 1;
  const what = f.kind === "dirs" ? "untouched folders" : "matching files";
  $("#findSummary").textContent = res.count
    ? `${fmtInt(res.count)} ${what} totalling ${fmtSize(res.total)} (${fmtPct(res.total / total)} of ${state.node.node.n})` +
      (res.count > res.rows.length ? ` · showing the ${res.rows.length} largest` : "")
    : `No ${what} here.`;
  $("#found").replaceChildren(...res.rows.map((n) =>
    h("div", {
        class: "row " + (n.d ? "dir" : "file"),
        title: (n.d ? "Open this folder" : "Open the containing folder") + "\n" + n.p,
        onclick: () => navigate(n.d ? n.r : n.pr),
      },
      h("span", { class: "name" },
        h("span", { class: "label" }, n.n, " ", ...flags(n)),
        h("span", { class: "sub" }, "‎" + n.p + "‎")),
      h("span", { class: "c-size" }, fmtSize(n.s)),
      h("span", { class: "c-mod", title: fmtDate(n.m) }, fmtAge(n.m)))));
}

const debounce = (fn, ms) => {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
};
const refind = debounce(() => {
  state.dirty.cleanup = true;
  refreshTab();
}, 300);

// ---------------------------------------------------------------- wiring

$("#scanButton").addEventListener("click", (e) => {
  e.stopPropagation();
  const m = $("#scanMenu");
  m.hidden = !m.hidden;
  if (!m.hidden) { loadScans().catch((err) => toast(err.message)); }
});
document.addEventListener("click", (e) => {
  if (!$("#scanPicker").contains(e.target)) $("#scanMenu").hidden = true;
});
$$(".seg [data-color]").forEach((b) => b.addEventListener("click", () => {
  if (state.color === b.dataset.color) return;
  state.color = b.dataset.color;
  syncControls();
  if (state.ring) renderRing(state.ring);
  renderTree();
  renderLegend();
  writeHash();
}));
$("#depth").addEventListener("change", (e) => {
  state.depth = parseInt(e.target.value, 10);
  navigate(state.ref);
});
$$(".tabs button").forEach((b) => b.addEventListener("click", () => setTab(b.dataset.tab)));
$$(".thead [data-sort]").forEach((el) => el.addEventListener("click", () => {
  const key = el.dataset.sort;
  state.sort = state.sort.key === key ? { key, asc: !state.sort.asc } : { key, asc: key === "n" };
  renderTree();
}));
$$("[data-kind]").forEach((b) => b.addEventListener("click", () => {
  state.find.kind = b.dataset.kind;
  syncFindControls();
  refind();
}));
$("#older").addEventListener("change", (e) => { state.find.older = e.target.value; refind(); });
$("#ext").addEventListener("input", (e) => { state.find.ext = e.target.value; refind(); });
$("#q").addEventListener("input", (e) => { state.find.q = e.target.value; refind(); });
document.addEventListener("keydown", (e) => {
  if (e.target.matches("input, select")) return;
  if (e.key === "Backspace" || (e.altKey && e.key === "ArrowUp")) { e.preventDefault(); goUp(); }
});
window.addEventListener("popstate", () => applyHash());

(async function init() {
  try {
    await loadScans();
    syncFindControls();
    await applyHash();
  } catch (e) {
    toast(e.message);
  }
})();
