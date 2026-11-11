// Chat page (ADR-0044). The only page that writes, and only through approved actions.
"use strict";

const root = document.getElementById("root");
const view = { state: null, session: null, turns: [], sig: new Map(), timer: null };

function store(key, value) {
  try {
    if (value === undefined) return sessionStorage.getItem(key);
    sessionStorage.setItem(key, value);
  } catch { /* storage may be blocked; the page works without it */ }
  return null;
}

async function boot() {
  await topBar("chat");
  await refresh();
  const saved = Number(store("cogmesh.session"));
  if (saved) await openSession(saved, true);
  if (view.session === null) {
    const all = await api("/api/sessions");
    if (all.sessions.length) await openSession(all.sessions[0].id, true);
  }
  draw();
  loadGraph();
}

async function refresh() {
  view.state = await api("/api/state");
}

// The dependency analysis reads every file of every project and can take a long time, so the
// project list is drawn first and this section fills in when the analysis answers.
function paintEdges() {
  const box = view.edgeBox;
  if (!box) return;
  clear(box);
  if (!view.state.projects.length) return;
  if (view.graphError) {
    box.append(h("p", { class: "notice", role: "alert" },
      "The projects are saved, but their files could not be analysed: " + view.graphError));
  } else if (view.graph) {
    box.append(edgeList(view.graph));
  } else {
    box.append(h("p", { class: "muted" }, "Reading the projects' files to find dependencies… you can keep working."));
  }
}

async function loadGraph() {
  view.graph = null;
  view.graphError = null;
  paintEdges();
  const seq = (view.graphSeq = (view.graphSeq || 0) + 1);
  try {
    const r = await api("/api/graph");
    if (seq !== view.graphSeq) return;
    view.graph = r.graph;
    view.graphError = r.graph_error;
  } catch (err) {
    if (seq !== view.graphSeq) return;
    view.graphError = err.message;
  }
  paintEdges();
}

async function openSession(id, quiet) {
  try {
    const s = await api("/api/session", { id });
    view.session = id;
    view.turns = s.turns;
    store("cogmesh.session", String(id));
    schedule();
  } catch (err) {
    if (!quiet) throw err;
    store("cogmesh.session", "");
  }
}

function schedule() {
  clearTimeout(view.timer);
  const busy = view.turns.some((t) => t.status === "running");
  if (!busy) return;
  view.timer = setTimeout(async () => {
    try {
      const s = await api("/api/session", { id: view.session });
      view.turns = s.turns;
      await refresh();
    } catch { /* try again on the next tick */ }
    draw();
    schedule();
  }, 1000);
}

// ---- layout -------------------------------------------------------------------------------

function draw() {
  clear(root);
  root.append(sidebar(), main());
}

function sidebar() {
  const st = view.state;
  const box = h("section", { class: "panel stack", "aria-labelledby": "projects-h" });
  box.append(h("h2", { id: "projects-h" }, "Projects"));
  if (!st.projects.length) {
    box.append(h("p", { class: "empty" },
      "Start by adding the projects that belong together: a local folder or a GitHub address."));
  }
  const list = h("ul", { class: "stack", style: null });
  list.style.listStyle = "none";
  list.style.margin = "0";
  list.style.padding = "0";
  for (const p of st.projects) {
    list.append(h("li", null,
      h("strong", null, p.name), " ", tag(p.kind), " ",
      p.changeable ? null : tag("read-only", "warn"),
      h("div", { class: "muted mono" }, cut(p.location, 60)),
      attachForm(p)));
  }
  box.append(list, addProjectForm());
  view.edgeBox = h("div", { class: "stack", "aria-live": "polite" });
  box.append(view.edgeBox);
  paintEdges();
  if (st.projects.length >= 2) box.append(linkForm(st.projects));
  box.append(startForm());
  return box;
}

