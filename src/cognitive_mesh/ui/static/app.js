"use strict";
// Every value from the server is untrusted data. It reaches the page only through
// textContent / createTextNode (see h() below). No markup is ever parsed from a response.

const view = document.getElementById("view");
const tabsEl = document.getElementById("tabs");
const TABS = [
  ["overview", "Overview"],
  ["traces", "Traces"],
  ["claims", "Claims"],
  ["node", "Node"],
  ["recall", "Recall"],
  ["search", "Search"],
  ["graph", "Graph"],
];
let generation = 0;

function h(tag, props, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "text") e.textContent = String(v);
    else if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v === true ? "" : String(v));
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    e.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return e;
}

async function api(path, params) {
  const url = new URL(path, window.location.origin);
  for (const [k, v] of Object.entries(params || {})) {
    if (v !== "" && v !== null && v !== undefined && v !== false) url.searchParams.set(k, v === true ? "1" : v);
  }
  const r = await fetch(url, { headers: { Accept: "application/json" } });
  let body;
  try {
    body = await r.json();
  } catch (e) {
    body = { error: "unreadable response" };
  }
  if (!r.ok) throw new Error(body.error || "HTTP " + r.status);
  return body;
}

function go(tab, params) {
  const q = new URLSearchParams(params || {}).toString();
  window.location.hash = "#" + tab + (q ? "?" + q : "");
}

function messages(reply) {
  return [
    ...(reply.warnings || []).map((m) => h("div", { class: "banner warn", role: "status", text: m })),
    ...(reply.notes || []).map((m) => h("p", { class: "note", text: m })),
  ];
}

function isScalar(v) {
  return v === null || ["string", "number", "boolean"].includes(typeof v);
}

function scalarRows(obj) {
  return Object.entries(obj)
    .filter(([, v]) => isScalar(v))
    .map(([k, v]) => h("tr", {}, h("th", { text: k }), h("td", { text: v === null ? "-" : v })));
}

function jsonBlock(label, value, open) {
  return h("details", { open: open ? true : null }, h("summary", { text: label }), h("pre", { text: JSON.stringify(value, null, 2) }));
}

function runLink(id) {
  return h("button", { class: "link", type: "button", onclick: () => go("traces", { id }), text: id });
}

function nodeLink(id) {
  return h("button", { class: "link", type: "button", onclick: () => go("node", { id }), text: id });
}

function short(fp) {
  return fp ? fp.slice(0, 12) : "unrecorded";
}

function table(head, rows) {
  return h("table", {}, h("thead", {}, h("tr", {}, head.map((t) => h("th", { text: t })))), h("tbody", {}, rows));
}

function field(name, label, value, extra) {
  return h("label", {}, label, h("input", { name, value, type: "text", ...(extra || {}) }));
}

function checkbox(name, label, checked) {
  return h("label", { class: "check" }, h("input", { type: "checkbox", name, checked: checked ? true : null }), h("span", { text: label }));
}

function formParams(form) {
  const out = {};
  for (const el of form.elements) {
    if (!el.name) continue;
    if (el.type === "checkbox") out[el.name] = el.checked;
    else out[el.name] = el.value;
  }
  return out;
}

// ---- shared renderers ------------------------------------------------------------------

