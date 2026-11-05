// Workspace dependency graph (ADR-0044). Read-only. Detected edges are solid, edges a person
// declared are dashed and labelled so the two can never be confused.
"use strict";

const main = document.getElementById("root");
const root = h("div", { class: "pane" }); // the project graph draws into this
const filePane = h("div", { class: "fg stack" }); // the file and module graph draws into this
const KIND_COLOURS = ["#1f3a93", "#17745f", "#8a5300", "#7a3b8f", "#a52f2f", "#2b7a9b", "#5c6b00"];
const W = 900, H = 600;
const g = { data: null, kinds: new Map(), hidden: new Set(), pick: null, view: { x: 0, y: 0, w: W, h: H } };

function tree(value, depth) {
  if (value === null || value === undefined) return document.createTextNode("–");
  if (Array.isArray(value)) {
    if (!value.length) return document.createTextNode("none");
    return h("ul", { class: "stack" }, value.slice(0, 40).map((v) => h("li", null, tree(v, (depth || 0) + 1))));
  }
  if (typeof value === "object") {
    return kv(Object.entries(value).map(([k, v]) => [k.replace(/_/g, " "), typeof v === "object" ? tree(v, (depth || 0) + 1) : String(v)]));
  }
  return document.createTextNode(String(value));
}

function layout(nodes, edges) {
  const pos = new Map();
  nodes.forEach((n, i) => {
    const a = (2 * Math.PI * i) / Math.max(nodes.length, 1);
    pos.set(n.name, { x: W / 2 + 220 * Math.cos(a), y: H / 2 + 200 * Math.sin(a), vx: 0, vy: 0 });
  });
  const pairs = [...new Set(edges.map((e) => e.source + "\u0000" + e.target))].map((s) => s.split("\u0000"));
  for (let step = 0; step < 300; step++) {
    const cool = 1 - step / 300;
    for (const a of nodes) for (const b of nodes) {
      if (a === b) continue;
      const p = pos.get(a.name), q = pos.get(b.name);
      let dx = p.x - q.x, dy = p.y - q.y;
      const d2 = Math.max(dx * dx + dy * dy, 25);
      const f = 9000 / d2;
      const d = Math.sqrt(d2);
      p.vx += (dx / d) * f * cool; p.vy += (dy / d) * f * cool;
    }
    for (const [s, t] of pairs) {
      const p = pos.get(s), q = pos.get(t);
      if (!p || !q) continue;
      const dx = q.x - p.x, dy = q.y - p.y, d = Math.max(Math.hypot(dx, dy), 1);
      const f = (d - 190) * 0.02;
      p.vx += (dx / d) * f; p.vy += (dy / d) * f; q.vx -= (dx / d) * f; q.vy -= (dy / d) * f;
    }
    for (const n of nodes) {
      const p = pos.get(n.name);
      p.vx += (W / 2 - p.x) * 0.004; p.vy += (H / 2 - p.y) * 0.004;
      p.x = Math.min(W - 80, Math.max(80, p.x + p.vx * 0.5)); p.y = Math.min(H - 40, Math.max(40, p.y + p.vy * 0.5));
      p.vx *= 0.6; p.vy *= 0.6;
    }
  }
  return pos;
}

function colour(kind) {
  if (!g.kinds.has(kind)) g.kinds.set(kind, KIND_COLOURS[g.kinds.size % KIND_COLOURS.length]);
  return g.kinds.get(kind);
}

function fit(pos) {
  const xs = [...pos.values()].map((p) => p.x), ys = [...pos.values()].map((p) => p.y);
  if (!xs.length) return;
  const x0 = Math.min(...xs) - 120, x1 = Math.max(...xs) + 120, y0 = Math.min(...ys) - 90, y1 = Math.max(...ys) + 90;
  const w = Math.max(x1 - x0, 360), h = Math.max(y1 - y0, 240);
  const k = Math.max(w / W, h / H);
  g.view = { x: (x0 + x1) / 2 - (W * k) / 2, y: (y0 + y1) / 2 - (H * k) / 2, w: W * k, h: H * k };
}

