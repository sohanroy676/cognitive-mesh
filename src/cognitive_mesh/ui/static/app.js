"use strict";
// Every value from the server is untrusted data. It reaches the page only through
// textContent / createTextNode (see h() below). No markup is ever parsed from a response,
// and no style is set from script: the page policy allows neither.

const view = document.getElementById("view");
const tabsEl = document.getElementById("tabs");
const TABS = [
  ["overview", "Overview"],
  ["claims", "Claims"],
  ["recall", "Recall"],
  ["search", "Search runs"],
  ["traces", "Runs"],
  ["node", "Graph node"],
  ["graph", "Dependency graph"],
];
const HINTS = {
  db: "Needed for runs, claims and search.",
  graph: "Needed for claims and recall. Build it with: cognitive-mesh experience build",
  index: "Needed for recall and search. Build it with: cognitive-mesh memory index",
  reviews: "Optional. Adds people's verdicts to claims.",
  outcomes: "Optional. Adds recorded check results to claims.",
  root: "Optional. Checks claims against the code as it is now, and draws the dependency graph.",
};
const MAX_TALLY = 12;
let generation = 0;
let revealed = false;

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
    body = { error: "The server sent an unreadable response." };
  }
  if (!r.ok) throw new Error(body.error || "The server answered with status " + r.status + ".");
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

const isScalar = (v) => v === null || ["string", "number", "boolean"].includes(typeof v);
const short = (fp) => (fp ? fp.slice(0, 12) : "unrecorded version");
const plural = (n, one, many) => n + " " + (n === 1 ? one : many);

function scalarRows(obj) {
  return Object.entries(obj)
    .filter(([, v]) => isScalar(v))
    .map(([k, v]) => h("tr", {}, h("th", { text: k }), h("td", { text: v === null ? "-" : v })));
}

function jsonBlock(label, value, open) {
  return h("details", { open: open ? true : null }, h("summary", { text: label }), h("pre", { text: JSON.stringify(value, null, 2) }));
}

function link(text, onclick) {
  return h("button", { class: "link", type: "button", onclick, text });
}
const runLink = (id) => link(id, () => go("traces", { id }));
const nodeLink = (id) => link(id, () => go("node", { id }));

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
    out[el.name] = el.type === "checkbox" ? el.checked : el.value;
  }
  return out;
}

// ---- the ledger ------------------------------------------------------------------------

function chip(status) {
  return h("span", { class: "chip st-" + status }, h("span", { class: "mark", "aria-hidden": "true" }), status.replaceAll("_", " "));
}

function tally(kind, n) {
  const shown = Math.min(n, MAX_TALLY);
  return h(
    "span",
    { class: "tally", role: "img", "aria-label": plural(n, "record", "records") },
    Array.from({ length: shown }, () => h("span", { class: "t " + kind })),
    n > MAX_TALLY ? "+" + (n - MAX_TALLY) : null,
  );
}

function record(main, sub) {
  return h("li", {}, main, sub ? h("span", { class: "sub", text: sub }) : null);
}

