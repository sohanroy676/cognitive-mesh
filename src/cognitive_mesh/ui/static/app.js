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

// ---- pins: ids kept in this browser only (ADR-0032 point 5) ---------------------------------
const PIN_KEY = "cogmesh.pins.v1";
const FOLDER_KEY = "cogmesh.folder.v1";
const MAX_PINS = 200;

function readStore(key) {
  try {
    return window.localStorage.getItem(key);
  } catch (e) {
    return null;
  }
}

function writeStore(key, value) {
  try {
    window.localStorage.setItem(key, value);
  } catch (e) {
    // storage is unavailable (private window, blocked site data): the page still works
  }
}

const validPin = (p) => p && typeof p.id === "string" && p.id.length > 0 && p.id.length <= 2000 && (p.space === "deps" || p.space === "exp");

function loadPins() {
  const raw = readStore(PIN_KEY);
  if (!raw) return [];
  try {
    const v = JSON.parse(raw);
    return Array.isArray(v) ? v.filter(validPin).slice(-MAX_PINS) : [];
  } catch (e) {
    return [];
  }
}

let pinned = loadPins();
const pins = {
  list: () => pinned.slice(),
  has: (space, id) => pinned.some((p) => p.space === space && p.id === id),
  find: (id) => pinned.find((p) => p.id === id) || null,
  remove(space, id) {
    pinned = pinned.filter((p) => !(p.space === space && p.id === id));
    writeStore(PIN_KEY, JSON.stringify(pinned));
  },
  toggle(entry) {
    if (pins.has(entry.space, entry.id)) {
      pins.remove(entry.space, entry.id);
      return;
    }
    pinned.push({ space: entry.space, id: entry.id, label: entry.label || entry.id, folder: entry.folder || null, pinned_at: new Date().toISOString() });
    if (pinned.length > MAX_PINS) pinned = pinned.slice(-MAX_PINS);
    writeStore(PIN_KEY, JSON.stringify(pinned));
  },
};
const folderMemory = { get: () => readStore(FOLDER_KEY) || "", set: (v) => writeStore(FOLDER_KEY, v) };

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

function openPin(p) {
  if (p.space === "deps") go("graph", { path: p.folder || "", focus: p.id });
  else go("node", { id: p.id });
}

// A pinned id is marked, and links back to where it was pinned, wherever it appears again.
function idLink(p) {
  return h("span", { class: "pinned-id" }, h("span", { class: "badge", text: "pinned" }), link(p.id, () => openPin(p)));
}