function addProjectForm() {
  const src = h("input", { type: "text", id: "add-src", placeholder: "C:\\work\\api or https://github.com/owner/repo" });
  const name = h("input", { type: "text", id: "add-name", placeholder: "name (optional)" });
  const msg = h("p", { class: "muted", role: "status" });
  const go = h("button", { type: "button", onclick: async () => {
    msg.textContent = "Adding…";
    try {
      const r = await post("/api/projects/add", { source: src.value, name: name.value || null });
      msg.textContent = "Added " + r.added + ".";
      await refresh();
      draw();
      loadGraph();
    } catch (err) { msg.textContent = err.message; }
  } }, "Add project");
  return h("form", { class: "stack", onsubmit: (e) => e.preventDefault() },
    h("div", null, h("label", { for: "add-src" }, "Folder or GitHub address"), src),
    h("div", null, h("label", { for: "add-name" }, "Name"), name), go, msg);
}

// A new folder or GitHub project that belongs to an existing project: it is stored in the
// workspace database and linked to its parent, so the dependency graph shows it.
function attachForm(parent) {
  const id = "att-" + parent.name.replace(/[^A-Za-z0-9_-]/g, "_");
  const src = h("input", { type: "text", id, placeholder: "folder path or GitHub address" });
  const msg = h("p", { class: "muted", role: "status" });
  const go = h("button", { type: "button", onclick: async () => {
    msg.textContent = "Adding…";
    try {
      const r = await post("/api/projects/attach", { parent: parent.name, source: src.value });
      msg.textContent = "Added " + r.added + " to " + parent.name + ".";
      await refresh();
      draw();
      loadGraph();
    } catch (err) { msg.textContent = err.message; }
  } }, "Add to " + parent.name);
  return h("details", null, h("summary", null, "Add a folder or project to " + parent.name),
    h("form", { class: "stack", onsubmit: (e) => e.preventDefault() },
      h("label", { for: id }, "Folder or GitHub address"), src, go, msg));
}

function edgeList(graph) {
  const wrap = h("div", { class: "stack" },
    h("h3", null, "Dependencies found (" + graph.edges.length + ")"));
  if (!graph.edges.length) {
    wrap.append(h("p", { class: "muted" },
      "None detected between these projects yet. Declare a link below if you know of one."));
  }
  for (const e of graph.edges.slice(0, 40)) {
    wrap.append(h("div", null,
      h("span", { class: "mono" }, e.source + " → " + e.target), " ",
      tag(e.kind), " ", e.declared ? tag("declared by you", "warn") : tag("detected", "ok")));
  }
  if (graph.unresolved.length) {
    wrap.append(h("p", { class: "muted" }, graph.unresolved.length +
      " reference(s) could not be matched to a project; the graph page lists them."));
  }
  return wrap;
}

function linkForm(projects) {
  const opts = () => projects.map((p) => h("option", { value: p.name }, p.name));
  const from = h("select", { id: "l-from" }, opts());
  const to = h("select", { id: "l-to" }, opts());
  if (projects.length > 1) to.selectedIndex = 1;
  const kind = h("input", { type: "text", id: "l-kind", value: "uses" });
  const note = h("input", { type: "text", id: "l-note", placeholder: "why you know this" });
  const msg = h("p", { class: "muted", role: "status" });
  const go = h("button", { type: "button", onclick: async () => {
    try {
      await post("/api/projects/link", { source: from.value, target: to.value, kind: kind.value, note: note.value });
      await refresh();
      draw();
      loadGraph();
    } catch (err) { msg.textContent = err.message; }
  } }, "Declare link");
  return h("details", null, h("summary", null, "Declare a dependency we cannot see"),
    h("form", { class: "stack", onsubmit: (e) => e.preventDefault() },
      h("div", null, h("label", { for: "l-from" }, "This project"), from),
      h("div", null, h("label", { for: "l-to" }, "depends on"), to),
      h("div", null, h("label", { for: "l-kind" }, "Kind"), kind),
      h("div", null, h("label", { for: "l-note" }, "Note"), note), go, msg));
}

function startForm() {
  const changeable = view.state.projects.filter((p) => p.changeable);
  if (!changeable.length) return h("p", { class: "muted" }, "Add a local folder to start a chat about it.");
  const pick = h("select", { id: "start-project" },
    changeable.map((p) => h("option", { value: p.name }, p.name)));
  const msg = h("p", { class: "muted", role: "status" });
  const go = h("button", { type: "button", class: "primary", onclick: async () => {
    try {
      const r = await post("/api/projects/confirm", { project: pick.value });
      await openSession(r.session);
      draw();
    } catch (err) { msg.textContent = err.message; }
  } }, "Confirm dependencies and start");
  return h("form", { class: "stack", onsubmit: (e) => e.preventDefault() },
    h("h3", null, "Ready?"),
    h("div", null, h("label", { for: "start-project" }, "Project to change"), pick),
    h("p", { class: "muted" }, "Other projects are only read. Only this one can change."), go, msg);
}