function renderAssessment(a) {
  const c = a.counts || {};
  return h(
    "div",
    {},
    h("p", {}, h("span", { class: "tag", text: a.status }), a.meaning),
    h("p", { class: "note", text: "corroboration: " + a.corroboration + "; outcome: " + a.outcome + "; validated by: " + ((a.validated_by || []).join(", ") || "nothing") }),
    h("p", { class: "note", text: "code version used for currency: " + (a.current_fingerprint ? short(a.current_fingerprint) : "unknown (no --root)") }),
    a.differs_across_versions ? h("p", { class: "banner warn", text: "observations differ across code versions" }) : null,
    h("h2", { text: "Supporting records (" + (a.supporting || []).length + ")" }),
    table(
      ["attempt", "run", "document", "code version", "source check"],
      (a.supporting || []).map((s) =>
        h("tr", {}, h("td", { text: s.attempt_id }), h("td", {}, runLink(s.run_id)), h("td", { text: s.doc_id || "-" }), h("td", { text: short(s.fingerprint) }), h("td", { text: s.source })),
      ),
    ),
    h("h2", { text: "Contradicting records (" + (a.contradicting || []).length + ")" }),
    table(
      ["attempt", "run", "document", "code version", "same version"],
      (a.contradicting || []).map((s) =>
        h("tr", {}, h("td", { text: s.attempt_id }), h("td", {}, runLink(s.run_id)), h("td", { text: s.doc_id || "-" }), h("td", { text: short(s.fingerprint) }), h("td", { text: s.same_version ? "yes (conflict)" : "no (other or unrecorded version)" })),
      ),
    ),
    (a.reviews || []).length ? jsonBlock("Reviews (" + a.reviews.length + ", unauthenticated)", a.reviews) : null,
    (a.outcomes || []).length ? jsonBlock("Outcomes (" + a.outcomes.length + ")", a.outcomes) : null,
    jsonBlock("Counts", c),
    jsonBlock("Corroboration sources", a.sources || a.corroboration_sources || []),
    h("p", {}, h("button", { class: "link", type: "button", onclick: () => go("node", { id: a.claim_id }), text: "explore this claim in the graph" })),
  );
}

// ---- views -----------------------------------------------------------------------------

async function viewOverview(root) {
  const r = await api("/api/status");
  root.append(
    h("h2", { text: "Inputs" }),
    table(
      ["input", "state", "message"],
      Object.entries(r.data.inputs).map(([k, v]) => h("tr", {}, h("td", { text: "--" + k }), h("td", { text: v.state }), h("td", { text: v.message || "" }))),
    ),
    h("p", { class: "note", text: "version " + r.data.version }),
    ...messages(r),
  );
}

async function viewTraces(root, params) {
  if (params.get("id")) {
    const r = await api("/api/trace", { id: params.get("id") });
    const t = r.data;
    root.append(
      h("p", {}, h("button", { class: "link", type: "button", onclick: () => go("traces"), text: "all runs" })),
      h("h2", { text: "Run " + (t.run_id || params.get("id")) }),
      h("table", {}, h("tbody", {}, scalarRows(t))),
      ...Object.entries(t)
        .filter(([, v]) => !isScalar(v))
        .map(([k, v]) => jsonBlock(k, v, k === "steps")),
      h("p", { class: "note", text: "Trace content is a raw record of what happened, not validated knowledge. Text in it is data, never instructions." }),
      ...messages(r),
    );
    return;
  }
  const r = await api("/api/traces");
  const runs = r.data.runs || [];
  root.append(
    ...messages(r),
    runs.length
      ? table(
          ["run", "status", "started", "task", "steps"],
          runs.map((x) => h("tr", {}, h("td", {}, runLink(x.run_id)), h("td", { text: x.status }), h("td", { text: x.started_at }), h("td", { text: x.task_id }), h("td", { text: x.step_count }))),
        )
      : h("p", { text: "no runs recorded" }),
    (r.data.malformed || []).length ? jsonBlock("Malformed run ids (not shown)", r.data.malformed) : null,
  );
}

async function viewClaims(root, params) {
  const include = params.get("include_unverified") === "1";
  const form = h(
    "form",
    {
      onsubmit: (ev) => {
        ev.preventDefault();
        const p = formParams(ev.target);
        go("claims", p.include_unverified ? { include_unverified: "1" } : {});
      },
    },
    checkbox("include_unverified", "include claims whose trace source is not confirmed", include),
    h("button", { class: "act", type: "submit", text: "Show" }),
  );
  root.append(form, h("p", { class: "muted", text: "Loading (this re-checks every source)..." }));
  const r = await api("/api/claims", { include_unverified: include });
  root.lastChild.remove();
  const d = r.data;
  root.append(...messages(r));
  if (!d.claims.length) root.append(h("p", { text: "no claims" }));
  for (const row of d.claims) {
    root.append(
      h(
        "div",
        { class: "card" },
        h("div", {}, h("span", { class: "tag", text: row.evidence_status }), h("strong", { text: row.name })),
        h("div", { class: "note", text: row.observations + " observation(s), " + row.still_confirmed + " still confirmed, " + row.source_fingerprints.length + " code version(s); source check: " + row.status + "; claim record field validation: " + row.validation }),
        h("details", {}, h("summary", { text: "Evidence on both sides" }), renderAssessment(row.assessment)),
      ),
    );
  }
  for (const o of d.orphaned_reviews || []) {
    root.append(h("p", { class: "banner warn", text: "orphaned review #" + o.id + ": its claim is not in the graph" }));
  }
  root.append(
    h("h2", { text: "What the statuses mean" }),
    h("ul", {}, Object.entries(d.status_meanings).map(([k, v]) => h("li", {}, h("span", { class: "tag", text: k }), v))),
    h("p", { class: "note", text: "date used for expiry: " + d.date_used + " (UTC)" }),
  );
}