function draw() {
  const { nodes, edges } = g.data;
  const pos = g.pos || (g.pos = layout(nodes, edges));
  if (!g.fitted) { fit(pos); g.fitted = true; }
  for (const e of edges) colour(e.kind);
  const canvas = svg("svg", { class: "canvas", viewBox: viewBox(), role: "img", "aria-label": "Dependency graph of the workspace" });
  const defs = svg("defs", null);
  for (const [kind, c] of g.kinds) {
    defs.append(svg("marker", { id: "arrow-" + safeId(kind), viewBox: "0 0 10 10", refX: "9", refY: "5", markerWidth: "7", markerHeight: "7", orient: "auto-start-reverse" },
      svg("path", { d: "M0,0 L10,5 L0,10 z", fill: c })));
  }
  canvas.append(defs);
  const seen = new Map();
  for (const e of edges) {
    if (g.hidden.has(e.kind) || (e.declared && g.hidden.has("declared"))) continue;
    const p = pos.get(e.source), q = pos.get(e.target);
    if (!p || !q) continue;
    const key = [e.source, e.target].sort().join("|");
    const n = seen.get(key) || 0; seen.set(key, n + 1);
    const mx = (p.x + q.x) / 2, my = (p.y + q.y) / 2;
    const dx = q.x - p.x, dy = q.y - p.y, d = Math.max(Math.hypot(dx, dy), 1);
    const off = (n % 2 ? 1 : -1) * Math.ceil(n / 2) * 28;
    const cx = mx - (dy / d) * off, cy = my + (dx / d) * off;
    const picked = g.pick && g.pick.edge === e;
    canvas.append(svg("path", {
      d: "M" + p.x + "," + p.y + " Q" + cx + "," + cy + " " + q.x + "," + q.y,
      fill: "none", stroke: colour(e.kind), "stroke-width": picked ? 4 : 2,
      "stroke-dasharray": e.declared ? "7 5" : null, "marker-end": "url(#arrow-" + safeId(e.kind) + ")",
      tabindex: "0", role: "button", "aria-label": e.source + " depends on " + e.target + ", " + e.kind + (e.declared ? ", declared" : ", detected"),
      onclick: () => select({ edge: e }), onkeydown: (ev) => { if (ev.key === "Enter") select({ edge: e }); },
      style: null, class: "edge",
    }));
  }
  for (const nd of nodes) {
    const p = pos.get(nd.name);
    const w = Math.max(90, nd.name.length * 8 + 24);
    const picked = g.pick && g.pick.node === nd;
    canvas.append(svg("g", { transform: "translate(" + p.x + "," + p.y + ")", tabindex: "0", role: "button", "aria-label": "Project " + nd.name,
      onclick: () => select({ node: nd }), onkeydown: (ev) => { if (ev.key === "Enter") select({ node: nd }); } },
      svg("rect", { x: -w / 2, y: -17, width: w, height: 34, rx: 8, fill: picked ? "var(--mark)" : "var(--surface)", stroke: "var(--ink)", "stroke-width": picked ? 3 : 1.5 }),
      svg("text", { "text-anchor": "middle", y: 5, "font-size": 14, "font-weight": 600, fill: picked ? "#172033" : null, text: nd.name })));
  }
  attachPanZoom(canvas);
  return canvas;
}

function safeId(s) { return String(s).replace(/[^A-Za-z0-9_-]/g, "_"); }
function viewBox() { const v = g.view; return v.x + " " + v.y + " " + v.w + " " + v.h; }

function attachPanZoom(canvas) {
  let drag = null;
  canvas.addEventListener("wheel", (ev) => {
    ev.preventDefault();
    const k = ev.deltaY < 0 ? 0.88 : 1.14;
    const r = canvas.getBoundingClientRect();
    const px = g.view.x + ((ev.clientX - r.left) / r.width) * g.view.w;
    const py = g.view.y + ((ev.clientY - r.top) / r.height) * g.view.h;
    g.view = { x: px - (px - g.view.x) * k, y: py - (py - g.view.y) * k, w: g.view.w * k, h: g.view.h * k };
    canvas.setAttribute("viewBox", viewBox());
  }, { passive: false });
  canvas.addEventListener("pointerdown", (ev) => { if (ev.target === canvas) { drag = { x: ev.clientX, y: ev.clientY }; canvas.setPointerCapture(ev.pointerId); } });
  canvas.addEventListener("pointermove", (ev) => {
    if (!drag) return;
    const r = canvas.getBoundingClientRect();
    g.view.x -= ((ev.clientX - drag.x) / r.width) * g.view.w; g.view.y -= ((ev.clientY - drag.y) / r.height) * g.view.h;
    drag = { x: ev.clientX, y: ev.clientY };
    canvas.setAttribute("viewBox", viewBox());
  });
  canvas.addEventListener("pointerup", () => { drag = null; });
}