function main() {
  const section = h("section", { class: "stack", "aria-label": "Conversation" });
  const st = view.state;
  if (!st.model.configured) {
    section.append(h("p", { class: "notice" },
      "No model is configured. Start the server with --catalog and --conductor to send prompts. You can still add projects and look at the graph and data."));
  }
  if (view.session === null) {
    section.append(h("div", { class: "panel empty" },
      h("h2", null, "Ask for a small change"),
      h("p", null, "Once the projects are confirmed you can ask for a change of up to 3 files and 100 lines, or ask a question. Each turn shows which models were chosen and why, which other projects were read, the diff, and what it was checked against.")));
    return section;
  }
  for (const turn of view.turns) section.append(turnCard(turn));
  section.append(askBox());
  return section;
}

function askBox() {
  const text = h("textarea", { id: "ask", placeholder: "Describe the change or ask a question", maxlength: 4000 });
  const memory = h("input", { type: "checkbox", id: "mem", checked: true });
  const modeBox = h("fieldset", { class: "row", style: null },
    h("legend", { class: "muted" }, "What should happen"),
    h("label", null, h("input", { type: "radio", name: "mode", value: "change", checked: true }), " Make a small change"),
    h("label", null, h("input", { type: "radio", name: "mode", value: "ask" }), " Only answer a question"));
  modeBox.style.border = "0"; modeBox.style.padding = "0"; modeBox.style.margin = "0";
  const msg = h("p", { class: "muted", role: "status" });
  const send = h("button", { type: "button", class: "primary", onclick: async () => {
    if (!text.value.trim()) { msg.textContent = "Write what you want first."; return; }
    send.disabled = true;
    try {
      await post("/api/chat/send", { session: view.session, text: text.value, memory: memory.checked,
        mode: modeBox.querySelector("input[name=mode]:checked").value });
      await openSession(view.session);
      draw();
    } catch (err) { msg.textContent = err.message; send.disabled = false; }
  } }, "Send");
  return h("form", { class: "ask-box stack", onsubmit: (e) => e.preventDefault() },
    h("label", { for: "ask" }, "Your prompt"), text, modeBox,
    h("div", { class: "row" }, send,
      h("label", { for: "mem", style: null }, memory, " Let the coder see earlier experience")), msg);
}

// ---- one turn -----------------------------------------------------------------------------

const STATUS_KIND = { done: "ok", failed: "bad", rejected: "warn", interrupted: "bad", running: "mark", awaiting_approval: "mark" };

// What each capability does, in plain words (the page's own text, not the model's).
const CAPS = {
  explain_file: { agent: "Code explainer", task: "Summarise one file and list its main symbols" },
  dependencies_of: { agent: "Dependency analyst", task: "List what a module imports (its direct dependencies)" },
  unresolved_report: { agent: "Dependency analyst", task: "List imports that could not be matched to a file" },
  compare_dependencies: { agent: "Dependency analyst", task: "Compare two dependency lists" },
  summarise_dependents: { agent: "Cross-project reader", task: "Say how other projects use a target" },
};
const DRAFT_OUTCOME = {
  applied: "applied cleanly",
  "refused:not_a_unified_diff": "rejected: the reply was not a unified diff",
  "refused:malformed_diff": "rejected: the diff was malformed",
};

function li(text, kind) { return h("li", { class: kind || "" }, text); }

function parseOut(raw) {
  try { const o = JSON.parse(raw).output; return o === undefined ? null : o; } catch { return null; }
}

function inputRows(inputs) {
  const rows = [];
  for (const [k, v] of Object.entries(inputs || {})) {
    const val = typeof v === "string" ? v : JSON.stringify(v);
    if (k === "path") rows.push("File: " + val);
    else if (k === "node_id") rows.push("Module: " + val.replace(/^module:/, ""));
    else rows.push(k.replace(/_/g, " ") + ": " + val);
  }
  return rows.length ? rows.map((r) => h("div", { class: "mono" }, r)) : [h("span", { class: "muted" }, "none")];
}

