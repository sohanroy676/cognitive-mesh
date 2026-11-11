// Shared helpers for the three pages (ADR-0044).
// No innerHTML anywhere: every string from the server reaches the page as a text node.
"use strict";

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (k === "text") el.textContent = v;
    else el.setAttribute(k, v === true ? "" : String(v));
  }
  add(el, kids);
  return el;
}

function add(el, kids) {
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
}

function svg(tag, attrs, ...kids) {
  const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined) continue;
    if (k === "text") el.textContent = v;
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, String(v));
  }
  add(el, kids);
  return el;
}

function clear(el) {
  while (el.firstChild) el.removeChild(el.firstChild);
  return el;
}

let tokenCache = null;
async function meta() {
  if (!tokenCache) {
    const r = await fetch("/api/meta", { credentials: "same-origin" });
    if (!r.ok) throw new Error("this page has no access token: open the URL printed by the server");
    tokenCache = await r.json();
  }
  return tokenCache;
}

async function api(path, params) {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params || {})) {
    if (v !== undefined && v !== null && v !== "") q.set(k, String(v));
  }
  const r = await fetch(path + (q.size ? "?" + q : ""), { credentials: "same-origin" });
  let body = {};
  try { body = await r.json(); } catch { /* keep {} */ }
  if (!r.ok) throw new Error(body.error || "request failed (" + r.status + ")");
  return body;
}

async function post(path, body) {
  const m = await meta();
  const r = await fetch(path, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-Cogmesh-Token": m.token },
    body: JSON.stringify(body || {}),
  });
  let out = {};
  try { out = await r.json(); } catch { /* keep {} */ }
  if (!r.ok) throw new Error(out.error || "request failed (" + r.status + ")");
  return out;
}

// The top bar. Each tab opens another local server; the token travels in the URL once, then
// that server sets its own cookie.
async function topBar(current, extra) {
  const m = await meta();
  const link = (name, key, label) => {
    const href = "http://127.0.0.1:" + m.ports[key] + "/?token=" + encodeURIComponent(m.token);
    return h("a", { href, "aria-current": key === current ? "page" : null }, label);
  };
  document.body.prepend(
    h("header", { class: "bar" },
      h("span", { class: "brand" }, "Cognitive Mesh"),
      h("nav", { class: "tabs", "aria-label": "Pages" },
        link("chat", "app", "Chat"), link("graph", "graph", "Dependency graph"),
        link("data", "data", "Data")),
      h("span", { class: "spacer" }),
      m.demo ? h("span", { class: "tag", title: "The models' replies are played back from a recording; everything else is real" }, "demo mode") : null,
      extra || null));
}

function showError(into, err) {
  clear(into);
  into.append(h("p", { class: "notice", role: "alert" }, String(err.message || err)));
}

// A diff as text: colour by the first character, never as markup.
function diffView(text) {
  const pre = h("pre", { class: "diff mono" });
  for (const line of String(text).split("\n")) {
    const cls = line.startsWith("+") && !line.startsWith("+++") ? "add"
      : line.startsWith("-") && !line.startsWith("---") ? "del"
      : line.startsWith("@@") ? "hunk" : "";
    pre.append(h("div", { class: cls }, line === "" ? " " : line));
  }
  return pre;
}

// Model-written text, always labelled.
function said(text, label) {
  return h("div", null,
    h("div", { class: "said-label" }, label || "written by a model, not verified"),
    h("div", { class: "said" }, text));
}

function tag(text, kind) { return h("span", { class: "tag" + (kind ? " " + kind : "") }, text); }

function kv(pairs) {
  const dl = h("dl", { class: "kv" });
  for (const [k, v] of pairs) {
    if (v === null || v === undefined || v === "") continue;
    dl.append(h("dt", null, k), h("dd", null, v));
  }
  return dl;
}

function cut(text, n) { const s = String(text); return s.length > n ? s.slice(0, n) + "…" : s; }
