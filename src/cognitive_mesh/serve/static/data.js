// Data browser (ADR-0044). Read-only: browse, search, filter, follow links. Nothing is edited.
"use strict";

const root = document.getElementById("root");
const S = { overview: null, tab: "index", coll: null, offset: 0, limit: 50, rec: null,
  src: null, tables: [], table: null, rows: null, q: "", sort: "", desc: false, rowOffset: 0, row: null };

const detail = h("section", { class: "panel stack", "aria-label": "Record" });

async function boot() {
  await topBar("data");
  S.overview = await api("/api/data/overview");
  if (S.overview.collections.length) S.coll = S.overview.collections[0].name;
  const names = Object.keys(S.overview.sources);
  if (names.length) S.src = names[0];
  render();
}

function tabs() {
  const mk = (key, label) => h("button", { type: "button", class: S.tab === key ? "primary" : "", "aria-pressed": S.tab === key ? "true" : "false",
    onclick: () => { S.tab = key; S.rec = null; S.row = null; render(); } }, label);
  return h("div", { class: "row" }, mk("index", "Vector index"), mk("tables", "Tables"),
    h("span", { class: "muted" }, S.overview.note));
}

function render() {
  clear(root);
  root.classList.add("split");
  const left = h("div", { class: "stack" }, tabs());
  const body = h("div", { class: "stack" });
  left.append(body);
  clear(detail);
  detail.append(h("h2", null, "Record"), h("p", { class: "muted" }, "Pick a row to read it in full."));
  root.append(left, detail);
  (S.tab === "index" ? indexTab : tablesTab)(body);
}

// ---- vector index -------------------------------------------------------------------------

function indexTab(body) {
  if (!S.overview.collections.length) {
    body.append(h("p", { class: "empty" }, S.overview.index_note || "The index has no collections yet. Run a chat turn or `memory index`."));
    return;
  }
  const pick = h("select", { id: "coll", onchange: (e) => { S.coll = e.target.value; S.offset = 0; render(); } },
    S.overview.collections.map((c) => h("option", { value: c.name, selected: c.name === S.coll }, c.name + " (" + c.count + ")")));
  const cur = S.overview.collections.find((c) => c.name === S.coll);
  body.append(h("div", { class: "row" }, h("label", { for: "coll" }, "Collection"), pick,
    h("span", { class: "muted" }, cur.model && cur.model.model_name ? "embedded with " + cur.model.model_name : "")));
  body.append(searchBox(), h("div", { class: "stack", id: "results" }), h("h2", null, "Everything in this collection"));
  const grid = h("div", { class: "stack" });
  body.append(grid, projectionBox());
  loadItems(grid);
}

function searchBox() {
  const q = h("input", { type: "search", id: "q", placeholder: "Search by meaning", size: 36 });
  const out = h("div", { class: "stack" });
  const go = h("button", { type: "button", class: "primary", onclick: async () => {
    if (!q.value.trim()) return;
    clear(out).append(h("p", { class: "muted" }, "Searching (the first search loads the embedding model and can take a while)…"));
    try {
      const r = await api("/api/index/search", { q: q.value, collection: S.coll, k: 10, min_similarity: 0.2 });
      clear(out);
      if (!r.hits.length) out.append(h("p", { class: "muted" }, r.message || "No sufficiently similar record."));
      const body = h("tbody");
      for (const hit of r.hits) {
        body.append(h("tr", { class: "pick", tabindex: "0", onclick: () => openItem(hit.id), onkeydown: (e) => { if (e.key === "Enter") openItem(hit.id); } },
          h("td", null, hit.similarity.toFixed(3)), h("td", null, hit.title), h("td", { class: "cell" }, cut(hit.excerpt, 90))));
      }
      if (r.hits.length) out.append(h("div", { class: "scroll" }, h("table", null, h("thead", null, h("tr", null, h("th", null, "closeness"), h("th", null, "record"), h("th", null, "excerpt"))), body)));
      if (r.unconfirmed.length) out.append(h("p", { class: "notice" }, r.unconfirmed.length + " match(es) whose source record could not be confirmed are not shown as results."));
      out.append(h("p", { class: "muted" }, "Closeness is how near two vectors are. It says nothing about whether a record is true."));
    } catch (err) { showError(out, err); }
  } }, "Search");
  return h("form", { class: "stack", onsubmit: (e) => { e.preventDefault(); go.click(); } },
    h("label", { for: "q" }, "Find records near a question"), h("div", { class: "row" }, q, go), out);
}