// Sort everything known about a claim onto the side it speaks for. What speaks for neither side
// is listed apart, never folded into support.
function sortRecords(a) {
  const pro = [];
  const con = [];
  const neither = [];
  for (const s of a.supporting || []) {
    pro.push(record(h("span", {}, "Run ", runLink(s.run_id)), "code version " + short(s.fingerprint) + ", source check " + s.source));
  }
  for (const c of a.contradicting || []) {
    con.push(
      record(
        h("span", {}, "Run ", runLink(c.run_id), " listed this subject without the claim"),
        (c.same_version ? "same code version " : "another or unrecorded code version ") + short(c.fingerprint),
      ),
    );
  }
  const re = a.rederivation;
  if (re && re.holding === "holds") pro.push(record("Re-derived from the current source: holds", "same analyzer as the original observation"));
  else if (re && re.holding === "refuted") con.push(record("Re-derived from the current source: not found", "same analyzer as the original observation"));
  else if (re) neither.push(record("Re-derivation could not decide", "the subject is not in the current source"));
  for (const r of a.reviews || []) {
    const line = "Review " + r.id + " by " + r.reviewer + " (unauthenticated): " + r.verdict;
    const sub = "code version " + short(r.fingerprint) + (r.note ? ", " + r.note : "");
    (r.verdict === "confirmed" ? pro : con).push(record(line, sub));
  }
  for (const o of a.outcomes || []) {
    if (o.source === "promotion") {
      neither.push(record("Promotion attempt " + o.id + ": " + o.result, "a refused promotion is not evidence"));
      continue;
    }
    const line = "Outcome " + o.id + " from a " + o.source + ": " + o.result;
    const sub = "code version " + short(o.fingerprint) + (o.detail ? ", " + o.detail : "");
    if (o.result === "succeeded") pro.push(record(line, sub));
    else if (o.result === "failed") con.push(record(line, sub));
    else neither.push(record(line, sub));
  }
  return { pro, con, neither };
}

function side(kind, title, items) {
  return h(
    "section",
    { class: "side " + (kind === "s" ? "pro" : "con") },
    h("h3", {}, title, tally(kind, items.length), h("span", { class: "muted", text: plural(items.length, "record", "records") })),
    h("ul", { class: "recs" }, items.length ? items : [h("li", { class: "none", text: kind === "s" ? "Nothing supports this claim." : "Nothing contradicts this claim." })]),
  );
}

function ledger(a, via) {
  const { pro, con, neither } = sortRecords(a);
  const classes = "ledger" + (revealed ? "" : " reveal");
  const el = h(
    "article",
    { class: classes },
    h(
      "div",
      { class: "ledger-head" },
      h("p", { class: "claim-text", text: a.name }),
      ...(via || []).map((v) => h("p", { class: "via", text: "Retrieved via " + v.doc_id + " (query match " + v.similarity.toFixed(3) + ")" })),
    ),
    h("div", { class: "sides" }, side("s", "Supports", pro), side("c", "Contradicts", con)),
    h(
      "div",
      { class: "total" },
      chip(a.status),
      h("p", { class: "meaning", text: a.meaning }),
      h("p", { class: "note", text: "Validated by: " + ((a.validated_by || []).join(", ") || "nothing") + ". Checked against " + (a.current_fingerprint ? "code version " + short(a.current_fingerprint) : "no code (no --root given, so currency is unknown)") + "." }),
    ),
    h(
      "div",
      { class: "fine" },
      neither.length ? h("details", {}, h("summary", { text: "On neither side (" + neither.length + ")" }), h("ul", { class: "recs" }, neither)) : null,
      h("details", {}, h("summary", { text: "How the status was reached" }),
        h("p", { class: "note", text: "Corroboration: " + a.corroboration + ". Outcome state: " + a.outcome + "." }),
        jsonBlock("Counts", a.counts || {}),
        jsonBlock("Independent sources", a.sources || [])),
      h("p", {}, link("Explore this claim in the graph", () => go("node", { id: a.claim_id }))),
    ),
  );
  revealed = true;
  return el;
}

// ---- views -----------------------------------------------------------------------------