function outputNode(s) {
  if (s.status !== "succeeded") {
    const why = s.failure_message || s.failure || (s.status === "blocked" ? "an earlier step it depends on failed" : "no result");
    return h("div", { class: "notice" }, "No result: " + why + (s.attempts > 1 ? " (after " + s.attempts + " attempts)" : ""));
  }
  const o = parseOut(s.output);
  if (o === null || typeof o !== "object") return h("pre", { class: "mono" }, cut(s.output || "(nothing recorded)", 1500));
  if (typeof o.summary === "string") {
    const syms = Array.isArray(o.symbols) && o.symbols.length ? "\n\nMain symbols: " + o.symbols.join(", ") : "";
    return said(o.summary + syms, "written by " + (s.model || "a model") + ", not verified");
  }
  if (Array.isArray(o.dependencies)) {
    if (!o.dependencies.length) return h("p", null, "No dependencies found.");
    const n = o.total ?? o.dependencies.length;
    return h("div", null,
      h("p", null, n + (n === 1 ? " direct dependency" : " direct dependencies") + (o.truncated ? " (list cut)" : "") + ":"),
      h("ul", { class: "plain" }, o.dependencies.slice(0, 30).map((d) =>
        h("li", null, h("span", { class: "mono" }, String(d.node_id).replace(/^module:/, "")), " ", tag(d.edge_kind || d.node_kind || "link")))));
  }
  if (Array.isArray(o.summaries)) {
    return said(o.summaries.map((x) => x.project + ": " + x.summary).join("\n\n"), "written by " + (s.model || "a model") + ", not verified");
  }
  return h("pre", { class: "mono" }, cut(JSON.stringify(o, null, 2), 1500));
}

function agentCard(n, title, task, chips, input, output, why) {
  return h("section", { class: "agent", "aria-label": title },
    h("header", { class: "row" }, h("span", { class: "num" }, String(n)), h("strong", null, title), chips),
    h("p", { class: "muted" }, "Task: " + task),
    h("dl", { class: "io" }, h("dt", null, "Input"), h("dd", null, input), h("dt", null, "Output"), h("dd", null, output)),
    why ? h("p", { class: "muted" }, "Why this model: " + why) : null);
}