async function loadItems(grid) {
  clear(grid).append(h("p", { class: "muted" }, "Loading…"));
  try {
    const r = await api("/api/index/items", { collection: S.coll, offset: S.offset, limit: S.limit });
    clear(grid);
    const body = h("tbody");
    for (const it of r.items) {
      const m = it.metadata;
      const label = m.capability ? m.capability + " · " + (m.result_status || "") : (m.basis || "") + (m.app_id ? " · " + m.app_id : "");
      body.append(h("tr", { class: "pick" + (S.rec === it.id ? " on" : ""), tabindex: "0", onclick: () => openItem(it.id), onkeydown: (e) => { if (e.key === "Enter") openItem(it.id); } },
        h("td", { class: "cell mono" }, cut(it.id, 48)), h("td", null, label), h("td", null, m.basis || "")));
    }
    grid.append(h("div", { class: "scroll" }, h("table", null, h("thead", null, h("tr", null, h("th", null, "id"), h("th", null, "kind"), h("th", null, "basis"))), body)),
      pager(r.offset, r.limit, r.total, (o) => { S.offset = o; loadItems(grid); }));
  } catch (err) { showError(grid, err); }
}

function pager(offset, limit, total, go) {
  const last = Math.max(0, total - limit);
  return h("div", { class: "pager" },
    h("button", { type: "button", disabled: offset <= 0, onclick: () => go(Math.max(0, offset - limit)) }, "Previous"),
    h("span", null, total ? (offset + 1) + "–" + Math.min(offset + limit, total) + " of " + total : "0 of 0"),
    h("button", { type: "button", disabled: offset >= last, onclick: () => go(Math.min(last, offset + limit)) }, "Next"));
}

async function openItem(id) {
  S.rec = id;
  clear(detail).append(h("h2", null, "Record"), h("p", { class: "muted" }, "Loading…"));
  try {
    const r = await api("/api/index/item", { collection: S.coll, id });
    clear(detail).append(h("h2", null, "Record"), h("div", { class: "mono muted" }, id),
      kv(Object.entries(r.metadata).map(([k, v]) => [k, String(v)])),
      kv([["Vector", r.vector.dimension + " numbers, length " + r.vector.norm], ["First numbers", r.vector.head.map((x) => x.toFixed(3)).join(" ")]]));
    const s = r.source;
    if (s.found) {
      detail.append(s.basis === "person" ? said(s.text, "written by a person") : said(s.text, "recorded text; model-written parts are not verified"));
      if (s.cut) detail.append(h("p", { class: "muted" }, s.cut + " more characters are not shown."));
      if (s.run_id) detail.append(h("p", { class: "muted mono" }, "run " + s.run_id));
    } else {
      detail.append(h("p", { class: "notice" }, "The source record could not be confirmed (" + s.check + "). The vector is shown without text."));
    }
    const near = h("div", { class: "stack" });
    detail.append(h("button", { type: "button", onclick: async () => {
      clear(near).append(h("p", { class: "muted" }, "Looking…"));
      try {
        const n = await api("/api/index/neighbours", { collection: S.coll, id, k: 8 });
        clear(near);
        for (const x of n.neighbours) near.append(h("div", null, h("button", { type: "button", onclick: () => openItem(x.id) }, cut(x.id, 40)), " ", x.similarity.toFixed(3)));
        near.append(h("p", { class: "muted" }, n.note));
      } catch (err) { showError(near, err); }
    } }, "Show nearest records"), near);
  } catch (err) { showError(detail, err); }
}

function projectionBox() {
  const out = h("div", { class: "stack" });
  const go = h("button", { type: "button", onclick: async () => {
    clear(out).append(h("p", { class: "muted" }, "Computing…"));
    try { drawProjection(out, await api("/api/index/projection", { collection: S.coll, limit: 2000 })); }
    catch (err) { showError(out, err); }
  } }, "Draw a 2-D picture of the vectors");
  return h("section", { class: "stack" }, h("h2", null, "Map"), go, out);
}

function drawProjection(out, r) {
  clear(out);
  const size = 560;
  const labels = [...new Set(r.points.map((p) => p.label))];
  const palette = ["#1f3a93", "#17745f", "#8a5300", "#7a3b8f", "#a52f2f", "#2b7a9b"];
  const colourOf = (l) => palette[labels.indexOf(l) % palette.length];
  const canvas = svg("svg", { class: "canvas", viewBox: "0 0 " + size + " " + size, role: "img", "aria-label": "2-D map of " + r.shown + " vectors" });
  for (const p of r.points) {
    canvas.append(svg("circle", { cx: ((p.x + 1) / 2) * (size - 40) + 20, cy: ((p.y + 1) / 2) * (size - 40) + 20, r: 4.5, fill: colourOf(p.label), "fill-opacity": 0.75,
      tabindex: "0", role: "button", "aria-label": p.id, onclick: () => openItem(p.id), onkeydown: (e) => { if (e.key === "Enter") openItem(p.id); } },
      svg("title", { text: p.id })));
  }
  const leg = h("div", { class: "legend" });
  for (const l of labels) { const sw = h("span", { class: "swatch" }); sw.style.borderTopColor = colourOf(l); leg.append(h("span", null, sw, l || "unlabelled")); }
  out.append(canvas, leg, h("p", { class: "muted" }, "Showing " + r.shown + " of " + r.total + ". " + r.note));
}