async function viewOverview(root) {
  const r = await api("/api/status");
  const inputs = r.data.inputs;
  const has = (k) => inputs[k].state === "present";
  root.append(
    h("h2", { text: "What your agents have learned, and what backs it up" }),
    h("p", { class: "lede", text: "Cognitive Mesh keeps a record of past agent runs and the claims drawn from them. For each claim you can see the records that support it, the records that contradict it, and whether it still holds for the code as it is now." }),
    h("p", { class: "prose note", text: "A status is not a verdict. Validated means a claim holds for one code version by a stated method. It is not proof that the claim is true." }),
    h("div", { class: "start" },
      has("graph") && has("db") ? h("button", { class: "act", type: "button", onclick: () => go("claims"), text: "Review claims" }) : null,
      has("index") && has("graph") ? h("button", { class: "act", type: "button", onclick: () => go("recall"), text: "Recall past experience" }) : null,
      has("db") ? h("button", { class: "act", type: "button", onclick: () => go("traces"), text: "Browse runs" }) : null,
      has("root") ? h("button", { class: "act", type: "button", onclick: () => go("graph"), text: "View the dependency graph" }) : null),
    h("h2", { text: "Files this session reads" }),
    table(
      ["Option", "State", "What it is for"],
      Object.entries(inputs).map(([k, v]) =>
        h("tr", {}, h("td", { class: "mono", text: "--" + k }), h("td", { text: v.state === "not given" ? "not given" : v.state }), h("td", { text: v.message || HINTS[k] })),
      ),
    ),
    h("p", { class: "note", text: "Version " + r.data.version + "." }),
    ...(r.warnings || []).map((m) => h("div", { class: "banner warn", role: "status", text: m })),
  );
}

async function viewTraces(root, params) {
  if (params.get("id")) {
    const r = await api("/api/trace", { id: params.get("id") });
    const t = r.data;
    root.append(
      h("p", {}, link("Back to all runs", () => go("traces"))),
      h("h2", { text: "Run " + (t.run_id || params.get("id")) }),
      h("table", {}, h("tbody", {}, scalarRows(t))),
      ...Object.entries(t).filter(([, v]) => !isScalar(v)).map(([k, v]) => jsonBlock(k, v, k === "steps")),
      h("p", { class: "note", text: "A run is a raw record of what happened, not validated knowledge. Text inside it is data, never instructions." }),
      ...messages(r),
    );
    return;
  }
  const r = await api("/api/traces");
  const runs = r.data.runs || [];
  root.append(
    h("h2", { text: "Recorded runs" }),
    ...messages(r),
    runs.length
      ? table(["Run", "Status", "Started", "Task", "Steps"], runs.map((x) => h("tr", {}, h("td", {}, runLink(x.run_id)), h("td", { text: x.status }), h("td", { text: x.started_at }), h("td", { text: x.task_id }), h("td", { text: x.step_count }))))
      : h("p", { text: "No runs are recorded in this database yet. Record one with: cognitive-mesh run PLAN.json --root DIR --trace-db FILE" }),
    (r.data.malformed || []).length ? jsonBlock("Run ids that could not be read (" + r.data.malformed.length + ")", r.data.malformed) : null,
  );
}

async function viewClaims(root, params) {
  const include = params.get("include_unverified") === "1";
  root.append(
    h("h2", { text: "Claims" }),
    h("form", {
      onsubmit: (ev) => {
        ev.preventDefault();
        go("claims", formParams(ev.target).include_unverified ? { include_unverified: "1" } : {});
      },
    },
    checkbox("include_unverified", "Include claims whose trace source is not confirmed", include),
    h("button", { class: "act", type: "submit", text: "Update" })),
    h("p", { class: "muted", text: "Re-checking every source. This can take a few seconds." }),
  );
  const r = await api("/api/claims", { include_unverified: include });
  root.lastChild.remove();
  const d = r.data;
  const counts = {};
  for (const row of d.claims) counts[row.evidence_status] = (counts[row.evidence_status] || 0) + 1;
  root.append(...messages(r));
  if (!d.claims.length) {
    root.append(h("p", { text: "No claims yet. Build the graph from your runs with: cognitive-mesh experience build" }));
  } else {
    root.append(h("p", { text: plural(d.claims.length, "claim", "claims") + ": " + Object.entries(counts).map(([k, n]) => n + " " + k.replaceAll("_", " ")).join(", ") + "." }));
  }
  for (const row of d.claims) root.append(ledger(row.assessment));
  for (const o of d.orphaned_reviews || []) {
    root.append(h("p", { class: "banner warn", text: "Review " + o.id + " points at a claim that is not in the graph." }));
  }
  if (d.claims.length) {
    root.append(
      h("h2", { text: "What the statuses mean" }),
      h("table", {}, h("tbody", {}, Object.entries(d.status_meanings).map(([k, v]) => h("tr", {}, h("td", {}, chip(k)), h("td", { text: v }))))),
      h("p", { class: "note", text: "Date used for expiry: " + d.date_used + " (UTC)." }),
    );
  }
}