function agentsSection(turn) {
  const evs = turn.events;
  const first = (k) => evs.find((e) => e.kind === k);
  const plan = first("plan"), analysis = first("analysis"), coder = first("coder");
  const answer = first("answer"), answerFail = first("answer_failed");
  const drafts = evs.filter((e) => e.kind === "draft");
  const cards = [];
  let n = 0;
  if (plan) {
    const d = plan.data;
    cards.push(agentCard(++n, "Conductor", "Plan the steps and choose a model for each",
      [tag(view.state.model.conductor || "model")],
      h("div", null, "Your request: " + cut(turn.text, 300)),
      h("div", { class: "stack" },
        h("ol", { class: "plain" }, d.steps.map((s) => li(s.step_id + ": " + ((CAPS[s.capability] || {}).task || s.capability)))),
        d.reasoning ? said(d.reasoning, "why this plan (model-written, not verified)") : null)));
    const results = new Map(((analysis && analysis.data.steps) || []).map((s) => [s.step_id, s]));
    const assigned = new Map(d.assignments.map((a) => [a.step_id, a]));
    for (const st of d.steps) {
      const r = results.get(st.step_id) || { status: "not_run", inputs: {} };
      const a = assigned.get(st.step_id);
      const cap = CAPS[st.capability] || { agent: st.role || st.capability, task: st.capability };
      const chips = [tag(r.status.replace("_", " "), r.status === "succeeded" ? "ok" : r.status === "not_run" ? "" : "bad"),
        tag(r.model || (a && a.model) || "no model needed")];
      cards.push(agentCard(++n, cap.agent, cap.task, chips, inputRows(r.inputs), outputNode(r), a && a.reason));
    }
  }
  if (coder) {
    const d = coder.data;
    const mem = first("memory");
    const rows = drafts.map((e) => {
      const x = e.data;
      return h("li", null, h("strong", null, "Draft " + x.draft_no + ": "), DRAFT_OUTCOME[x.outcome] || x.outcome,
        x.summary && x.summary !== "no summary was recorded"
          ? h("details", null, h("summary", null, "what the coder said"), said(x.summary + (x.reasoning ? "\n\n" + x.reasoning : ""), "written by " + x.model + ", not verified")) : null);
    });
    cards.push(agentCard(++n, "Coder", "Write the change as a unified diff (up to 3 files, 100 lines)",
      [tag(drafts.some((e) => e.data.outcome === "applied") ? "change made" : "no change", drafts.some((e) => e.data.outcome === "applied") ? "ok" : "bad"), tag(d.model)],
      h("div", { class: "stack" }, h("div", null, "Your request: " + cut(turn.text, 300)),
        h("div", { class: "mono" }, "Files: " + (d.files.length ? d.files.join(", ") : "none chosen")),
        mem ? h("div", { class: "muted" }, mem.data.used ? "Earlier experience offered: " + mem.data.claims + " claim(s), " + mem.data.past_changes + " earlier change(s)." : "No earlier experience offered.") : null),
      rows.length ? h("ol", { class: "plain" }, rows) : h("span", { class: "muted" }, "no draft yet"), d.why));
  }
  if (answer || answerFail) {
    const files = [...new Set(((analysis && analysis.data.steps) || []).map((s) => (s.inputs || {}).path || ((s.inputs || {}).node_id || "").replace(/^module:/, "")).filter(Boolean))];
    cards.push(agentCard(++n, "Answerer", "Answer your question from the files and the findings above",
      [answer ? tag("answered", "ok") : tag("no answer", "bad"), tag(answer ? answer.data.model : "all models failed")],
      h("div", { class: "stack" }, h("div", null, "Question: " + cut(turn.text, 300)),
        h("div", { class: "mono" }, "Files offered: " + (files.length ? files.join(", ") : "none"))),
      answer ? h("span", null, "Shown under Answer above" + (answer.data.cited_paths.length ? "; it cites " + answer.data.cited_paths.join(", ") : "") + ".")
        : h("div", { class: "notice" }, answerFail.data.message)));
  }
  if (!cards.length) return null;
  return h("section", { class: "stack", "aria-label": "What each agent did" }, h("h3", null, "What each agent did"), cards);
}

function timelineLine(ev) {
  const d = ev.data;
  switch (ev.kind) {
    case "replay": return li("Recorded example (no model was called now)");
    case "started": return li("Started on project " + d.project);
    case "plan": return li("Conductor made a plan with " + d.steps.length + " step(s)");
    case "analysis": return li("The planned steps ran: " + d.status, d.status === "succeeded" ? "ok" : "bad");
    case "coder": return li("Coder chosen: " + d.model);
    case "memory": return li(d.used ? "Memory offered to the coder: " + d.claims + " claim(s), " + d.past_changes + " earlier change(s)" : "The coder was not given earlier experience");
    case "draft": return li("Draft " + d.draft_no + ": " + (DRAFT_OUTCOME[d.outcome] || d.outcome), d.outcome === "applied" ? "ok" : "");
    case "change_event": return li(d.kind + (d.detail ? ": " + d.detail : ""), /refused|failed/.test(d.kind + d.detail) ? "bad" : "");
    case "approval_request": return li((d.action === "pr" ? "Pull request" : "Review") + " requested for commit " + String(d.commit).slice(0, 12));
    case "approved": return li("You approved commit " + String(d.commit).slice(0, 12) + " (decision recorded)", "ok");
    case "rejected": return li(d.revised ? "You asked for a revision" : "You rejected this change", "bad");
    case "note": return li(d.message);
    case "answer": return li("Answer written by " + d.model, "ok");
    case "answer_failed": return li("No answer: " + d.message, "bad");
    case "done": return li(d.pr_url ? "Pull request opened" : "Finished", "ok");
    case "failed": return li("Stopped: " + d.message, "bad");
    default: return li(ev.kind);
  }
}