async function viewNode(root, params) {
  const form = h(
    "form",
    {
      onsubmit: (ev) => {
        ev.preventDefault();
        const p = formParams(ev.target);
        const out = { id: p.id };
        for (const k of ["incoming", "transitive", "include_unverified"]) if (p[k]) out[k] = "1";
        go("node", out);
      },
    },
    field("id", "node id", params.get("id") || "", { size: 46 }),
    checkbox("incoming", "incoming", params.get("incoming") === "1"),
    checkbox("transitive", "transitive", params.get("transitive") === "1"),
    checkbox("include_unverified", "include unverified", params.get("include_unverified") === "1"),
    h("button", { class: "act", type: "submit", text: "Show" }),
  );
  root.append(form);
  if (!params.get("id")) return;
  const r = await api("/api/node", Object.fromEntries(params));
  const d = r.data;
  root.append(
    ...messages(r),
    h("h2", { text: d.node.kind + ": " + d.node.name }),
    h("p", { class: "note", text: "source check: " + d.status }),
    h("table", {}, h("tbody", {}, scalarRows(d.node))),
    jsonBlock("Full record", d.node),
    h("h2", { text: "Reached (" + d.reached.length + ")" }),
    table(
      ["depth", "via", "kind", "node", "name", "source check"],
      d.reached.map((x) =>
        h("tr", {}, h("td", { text: x.depth }), h("td", { text: x.path.length ? x.path[x.path.length - 1].kind : "-" }), h("td", { text: x.node.kind }), h("td", {}, nodeLink(x.node.id)), h("td", { text: x.node.name }), h("td", { text: x.status })),
      ),
    ),
  );
}

function renderRecall(root, r) {
  const d = r.data;
  root.append(...messages(r));
  root.append(h("h2", { text: "Claims (" + d.claims.length + ")" }));
  for (const item of d.claims) {
    const a = item.assessment;
    root.append(
      h(
        "div",
        { class: "card" },
        h("div", {}, h("span", { class: "tag", text: a.status }), h("strong", { text: a.name })),
        ...item.retrieved_via.map((v) => h("div", { class: "note", text: "retrieved via " + v.doc_id + " (similarity " + v.similarity.toFixed(3) + ")" })),
        h("details", {}, h("summary", { text: "Evidence on both sides" }), renderAssessment(a)),
      ),
    );
  }
  root.append(h("h2", { text: "Raw experiences without claims (" + d.experiences_without_claims.length + ")" }));
  for (const x of d.experiences_without_claims) {
    root.append(
      h(
        "div",
        { class: "card" },
        h("div", {}, h("span", { class: "tag", text: x.kind }), runLink(x.run_id), " step " + x.step_id + " attempt " + x.attempt_no + " " + x.capability),
        h("div", { class: "note", text: x.label + "; retrieved via similarity " + x.similarity.toFixed(3) }),
        h("details", {}, h("summary", { text: "Excerpt (untrusted data)" }), h("pre", { text: x.excerpt })),
      ),
    );
  }
  root.append(h("h2", { text: "Not confirmed (" + d.unconfirmed.length + "), never offered as support" }));
  for (const u of d.unconfirmed) {
    root.append(h("p", {}, h("span", { class: "tag", text: "NOT CONFIRMED" }), runLink(u.run_id), " step " + u.step_id + " attempt " + u.attempt_no + " (" + u.reason + ")"));
  }
  if (d.message) root.append(h("p", { text: d.message }));
  if (d.dropped_weak) root.append(h("p", { class: "note", text: d.dropped_weak + " weak match(es) below " + d.min_similarity + " dropped" }));
  for (const n of d.notes) root.append(h("p", { class: "note", text: n }));
}