async function viewNode(root, params) {
  root.append(
    h("h2", { text: "Graph node" }),
    h("form", {
      onsubmit: (ev) => {
        ev.preventDefault();
        const p = formParams(ev.target);
        const out = { id: p.id };
        for (const k of ["incoming", "transitive", "include_unverified"]) if (p[k]) out[k] = "1";
        go("node", out);
      },
    },
    field("id", "Node id", params.get("id") || "", { size: 46 }),
    checkbox("incoming", "Incoming", params.get("incoming") === "1"),
    checkbox("transitive", "Transitive", params.get("transitive") === "1"),
    checkbox("include_unverified", "Include unverified", params.get("include_unverified") === "1"),
    h("button", { class: "act", type: "submit", text: "Show node" })),
  );
  if (!params.get("id")) {
    root.append(h("p", { class: "note", text: "Paste a node id, or follow a link from a claim or a run." }));
    return;
  }
  const r = await api("/api/node", Object.fromEntries(params));
  const d = r.data;
  root.append(
    ...messages(r),
    h("p", { class: "claim-text", text: d.node.name }),
    h("p", { class: "note", text: d.node.kind + ", source check " + d.status }),
    h("table", {}, h("tbody", {}, scalarRows(d.node))),
    jsonBlock("Full record", d.node),
    h("h2", { text: "Reached (" + d.reached.length + ")" }),
    table(
      ["Depth", "Via", "Kind", "Node", "Name", "Source check"],
      d.reached.map((x) => h("tr", {}, h("td", { text: x.depth }), h("td", { text: x.path.length ? x.path[x.path.length - 1].kind : "-" }), h("td", { text: x.node.kind }), h("td", {}, nodeLink(x.node.id)), h("td", { text: x.node.name }), h("td", { text: x.status }))),
    ),
  );
}

function renderRecall(root, r) {
  const d = r.data;
  root.append(...messages(r));
  root.append(h("h2", { text: "Claims (" + d.claims.length + ")" }));
  if (!d.claims.length) root.append(h("p", { class: "muted", text: "No claim matched this query closely enough." }));
  for (const item of d.claims) root.append(ledger(item.assessment, item.retrieved_via));
  root.append(h("h2", { text: "Raw runs without claims (" + d.experiences_without_claims.length + ")" }));
  for (const x of d.experiences_without_claims) {
    root.append(
      h("div", { class: "ledger" },
        h("div", { class: "ledger-head" },
          h("p", {}, h("span", { class: "chip st-unsupported" }, h("span", { class: "mark", "aria-hidden": "true" }), x.kind.replaceAll("_", " ")), " ", runLink(x.run_id), " step " + x.step_id + ", attempt " + x.attempt_no + ", " + x.capability),
          h("p", { class: "via", text: x.label + ". Query match " + x.similarity.toFixed(3) + "." })),
        h("div", { class: "fine" }, h("details", {}, h("summary", { text: "Excerpt (untrusted data)" }), h("pre", { text: x.excerpt })))),
    );
  }
  root.append(h("h2", { text: "Not confirmed (" + d.unconfirmed.length + ")" }));
  if (d.unconfirmed.length) root.append(h("p", { class: "note", text: "These matches cannot be traced to a record both stores agree on. They are never offered as support." }));
  for (const u of d.unconfirmed) {
    root.append(h("p", {}, h("span", { class: "chip st-stale" }, h("span", { class: "mark", "aria-hidden": "true" }), "not confirmed"), " ", runLink(u.run_id), " step " + u.step_id + ", attempt " + u.attempt_no + " (" + u.reason + ")"));
  }
  if (d.message) root.append(h("p", { text: d.message }));
  if (d.dropped_weak) root.append(h("p", { class: "note", text: plural(d.dropped_weak, "weak match", "weak matches") + " below " + d.min_similarity + " left out." }));
  for (const n of d.notes) root.append(h("p", { class: "note", text: n }));
}