function turnCard(turn) {
  const open = new Set(turn.approvals.map((a) => a.id));
  const body = h("div", { class: "body" });
  const replay = turn.events.find((e) => e.kind === "replay");
  if (replay) body.append(h("div", { class: "notice", role: "note" }, h("strong", null, "Recorded example. "), replay.data.message));
  const answer = turn.events.find((e) => e.kind === "answer");
  const answerFail = turn.events.find((e) => e.kind === "answer_failed");
  if (answer) body.append(answerTop(answer.data));
  else if (answerFail) body.append(h("div", { class: "notice", role: "alert" }, "No answer was produced. " + answerFail.data.message));
  for (const ev of turn.events) {
    if (ev.kind === "failed") body.append(h("div", { class: "notice", role: "alert" }, "Stopped: " + ev.data.message));
    if (ev.kind === "done" && ev.data.pr_url) body.append(prLink(ev.data.pr_url));
  }
  const requests = turn.events.filter((e) => e.kind === "approval_request");
  const waiting = requests.filter((e) => open.has(e.data.approval_id));
  if (waiting.length) {
    body.append(h("div", { class: "stack" }, h("h3", null, "Needs your decision"), waiting.map((e) => approvalBlock(turn, e.data, true))));
  }
  const agents = agentsSection(turn);
  if (agents) body.append(agents);
  const closed = requests.filter((e) => !open.has(e.data.approval_id));
  for (const e of closed) {
    body.append(h("details", { open: replay ? true : null },
      h("summary", null, (e.data.action === "pr" ? "Pull request request" : "Review of the change") + " for commit " + String(e.data.commit).slice(0, 12) + (replay ? " (recorded)" : " (closed)")),
      approvalBlock(turn, e.data, false, !!replay)));
  }
  if (turn.events.length) {
    const events = h("ul", { class: "events" }, turn.events.map(timelineLine));
    body.append(h("details", null, h("summary", null, "Timeline (" + turn.events.length + " events)"), events));
  }
  if (turn.status === "interrupted") {
    body.append(h("p", { class: "notice" }, "The server stopped before this turn finished. Nothing was pushed."));
  }
  if (turn.status === "running") body.append(h("p", { class: "muted" }, "Working…"));
  body.append(traceBox(turn));
  return h("article", { class: "turn", "aria-label": "Turn " + turn.id },
    h("header", null, h("div", { class: "ask" }, turn.text),
      tag(turn.status.replace("_", " "), STATUS_KIND[turn.status])),
    body);
}

function answerTop(d) {
  return h("div", { class: "stack panel result" }, h("h3", null, "Answer"),
    said(d.text, "written by " + d.model + ", not verified"),
    d.cited_paths && d.cited_paths.length
      ? h("p", { class: "muted" }, "Files it cites: " + d.cited_paths.join(", ")) : null);
}

function prLink(url) {
  const safe = /^https:\/\/github\.com\//.test(url);
  return h("p", null, "Pull request: ", safe ? h("a", { href: url, rel: "noopener noreferrer", target: "_blank" }, url) : h("span", { class: "mono" }, url));
}