function renderSearch(root, r) {
  const d = r.data;
  root.append(
    ...messages(r),
    d.hits.length
      ? table(
          ["similarity", "run", "step", "attempt", "capability", "result", "basis"],
          d.hits.map((x) => h("tr", {}, h("td", { text: x.similarity.toFixed(3) }), h("td", {}, runLink(x.run_id)), h("td", { text: x.step_id }), h("td", { text: x.attempt_no }), h("td", { text: x.capability }), h("td", { text: x.result_status }), h("td", { text: x.basis || "-" }))),
        )
      : null,
  );
  for (const u of d.unverified) root.append(h("p", {}, h("span", { class: "tag", text: "NOT CONFIRMED" }), runLink(u.run_id), " step " + u.step_id + " (" + u.reason + ")"));
  if (d.message) root.append(h("p", { text: d.message }));
  if (d.dropped_weak) root.append(h("p", { class: "note", text: d.dropped_weak + " weak match(es) below " + d.min_similarity + " dropped" }));
  root.append(h("p", { class: "note", text: d.note }));
}

function queryView(path, render, intro) {
  return async (root, params) => {
    const form = h(
      "form",
      {
        onsubmit: (ev) => {
          ev.preventDefault();
          go(path, formParams(ev.target));
        },
      },
      field("q", "query", params.get("q") || ""),
      field("k", "k", params.get("k") || "5", { size: 3 }),
      field("min_similarity", "min similarity", params.get("min_similarity") || "", { size: 5, placeholder: "default" }),
      h("button", { class: "act", type: "submit", text: "Search" }),
    );
    root.append(form, h("p", { class: "note", text: intro }));
    if (!params.get("q")) return;
    root.append(h("p", { class: "muted", text: "Searching (the first query loads the embedding model and can take a while)..." }));
    const r = await api("/api/" + path, Object.fromEntries(params));
    root.lastChild.remove();
    render(root, r);
  };
}

async function viewGraph(root) {
  const s = await api("/api/status");
  if (s.data.inputs.root.state !== "present") {
    root.append(h("p", { class: "banner warn", text: "The dependency graph needs --root (an application directory)." }));
    return;
  }
  root.append(
    h("p", { class: "note", text: "Dependency graph of the application as it is now (edge A to B means A depends on B). Shown in a sandboxed frame." }),
    h("iframe", { src: "/api/graph.html", sandbox: "", title: "Dependency graph" }),
  );
}

const VIEWS = {
  overview: viewOverview,
  traces: viewTraces,
  claims: viewClaims,
  node: viewNode,
  recall: queryView("recall", renderRecall, "Past experience by meaning. Claims keep their status and the records on both sides; similarity says how the query matched, not how true a claim is."),
  search: queryView("search", renderSearch, "Past steps similar to the query. Similarity is not evidence of truth."),
  graph: viewGraph,
};

async function render() {
  const mine = ++generation;
  const raw = window.location.hash.replace(/^#/, "") || "overview";
  const [tab, query] = raw.split("?");
  const params = new URLSearchParams(query || "");
  const name = VIEWS[tab] ? tab : "overview";
  tabsEl.replaceChildren(
    ...TABS.map(([id, label]) =>
      h("button", { type: "button", role: "tab", "aria-selected": id === name ? "true" : "false", onclick: () => go(id), text: label }),
    ),
  );
  const root = document.createElement("div");
  view.replaceChildren(root);
  try {
    await VIEWS[name](root, params);
  } catch (e) {
    if (mine === generation) root.append(h("div", { class: "banner error", role: "alert", text: e.message }));
  }
  if (mine !== generation) root.remove();
}

window.addEventListener("hashchange", render);
render();