function mentions(text) {
  return String(text)
    .split(/(\s+)/)
    .map((tok) => {
      const p = tok.trim() ? pins.find(tok) : null;
      return p ? idLink(p) : tok;
    });
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

function recordItem(r) {
  return h("li", {}, h("span", {}, r.before, r.runId ? runLink(r.runId) : null, r.after || null), r.sub ? h("span", { class: "sub", text: r.sub }) : null);
}

function side(kind, title, items) {
  const empty = kind === "s" ? "Nothing found that supports this claim." : "Nothing found that contradicts this claim.";
  return h(
    "section",
    { class: "side " + (kind === "s" ? "pro" : "con") },
    h("h3", {}, title, tally(kind, items.length), h("span", { class: "muted", text: plural(items.length, "record", "records") })),
    h("ul", { class: "recs" }, items.length ? items.map(recordItem) : [h("li", { class: "none", text: empty })]),
  );
}

// A short key to read the page by. It is open the first time and remembers when it is closed.
const HELP_KEY = "cogmesh.help.v1";
function helpPanel() {
  const box = h("details", { class: "helpbox", open: readStore(HELP_KEY) === "closed" ? null : true },
    h("summary", { text: "How to read this page" }),
    h("ul", { class: "plain" }, window.CogExplain.KEY.map((line) => h("li", { text: line }))));
  box.addEventListener("toggle", () => writeStore(HELP_KEY, box.open ? "open" : "closed"));
  return box;
}

function ledger(a, via) {
  const X = window.CogExplain;
  const { pro, con, neither } = X.records(a);
  const say = X.headline(a);
  const inWords = X.aboutClaim(a.name);
  const classes = "ledger" + (revealed ? "" : " reveal");
  const el = h(
    "article",
    { class: classes },
    h(
      "div",
      { class: "ledger-head" },
      h("p", { class: "claim-text" }, mentions(a.name)),
      inWords ? h("p", { class: "inwords", text: "In words: " + inWords + "." }) : null,
      ...(via || []).map((v) => h("p", { class: "via", text: "Found by search on " + v.doc_id + " (query match " + v.similarity.toFixed(3) + ")" })),
    ),
    h("div", { class: "sides" }, side("s", "Supports", pro), side("c", "Contradicts", con)),
    h(
      "div",
      { class: "total" },
      chip(a.status),
      h("p", { class: "headline", text: say.text }),
      say.next ? h("p", { class: "note", text: say.next }) : null,
      h("p", { class: "note", text: X.currentCodeNote(a) }),
    ),
    h(
      "div",
      { class: "fine" },
      neither.length ? h("details", {}, h("summary", { text: "Records that speak for neither side (" + neither.length + ")" }), h("ul", { class: "recs" }, neither.map(recordItem))) : null,
      h("details", {}, h("summary", { text: "How this status was reached" }),
        h("ul", { class: "plain" }, X.reading(a).filter(Boolean).map((line) => h("li", { text: line }))),
        h("p", { class: "note", text: "Formal meaning of “" + a.status.replaceAll("_", " ") + "”: " + a.meaning }),
        jsonBlock("Raw data, for checking", { corroboration: a.corroboration, outcome: a.outcome, counts: a.counts || {}, sources: a.sources || [] })),
      h("p", {}, link("Explore this claim in the graph", () => go("node", { id: a.claim_id })), " ", link("Show runs for this claim", () => go("traces", { for: a.claim_id }))),
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
    pins.list().length
      ? h("div", {}, h("h2", { text: "Pinned ids" }), h("ul", { class: "recs" }, pins.list().map((p) => h("li", {}, idLink(p), h("span", { class: "sub", text: p.space === "deps" ? "dependency graph node" + (p.folder ? " in " + p.folder : "") : "experience node" })))))
      : null,
    h("p", { class: "note", text: "Version " + r.data.version + "." }),
    ...(r.warnings || []).map((m) => h("div", { class: "banner warn", role: "status", text: m })),
  );
}

const ROLE_TEXT = {
  supports: "listed this claim",
  contradicts: "listed the subject without the claim",
  ran: "touched it",
};

function historyForm(value) {
  return h("form", {
    onsubmit: (ev) => {
      ev.preventDefault();
      const v = formParams(ev.target)["for"].trim();
      go("traces", v ? { for: v } : {});
    },
  },
  field("for", "Show every run that touched an id", value, { size: 52, placeholder: "A module, claim, target or attempt id" }),
  h("button", { class: "act", type: "submit", text: "Show runs" }));
}

async function renderHistory(root, id) {
  const r = await api("/api/history", { id });
  const d = r.data;
  const exp = d.found && d.node.kind !== null;
  root.append(
    h("p", {}, link("All runs", () => go("traces"))),
    h("h2", { text: "Runs that touched this id" }),
    h("p", { class: "claim-text" }, mentions(d.node.name)),
    d.found ? h("p", { class: "note", text: d.node.kind + (d.status ? ", status " + d.status.replaceAll("_", " ") : "") + ". " + d.node.id }) : null,
    ...messages(r),
  );
  if (exp) {
    // A target is a dependency-graph id, so it is pinned as one; everything else is an experience id.
    const entry = d.node.kind === "target"
      ? { space: "deps", id: d.node.name, label: d.node.name }
      : { space: "exp", id: d.node.id, label: d.node.name };
    const pinBtn = h("button", { class: "act small ghost", type: "button", text: pins.has(entry.space, entry.id) ? "Unpin this id" : "Pin this id" });
    pinBtn.addEventListener("click", () => {
      pins.toggle(entry);
      pinBtn.textContent = pins.has(entry.space, entry.id) ? "Unpin this id" : "Pin this id";
    });
    root.append(h("p", {}, pinBtn));
  }
  if (!d.found || !d.rows.length) {
    root.append(h("p", { text: "No recorded run mentions this id." + (d.found ? "" : " It is not in the experience graph. Build the graph from your runs with: cognitive-mesh experience build") }));
  } else {
    if (d.claims.length) {
      root.append(h("h2", { text: "Claims about it (" + d.claims.length + ")" }), h("ul", { class: "recs" }, d.claims.map((c) => h("li", {}, link(c.name, () => go("traces", { for: c.id }))))));
    }
    root.append(
      h("h2", { text: plural(d.total_runs, "run", "runs") + ", newest first" }),
      table(
        ["Run", "Started", "Run status", "What it said", "Capability", "Code version", "Source check"],
        d.rows.map((x) =>
          h("tr", {}, h("td", {}, runLink(x.run_id)), h("td", { text: x.started_at || "unreadable" }), h("td", { text: x.run_status }),
            h("td", { text: ROLE_TEXT[x.role] + (x.role === "contradicts" ? (x.same_version ? " (same code version)" : " (another code version)") : "") }),
            h("td", { text: x.capability || "-" }), h("td", { class: "mono", text: x.code_version ? x.code_version.slice(0, 12) : "unrecorded" }), h("td", { text: x.source_check }))),
      ),
    );
    if (d.truncated) root.append(h("p", { class: "banner warn", text: "Showing the newest runs only; " + d.total_runs + " runs touched this id." }));
  }
}

async function viewTraces(root, params) {
  if (params.get("for")) {
    root.append(historyForm(params.get("for")));
    await renderHistory(root, params.get("for"));
    return;
  }
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
    historyForm(""),
    pins.list().length ? h("p", { class: "note" }, "Pinned: ", ...pins.list().flatMap((p) => [link(p.id, () => go("traces", { for: p.id })), " "])) : null,
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
    helpPanel(),
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
    h("p", { class: "claim-text" }, mentions(d.node.name)),
    h("p", { class: "note", text: d.node.kind + ", source check " + d.status }),
    h("p", {}, link("Show runs for this node", () => go("traces", { for: d.node.id }))),
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

function queryView(path, title, button, render, intro) {
  return async (root, params) => {
    root.append(
      h("h2", { text: title }),
      h("p", { class: "prose note", text: intro }),
      path === "recall" ? helpPanel() : null,
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

const VIEWS = {
  overview: viewOverview,
  claims: viewClaims,
  recall: queryView("recall", "Recall past experience", "Recall", renderRecall, "Finds claims by meaning. Each claim keeps its status and the records on both sides. A query match says how the words matched, not how true a claim is."),
  traces: viewTraces,
  node: viewNode,
  graph: (root, params) => graphView(root, params),
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

const graphView = window.CogGraph.view({ h, api, go, pins, folder: folderMemory, plural, messages, rerender: () => render() });

window.addEventListener("hashchange", render);
render();
