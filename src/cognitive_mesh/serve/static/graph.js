// Workspace dependency graph (ADR-0044). Read-only. Detected edges are solid, edges a person
// declared are dashed and labelled so the two can never be confused.
"use strict";

const root = document.getElementById("root");
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

async function boot() {
  const m = await meta();
  g.chatUrl = "http://127.0.0.1:" + m.ports.app + "/?token=" + encodeURIComponent(m.token);
  await topBar("graph", h("button", { type: "button", onclick: load }, "Reload"));
  await load();
}

async function load() {
  try { g.data = await api("/api/workspace"); g.pick = null; g.kinds = new Map(); g.pos = null; g.fitted = false; render(); }
  catch (err) { clear(root); showError(root, err); }
}

boot().catch((err) => { clear(root); showError(root, err); });