const side = h("section", { class: "panel stack", "aria-label": "Details" });

function select(pick) { g.pick = pick; render(); }

function detail() {
  clear(side);
  side.append(h("h2", null, "Evidence"));
  const pick = g.pick;
  if (!pick) {
    side.append(h("p", { class: "muted" }, "Choose a project or a line to see where it comes from."));
  } else if (pick.node) {
    const nd = pick.node;
    side.append(kv([["Project", nd.name], ["Kind", nd.kind], ["Location", nd.location], ["Pinned commit", nd.commit], ["Application id", nd.app_id], ["Connections", nd.degree]]));
  } else {
    const e = pick.edge;
    side.append(h("p", null, h("strong", null, e.source + " → " + e.target), " ", tag(e.kind), " ", e.declared ? tag("declared by a person", "warn") : tag("detected", "ok")),
      h("p", { class: "muted" }, e.origin), tree(e.detail));
  }
  side.append(impactForm());
}

function impactForm() {
  const project = h("select", { id: "imp-p" }, g.data.nodes.map((n) => h("option", { value: n.name }, n.name)));
  const target = h("input", { type: "text", id: "imp-t", value: "package", size: 28 });
  const out = h("div", { class: "stack" });
  const go = h("button", { type: "button", onclick: async () => {
    clear(out).append(h("p", { class: "muted" }, "Looking…"));
    try { clear(out).append(tree(await api("/api/workspace/impact", { project: project.value, target: target.value }))); }
    catch (err) { showError(out, err); }
  } }, "Who depends on this?");
  return h("form", { class: "stack", onsubmit: (e) => e.preventDefault() }, h("h3", null, "Impact of a change"),
    h("div", null, h("label", { for: "imp-p" }, "Project"), project),
    h("div", null, h("label", { for: "imp-t" }, "Target: package, route:GET /users, env:NAME, …"), target), go, out);
}

function legend() {
  const row = h("div", { class: "legend" });
  const kinds = [...g.kinds.keys()];
  for (const k of kinds) {
    const box = h("input", { type: "checkbox", checked: !g.hidden.has(k), onchange: (ev) => { ev.target.checked ? g.hidden.delete(k) : g.hidden.add(k); render(); } });
    const sw = h("span", { class: "swatch" }); sw.style.borderTopColor = g.kinds.get(k);
    row.append(h("label", null, box, " ", sw, k));
  }
  const dec = h("label", null, h("input", { type: "checkbox", checked: !g.hidden.has("declared"), onchange: (ev) => { ev.target.checked ? g.hidden.delete("declared") : g.hidden.add("declared"); render(); } }),
    " ", h("span", { class: "swatch dashed" }), "declared by a person (dashed)");
  row.append(dec);
  return row;
}

function render() {
  clear(root);
  const { nodes, edges, unresolved, counts } = g.data;
  const left = h("div", { class: "stack" });
  left.append(h("h2", null, nodes.length + " project(s), " + edges.length + " connection(s)"));
  if (g.data.analysed_at) {
    left.append(h("div", { class: "row" },
      h("span", { class: "muted" }, "Analysed " + stamp(g.data.analysed_at) + " and saved; switching pages does not read the files again."),
      h("button", { type: "button", onclick: () => load(true) }, "Re-read the projects' files")));
  }
  if (!nodes.length) {
    left.append(h("p", { class: "empty" }, "No projects yet. Add a folder or GitHub address on the ",
      h("a", { href: g.chatUrl }, "Chat page"), "; it appears here as soon as it is stored."));
  }
  else {
    const canvas = draw();
    left.append(legend(), canvas);
  }
  left.append(h("p", { class: "muted" }, "Detected from the projects' own files: " + ["detected", "api", "shared_schemas", "config"].map((k) => k.replace("_", " ") + " " + (counts[k] || 0)).join(", ") + "; declared " + (counts.declared || 0) + ". Scroll to zoom, drag the background to move."));
  if (unresolved.length) {
    left.append(h("details", null, h("summary", null, unresolved.length + " reference(s) not matched to any project"), tree(unresolved)));
  }
  detail();
  root.classList.add("split"); root.append(left, side);
}