// ---- tables -------------------------------------------------------------------------------

async function tablesTab(body) {
  const names = Object.keys(S.overview.sources);
  if (!names.length) { body.append(h("p", { class: "empty" }, "No database files exist yet. They appear after you add projects and run a turn.")); return; }
  const pick = h("select", { id: "src", onchange: (e) => { S.src = e.target.value; S.table = null; S.rows = null; S.rowOffset = 0; render(); } },
    names.map((n) => h("option", { value: n, selected: n === S.src }, n + " (" + S.overview.sources[n] + ")")));
  body.append(h("div", { class: "row" }, h("label", { for: "src" }, "Database"), pick));
  const list = h("div", { class: "row" });
  const grid = h("div", { class: "stack" });
  body.append(list, grid);
  try {
    const t = await api("/api/db/tables", { source: S.src });
    S.tables = t.tables;
    if (!S.table || !t.tables.some((x) => x.name === S.table)) S.table = t.tables.length ? t.tables[0].name : null;
    for (const tb of t.tables) {
      list.append(h("button", { type: "button", class: tb.name === S.table ? "primary" : "", onclick: () => { S.table = tb.name; S.q = ""; S.sort = ""; S.rowOffset = 0; render(); } }, tb.name + " (" + tb.rows + ")"));
    }
    if (S.table) loadRows(grid);
  } catch (err) { showError(grid, err); }
}

async function loadRows(grid) {
  clear(grid).append(h("p", { class: "muted" }, "Loading…"));
  try {
    const r = await api("/api/db/rows", { source: S.src, table: S.table, offset: S.rowOffset, limit: 50, q: S.q, sort: S.sort, desc: S.desc ? "1" : "" });
    clear(grid);
    const q = h("input", { type: "search", id: "flt", value: S.q, placeholder: "Filter rows containing…", size: 30 });
    grid.append(h("form", { class: "row", onsubmit: (e) => { e.preventDefault(); S.q = q.value; S.rowOffset = 0; loadRows(grid); } },
      h("label", { for: "flt" }, "Filter"), q, h("button", { type: "submit" }, "Apply")));
    const head = h("tr", null, r.columns.map((c) => h("th", { class: "sortable", tabindex: "0", scope: "col", "aria-sort": S.sort === c ? (S.desc ? "descending" : "ascending") : "none",
      onclick: () => { S.desc = S.sort === c ? !S.desc : false; S.sort = c; loadRows(grid); },
      onkeydown: (e) => { if (e.key === "Enter") e.target.click(); } }, c + (S.sort === c ? (S.desc ? " ▼" : " ▲") : ""))));
    const body = h("tbody");
    for (const row of r.rows) {
      body.append(h("tr", { class: "pick" + (S.row === row.rowid ? " on" : ""), tabindex: "0", onclick: () => openRow(row.rowid), onkeydown: (e) => { if (e.key === "Enter") openRow(row.rowid); } },
        row.cells.map((c) => h("td", { class: "cell" }, c === null ? "–" : String(c)))));
    }
    grid.append(h("div", { class: "scroll" }, h("table", null, h("thead", null, head), body)),
      pager(r.offset, r.limit, r.total, (o) => { S.rowOffset = o; loadRows(grid); }));
    if (!r.rows.length) grid.append(h("p", { class: "empty" }, S.q ? "No row contains that text." : "This table is empty."));
  } catch (err) { showError(grid, err); }
}

async function openRow(rowid) {
  S.row = rowid;
  clear(detail).append(h("h2", null, "Row"), h("p", { class: "muted" }, "Loading…"));
  try {
    const r = await api("/api/db/row", { source: S.src, table: S.table, rowid });
    clear(detail).append(h("h2", null, S.table + " · row " + rowid));
    for (const f of r.fields) {
      detail.append(h("div", null, h("label", null, f.name),
        typeof f.value === "string" && f.value.length > 80 ? h("pre", { class: "diff" }, f.value) : h("div", { class: "mono" }, f.value === null ? "–" : String(f.value)),
        f.cut ? h("small", { class: "muted" }, f.cut + " more characters are not shown.") : null));
    }
  } catch (err) { showError(detail, err); }
}

boot().catch((err) => { clear(root); showError(root, err); });