function approvalBlock(turn, d, isOpen, recorded) {
  const box = h("div", { class: "stack panel" });
  box.append(h("h3", null, d.action === "pr" ? "Open a pull request?" : "Review this change"),
    kv([["Branch", d.branch], ["Commit", String(d.commit).slice(0, 12)], ["Check", d.check],
      ["Status", d.status], ["Contract check", (d.impact || []).join("; ") || "not recorded"]]));
  if (d.note) box.append(h("p", { class: "notice" }, d.note));
  if (d.summary) box.append(said(d.summary + (d.reasoning ? "\n\n" + d.reasoning : ""), "what the model said it did (not verified)"));
  if (d.diff) {
    box.append(h("h3", null, "Diff"), diffView(d.diff));
    if (d.diff_cut) box.append(h("p", { class: "muted" }, d.diff_cut + " more characters are not shown."));
  } else {
    box.append(h("p", { class: "notice" }, "The diff could not be read from the worktree."));
  }
  if (d.check === "no check ran for this commit" || /not run|skipped/.test(d.check)) {
    box.append(h("p", { class: "notice" }, "No check validated this change. An approval is your label, not a test."));
  }
  if (!isOpen) {
    box.append(h("p", { class: "muted" }, recorded ? "Recorded example: the buttons are off. In a live run you can approve, revise or reject here." : "This request is closed."));
    return box;
  }
  const note = h("textarea", { "aria-label": "Note or what to revise", placeholder: "Note (needed to revise)" });
  const msg = h("p", { class: "muted", role: "status" });
  const body = (extra) => Object.assign({ approval_id: d.approval_id, change_id: d.change_id, diff_sha256: d.diff_sha256, note: note.value }, extra);
  const act = (path) => async (e) => {
    e.target.disabled = true;
    try {
      const r = await post(path, body());
      if (path === "/api/change/revise") { await openSession(view.session); }
      else { const s = await api("/api/session", { id: view.session }); view.turns = s.turns; }
      draw();
      void r;
    } catch (err) { msg.textContent = err.message; e.target.disabled = false; }
  };
  if (d.action === "pr") {
    box.append(h("div", { class: "row" },
      h("button", { type: "button", class: "primary", onclick: act("/api/change/pr") }, "Push branch and open pull request")),
      h("p", { class: "muted" }, "This sends the branch and the diff to GitHub. Nothing is merged."));
  } else {
    box.append(note, h("div", { class: "row" },
      h("button", { type: "button", class: "primary", onclick: act("/api/change/approve") }, "Approve"),
      h("button", { type: "button", onclick: act("/api/change/revise") }, "Revise"),
      h("button", { type: "button", class: "danger", onclick: act("/api/change/reject") }, "Reject")));
  }
  box.append(msg);
  return box;
}

function traceBox(turn) {
  const det = h("details", null, h("summary", null, "Trace: decisions, model calls, evidence"));
  const out = h("div", { class: "stack" });
  det.append(out);
  det.addEventListener("toggle", async () => {
    if (!det.open) return;
    clear(out).append(h("p", { class: "muted" }, "Loading…"));
    try { renderTrace(out, await api("/api/trace", { turn: turn.id })); } catch (err) { showError(out, err); }
  });
  return det;
}

function renderTrace(out, t) {
  clear(out);
  if (!t.run_ids.length && !t.changes.length) out.append(h("p", { class: "muted" }, "Nothing was recorded for this turn yet."));
  for (const c of t.changes) {
    out.append(h("h3", null, "Change " + c.change_id + " (" + c.status + ")"),
      kv([["Project", c.project], ["Branch", c.branch], ["Base", c.base_commit.slice(0, 12)]]));
    const list = h("ul", { class: "events" });
    for (const e of c.events) list.append(li(e.kind + (e.detail ? ": " + e.detail : "") + (e.ref ? " [" + cut(e.ref, 44) + "]" : "")));
    out.append(list);
  }
  if (t.decisions.length) out.append(h("h3", null, "Decisions and reasoning"));
  for (const d of t.decisions) {
    out.append(h("div", { class: "stack" },
      h("div", null, tag(d.made_by, d.made_by === "person" ? "ok" : "warn"), " ", h("strong", null, d.summary)),
      kv([["Chosen", d.chosen], ["Options", d.options.join(" | ")], ["Model", d.model], ["Citations", d.citations]]),
      said(d.reasoning, d.basis === "person" ? "written by a person" : "written by a model, not verified"),
      h("div", { class: "muted mono" }, d.decision_id)));
  }
  if (t.calls.length) {
    out.append(h("h3", null, "Model calls"));
    const body = h("tbody");
    for (const c of t.calls) {
      body.append(h("tr", null, h("td", null, c.entry_id || c.model || ""), h("td", null, c.result || c.outcome || ""),
        h("td", null, c.latency_ms ?? ""), h("td", null, (c.prompt_tokens ?? "?") + " / " + (c.completion_tokens ?? "?"))));
    }
    out.append(h("div", { class: "scroll" }, h("table", null,
      h("thead", null, h("tr", null, h("th", null, "Model"), h("th", null, "Result"), h("th", null, "ms"), h("th", null, "tokens in/out"))), body)));
  }
  out.append(h("p", { class: "muted" }, "Browse every record on the Data page."));
}

boot().catch((err) => { clear(root); showError(root, err); });