function stamp(iso) { return String(iso).slice(0, 19).replace("T", " ") + " UTC"; }

// ---- files and modules: the graph of every file in a typed folder --------------------------------
// Built from the folder once, saved on the server, and loaded from that file afterwards.

const F = { folder: "", limit: false, saved: [], projects: [], started: false };
const PINS = "cogmesh.pins.v1";
const FOLDER_KEY = "cogmesh.graph.folder";

function remembered(key, value) {
  try {
    if (value === undefined) return localStorage.getItem(key);
    localStorage.setItem(key, value);
  } catch { /* the page works without it */ }
  return null;
}

const pinStore = {
  list() { try { return JSON.parse(remembered(PINS) || "[]"); } catch { return []; } },
  save(items) { remembered(PINS, JSON.stringify(items.slice(0, 200))); },
  has(space, id) { return this.list().some((p) => p.space === space && p.id === id); },
  toggle(entry) {
    const items = this.list();
    const at = items.findIndex((p) => p.space === entry.space && p.id === entry.id);
    if (at >= 0) items.splice(at, 1); else items.push(entry);
    this.save(items);
  },
  remove(space, id) { this.save(this.list().filter((p) => !(p.space === space && p.id === id))); },
};

const fileCtx = {
  h,
  put: (parent, ...kids) => add(parent, kids),
  reput: (el, ...kids) => add(clear(el), kids),
  plural: (n, one, many) => n + " " + (n === 1 ? one : many),
  pins: pinStore,
  go: (name, params) => {
    if (name !== "graph") return;
    F.folder = params.path || F.folder;
    F.limit = false;
    fileLoad(false, params.focus || "");
  },
};
const drawFiles = window.CogFileGraph.view(fileCtx);

async function fileBoot() {
  filePane.append(h("p", { class: "muted" }, "Loading…"));
  try {
    const r = await api("/api/folder-graphs");
    F.saved = r.saved; F.projects = r.projects;
  } catch { /* the form still works */ }
  F.folder = remembered(FOLDER_KEY) || (F.projects.length ? F.projects[0].folder : "");
  fileForm();
  // Come back to the page: show the saved graph at once, never start a build by itself.
  if (F.folder && F.saved.some((s) => s.folder.toLowerCase() === F.folder.toLowerCase())) fileLoad(false, "");
}

let fileOut = null;
function fileForm() {
  clear(filePane);
  const input = h("input", { type: "text", id: "fg-path", size: 56, value: F.folder, list: "fg-folders",
    placeholder: "A folder, for example E:\\Projects", "aria-label": "Folder to graph" });
  const options = h("datalist", { id: "fg-folders" });
  const seen = new Set();
  for (const p of F.projects) if (!seen.has(p.folder)) { seen.add(p.folder); options.append(h("option", { value: p.folder }, p.name)); }
  for (const s of F.saved) if (!seen.has(s.folder)) { seen.add(s.folder); options.append(h("option", { value: s.folder })); }
  const full = h("input", { type: "checkbox", id: "fg-full", checked: F.limit });
  const go = (rebuild) => { F.folder = input.value.trim(); F.limit = full.checked; remembered(FOLDER_KEY, F.folder); fileLoad(rebuild, ""); };
  filePane.append(
    h("h2", null, "Files and modules"),
    h("p", { class: "muted" }, "Type a folder that holds one or more projects. Every Python, JavaScript and TypeScript file in it becomes a node, and an arrow from A to B means A imports or depends on B. Nothing is run. The graph is built the first time and saved; after that it is loaded from the saved file."),
    h("form", { class: "stack", onsubmit: (e) => { e.preventDefault(); go(false); } },
      h("label", { for: "fg-path" }, "Folder"), input, options,
      h("label", null, full, " Show only the first 300 nodes (faster to draw)"),
      h("div", { class: "row" },
        h("button", { type: "submit", class: "primary" }, "Show graph"),
        h("button", { type: "button", id: "fg-rebuild", onclick: () => go(true) }, "Re-read the folder"))));
  if (F.saved.length) {
    const list = h("ul", { class: "stack saved" });
    for (const s of F.saved.slice(0, 12)) {
      list.append(h("li", null, h("button", { type: "button", class: "link", onclick: () => { input.value = s.folder; go(false); } }, s.folder),
        " ", h("span", { class: "muted" }, s.nodes + " nodes, " + s.edges + " edges, saved " + stamp(s.built_at))));
    }
    filePane.append(h("details", null, h("summary", null, "Saved graphs (" + F.saved.length + ")"), list));
  }
  fileOut = h("div", { class: "stack", "aria-live": "polite" });
  filePane.append(fileOut);
}