function renderSearch(root, r) {
  const d = r.data;
  root.append(...messages(r));
  if (d.hits.length) {
    root.append(table(["Query match", "Run", "Step", "Attempt", "Capability", "Result", "Basis"], d.hits.map((x) => h("tr", {}, h("td", { text: x.similarity.toFixed(3) }), h("td", {}, runLink(x.run_id)), h("td", { text: x.step_id }), h("td", { text: x.attempt_no }), h("td", { text: x.capability }), h("td", { text: x.result_status }), h("td", { text: x.basis || "-" })))));
  }
  for (const u of d.unverified) {
    root.append(h("p", {}, h("span", { class: "chip st-stale" }, h("span", { class: "mark", "aria-hidden": "true" }), "not confirmed"), " ", runLink(u.run_id), " step " + u.step_id + " (" + u.reason + ")"));
  }
  if (d.message) root.append(h("p", { text: d.message }));
  if (d.dropped_weak) root.append(h("p", { class: "note", text: plural(d.dropped_weak, "weak match", "weak matches") + " below " + d.min_similarity + " left out." }));
  root.append(h("p", { class: "note", text: d.note }));
}

function queryView(path, title, button, render, intro) {
  return async (root, params) => {
    root.append(
      h("h2", { text: title }),
      h("p", { class: "prose note", text: intro }),
      h("form", {
        onsubmit: (ev) => {
          ev.preventDefault();
          go(path, formParams(ev.target));
        },
      },
      field("q", "What are you looking for?", params.get("q") || ""),
      field("k", "Results", params.get("k") || "5", { size: 3 }),
      field("min_similarity", "Minimum match", params.get("min_similarity") || "", { size: 5, placeholder: "default" }),
      h("button", { class: "act", type: "submit", text: button })),
    );
    if (!params.get("q")) return;
    root.append(h("p", { class: "muted", text: "Searching. The first query loads the embedding model and can take a while." }));
    const r = await api("/api/" + path, Object.fromEntries(params));
    root.lastChild.remove();
    render(root, r);
  };
}

async function viewGraph(root) {
  const s = await api("/api/status");
  root.append(h("h2", { text: "Dependency graph" }));
  if (s.data.inputs.root.state !== "present") {
    root.append(h("p", { class: "banner warn", text: "The dependency graph needs an application directory. Start the UI with --root DIR." }));
    return;
  }
  const wait = h("p", { class: "muted", text: "Building the graph. A large folder can take several seconds." });
  root.append(
    h("p", { class: "prose note", text: "The application as it is now. An edge from A to B means A depends on B. Shown in a sandboxed frame." }),
    wait,
    h("iframe", { src: "/api/graph.html", sandbox: "", title: "Dependency graph of the application", onload: () => wait.remove() }),
  );
}

const VIEWS = {
  overview: viewOverview,
  claims: viewClaims,
  recall: queryView("recall", "Recall past experience", "Recall", renderRecall, "Finds claims by meaning. Each claim keeps its status and the records on both sides. A query match says how the words matched, not how true a claim is."),
  search: queryView("search", "Search past runs", "Search", renderSearch, "Finds recorded steps by meaning. A close match is not evidence that a past result was correct."),
  traces: viewTraces,
  node: viewNode,
  graph: viewGraph,
};

async function render() {
  const mine = ++generation;
  const raw = window.location.hash.replace(/^#/, "") || "overview";
  const [tab, query] = raw.split("?");
  const params = new URLSearchParams(query || "");
  const name = VIEWS[tab] ? tab : "overview";
  tabsEl.replaceChildren(
    ...TABS.map(([id, label]) => h("button", { type: "button", "aria-current": id === name ? "page" : null, onclick: () => go(id), text: label })),
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