async function fileLoad(rebuild, focus) {
  if (!fileOut) return;
  remembered(FOLDER_KEY, F.folder);
  const started = Date.now();
  const wait = h("p", { class: "muted", role: "status" });
  const tick = () => { wait.textContent = (rebuild ? "Reading every file in the folder" : "Loading the graph") + " (" + Math.round((Date.now() - started) / 1000) + " s). The first read of a large folder can take several seconds."; };
  tick();
  clear(fileOut).append(wait);
  const timer = setInterval(tick, 1000);
  try {
    const reply = await api("/api/folder-graph", { path: F.folder, limit: F.limit ? "1" : "", rebuild: rebuild ? "1" : "" });
    clearInterval(timer);
    clear(fileOut);
    const src = reply.data.source || {};
    fileOut.append(h("p", { class: "notice" }, src.kind === "saved"
      ? "Loaded from the saved graph built " + stamp(src.built_at) + " (the folder was not read again). If its files changed, choose Re-read the folder."
      : "Built from the folder just now" + (src.saved === false ? " (it could not be saved, so the next request reads the folder again)." : " and saved; the next visit loads it from the saved file.")));
    drawFiles(fileOut, reply.data, F.folder, focus);
    try { const r = await api("/api/folder-graphs"); F.saved = r.saved; } catch { /* keep the old list */ }
  } catch (err) {
    clearInterval(timer);
    showError(clear(fileOut), err);
  }
}

// ---- the two views --------------------------------------------------------------------------------

function showMode(mode) {
  filePane.hidden = mode !== "files";
  root.hidden = mode !== "projects";
  for (const b of document.querySelectorAll("[data-mode]")) b.setAttribute("aria-pressed", b.dataset.mode === mode ? "true" : "false");
  if (b_mode.last !== mode) remembered("cogmesh.graph.mode", mode);
  b_mode.last = mode;
  if (mode === "projects" && !g.data && !g.loading) load(false);
  if (mode === "files" && !F.started) { F.started = true; fileBoot(); }
}
const b_mode = { last: null };

async function boot() {
  const m = await meta();
  g.chatUrl = "http://127.0.0.1:" + m.ports.app + "/?token=" + encodeURIComponent(m.token);
  await topBar("graph");
  const tab = (mode, label) => h("button", { type: "button", "data-mode": mode, onclick: () => showMode(mode) }, label);
  main.append(
    h("div", { class: "row", role: "group", "aria-label": "Which graph" },
      tab("files", "Files and modules"), tab("projects", "Projects and their links")),
    filePane, root);
  showMode(remembered("cogmesh.graph.mode") === "projects" ? "projects" : "files");
}

async function load(refresh) {
  g.loading = true;
  clear(root).append(h("p", { class: "muted", role: "status" }, refresh
    ? "Reading the projects' files again… large projects can take a while."
    : "Loading the projects' graph… the first analysis of large projects can take a while."));
  try {
    g.data = await api("/api/workspace", { refresh: refresh ? "1" : "" });
    g.pick = null; g.kinds = new Map(); g.pos = null; g.fitted = false; render();
  } catch (err) { clear(root); showError(root, err); }
  finally { g.loading = false; }
}

boot().catch((err) => { clear(main); showError(main, err); });
