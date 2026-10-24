"use strict";
// The interactive dependency graph (ADR-0032). Everything from the server is untrusted text: it is
// written with textContent only, never parsed as markup, and no style is set from script.
// Zoom and pan change the SVG viewBox attribute. app.js loads after this file and passes in the
// shared helpers (h, api, go, pins, folder memory).

(function () {
  const SVG_NS = "http://www.w3.org/2000/svg";
  const MIN_ZOOM = 0.5;
  const MAX_ZOOM = 60;
  const CLIP = 30;

  // ---- pure helpers (kept small and separate so a test runner can be added later) -------

  // View = { x, y, scale }: the world point at the top-left corner and CSS pixels per world unit.
  function fitView(worldW, worldH, stageW, stageH) {
    const scale = Math.max(0.01, Math.min(stageW / worldW, stageH / worldH));
    return { x: (worldW - stageW / scale) / 2, y: (worldH - stageH / scale) / 2, scale, fit: scale };
  }

  function zoomAt(view, factor, px, py) {
    const target = Math.min(view.fit * MAX_ZOOM, Math.max(view.fit * MIN_ZOOM, view.scale * factor));
    const wx = view.x + px / view.scale;
    const wy = view.y + py / view.scale;
    return { ...view, scale: target, x: wx - px / target, y: wy - py / target };
  }

  function panBy(view, dxPx, dyPx) {
    return { ...view, x: view.x - dxPx / view.scale, y: view.y - dyPx / view.scale };
  }

  function centerOn(view, wx, wy, stageW, stageH, scale) {
    const s = Math.min(view.fit * MAX_ZOOM, Math.max(view.fit * MIN_ZOOM, scale));
    return { ...view, scale: s, x: wx - stageW / s / 2, y: wy - stageH / s / 2 };
  }

  function clip(text) {
    return text.length > CLIP ? text.slice(0, CLIP - 1) + "\u2026" : text;
  }

  // ---- view --------------------------------------------------------------------------------

  function view(ctx) {
    const { h, api, go, pins, folder, plural, messages } = ctx;

    const svgEl = (tag, attrs, ...kids) => {
      const e = document.createElementNS(SVG_NS, tag);
      for (const [k, v] of Object.entries(attrs || {})) {
        if (v !== null && v !== undefined && v !== false) e.setAttribute(k, String(v));
      }
      for (const kid of kids.flat()) {
        if (kid !== null && kid !== undefined && kid !== false) e.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
      }
      return e;
    };

    return async function (root, params) {
      const status = await api("/api/status");
      const rootGiven = status.data.inputs.root.state === "present";
      const path = params.get("path") ?? "";
      const full = params.get("full") === "1";
      const focusId = params.get("focus") || "";
      const typed = path || (rootGiven ? "" : folder.get());

      root.append(
        h("h2", { text: "Dependency graph" }),
        h("p", { class: "prose note", text: "Type a folder to graph it. An edge from A to B means A depends on B. Nothing in the folder is changed or run." }),
      );
      const input = h("input", { name: "path", value: typed, type: "text", size: 54, placeholder: rootGiven ? "Leave empty to use the --root folder" : "For example E:\\Projects\\my-app", "aria-label": "Folder to graph" });
      const fullBox = h("input", { type: "checkbox", name: "full", checked: full ? true : null });
      const submit = (ev) => {
        ev.preventDefault();
        const next = { path: input.value.trim() };
        if (fullBox.checked) next.full = "1";
        folder.set(input.value.trim());
        const q = new URLSearchParams(next).toString();
        if (window.location.hash === "#graph?" + q) ctx.rerender();
        else go("graph", next);
      };
      root.append(
        h("form", { onsubmit: submit },
          h("label", {}, "Folder", input),
          h("label", { class: "check" }, fullBox, h("span", { text: "Show the full graph" })),
          h("button", { class: "act", type: "submit", text: "Build graph" })),
      );
      const out = h("div", {});
      root.append(out);
      if (!path && !rootGiven) {
        out.append(h("p", { class: "muted", text: "No folder yet. Type one above and choose Build graph." }));
        return;
      }

      const wait = h("p", { class: "muted", text: "Building the graph. A large folder can take several seconds." });
      out.append(wait);
      const started = Date.now();
      const timer = window.setInterval(() => {
        wait.textContent = "Building the graph (" + Math.round((Date.now() - started) / 1000) + " s). A large folder can take several seconds.";
      }, 1000);
      let reply;
      try {
        reply = await api("/api/graph", { path, full });
      } finally {
        window.clearInterval(timer);
        wait.remove();
      }
      draw(out, reply.data, focusId, path);
    };

    function draw(out, d, focusId, typedPath) {
      const byKey = new Map(d.nodes.map((n) => [n.key, n]));
      const byId = new Map(d.nodes.map((n) => [n.id, n]));
      const edgeByNumber = new Map(d.edges.map((e) => [String(e.number), e]));
      const incoming = new Map();
      const outgoing = new Map();
      const near = new Map(d.nodes.map((n) => [n.key, new Set([n.key])]));
      for (const e of d.edges) {
        incoming.set(e.target, (incoming.get(e.target) || 0) + 1);
        outgoing.set(e.source, (outgoing.get(e.source) || 0) + 1);
        if (near.has(e.source) && near.has(e.target)) {
          near.get(e.source).add(e.target);
          near.get(e.target).add(e.source);
        }
      }

      // summary and truncation
      const t = d.truncation;
      out.append(h("p", { text: d.folder + ": " + plural(t.shown_nodes, "node", "nodes") + " and " + plural(t.shown_edges, "edge", "edges") + " shown." }));
      if (t.truncated) {
        const omitted = Object.entries(t.omitted_nodes_by_kind).map(([k, n]) => n + " " + k).join(", ") || "none";
        out.append(
          h("div", { class: "banner warn", role: "status" },
            "Showing " + t.shown_nodes + " of " + t.total_nodes + " nodes and " + t.shown_edges + " of " + t.total_edges + " edges. Left out: " + omitted + ". ",
            h("button", { class: "link", type: "button", onclick: () => go("graph", { path: typedPath, full: "1" }), text: "Show the full graph" })),
        );
      } else if (d.full) {
        out.append(h("p", { class: "note", text: "This is the full graph." }));
      }
      if (d.nodes.length > 150) {
        out.append(h("p", { class: "note", text: "This graph is large, so it starts small. Zoom in, or use Find to jump to a node. Hover a node to see only its neighbours." }));
      }
      if (d.cycles_total) out.append(h("p", { class: "note", text: plural(d.cycles_total, "dependency cycle", "dependency cycles") + " found; cycle edges are drawn heavier." }));
      if (!d.nodes.length) {
        out.append(h("p", { text: "No dependency nodes were found in this folder. It needs a Python, JavaScript or TypeScript project, or a package manifest." }));
        return;
      }

      // stage: the SVG, zoom buttons and the inspector
      const stage = h("div", { class: "stage", tabindex: "0", role: "group", "aria-label": "Dependency graph. Plus and minus zoom, zero fits the whole graph, arrow keys pan." });
      const svg = svgEl("svg", { class: "gsvg", role: "presentation" });
      const defs = svgEl("defs", {},
        svgEl("marker", { id: "arrow", viewBox: "0 0 8 8", refX: 7, refY: 4, markerWidth: 7, markerHeight: 7, orient: "auto" }, svgEl("path", { d: "M0 0 L8 4 L0 8 z", class: "arrow" })),
        svgEl("marker", { id: "arrow-cycle", viewBox: "0 0 8 8", refX: 7, refY: 4, markerWidth: 7, markerHeight: 7, orient: "auto" }, svgEl("path", { d: "M0 0 L8 4 L0 8 z", class: "arrow cycle" })));
      const edgeLayer = svgEl("g", { class: "edges" });
      const nodeLayer = svgEl("g", { class: "nodes" });
      svg.append(defs, edgeLayer, nodeLayer);

      const W = d.box.w;
      const H = d.box.h;
      const edgePaths = new Map();
      for (const e of d.edges) {
        const a = byKey.get(e.source);
        const b = byKey.get(e.target);
        if (!a || !b) continue;
        let dPath;
        if (a === b) {
          dPath = "M" + (a.x + W) + " " + (a.y + H / 2) + " C" + (a.x + W + 50) + " " + (a.y - 14) + " " + (a.x + W + 50) + " " + (a.y + H + 14) + " " + (a.x + W) + " " + (a.y + H / 2);
        } else if (b.y > a.y + H / 2) {
          const x1 = a.x + W / 2, y1 = a.y + H, x2 = b.x + W / 2, y2 = b.y;
          const dy = Math.min(120, Math.max(20, (y2 - y1) / 2));
          dPath = "M" + x1 + " " + y1 + " C" + x1 + " " + (y1 + dy) + " " + x2 + " " + (y2 - dy) + " " + x2 + " " + y2;
        } else {
          const x1 = a.x + W, y1 = a.y + H / 2, x2 = b.x + W, y2 = b.y + H / 2;
          dPath = "M" + x1 + " " + y1 + " C" + (x1 + 60) + " " + y1 + " " + (x2 + 60) + " " + y2 + " " + x2 + " " + y2;
        }
        const cls = "edge k-" + e.kind + (e.in_cycle ? " cycle" : "") + (e.to_unresolved ? " unresolved" : "");
        const p = svgEl("path", { d: dPath, class: cls, "data-n": e.number, "marker-end": e.in_cycle ? "url(#arrow-cycle)" : "url(#arrow)" });
        edgePaths.set(e.number, p);
        edgeLayer.append(p);
      }

      const nodeEls = new Map();
      for (const n of d.nodes) {
        const g = svgEl("g", { class: "gn s-" + n.shape + (n.in_cycle ? " cycle" : ""), "data-key": n.key, tabindex: "0", role: "button", "aria-label": n.kind + " " + n.id, transform: "translate(" + n.x + " " + n.y + ")" },
          svgEl("title", {}, n.id),
          svgEl("rect", { width: W, height: H, rx: 3 }),
          svgEl("text", { class: "kind", x: 8, y: 15 }, n.kind),
          svgEl("text", { class: "lab", x: 8, y: 33 }, clip(n.label)),
          svgEl("rect", { class: "pinmark", x: W - 16, y: 5, width: 9, height: 9 }));
        nodeEls.set(n.key, g);
        nodeLayer.append(g);
      }
      stage.append(svg);

      // zoom controls
      let state = fitView(d.width, d.height, 800, 500);
      const size = () => ({ w: Math.max(stage.clientWidth, 100), h: Math.max(stage.clientHeight, 100) });
      const apply = () => {
        const s = size();
        svg.setAttribute("viewBox", state.x + " " + state.y + " " + s.w / state.scale + " " + s.h / state.scale);
        zoomNote.textContent = Math.round((state.scale / state.fit) * 100) + "%";
      };
      // Wheel and key events can arrive faster than frames: draw once per frame.
      let queued = false;
      const schedule = () => {
        if (queued) return;
        queued = true;
        window.requestAnimationFrame(() => {
          queued = false;
          apply();
        });
      };
      const fit = () => {
        const s = size();
        state = fitView(d.width, d.height, s.w, s.h);
        apply();
      };
      const zoomBy = (factor) => {
        const s = size();
        state = zoomAt(state, factor, s.w / 2, s.h / 2);
        schedule();
      };
      const zoomNote = h("span", { class: "zoomnote", "aria-live": "polite", text: "100%" });
      const bar = h("div", { class: "zoombar" },
        h("button", { type: "button", class: "zb", "aria-label": "Zoom in", onclick: () => zoomBy(1.4), text: "+" }),
        h("button", { type: "button", class: "zb", "aria-label": "Zoom out", onclick: () => zoomBy(1 / 1.4), text: "\u2212" }),
        h("button", { type: "button", class: "zb wide", onclick: fit, text: "Fit" }),
        zoomNote);
      stage.append(bar);

      // inspector and pins
      const inspector = h("div", { class: "inspector", "aria-live": "polite" });
      const pinList = h("div", { class: "pinlist" });
      let selected = null;
      let shown = null; // what the inspector shows; it changes only on a deliberate hover, focus or click

      const folderPath = typedPath || d.folder;
      const entryFor = (n) => ({ space: "deps", id: n.id, label: n.label, folder: d.folder });

      const highlight = (key) => {
        const active = key ? near.get(key) : null;
        svg.classList.toggle("focusing", Boolean(active));
        for (const [k, g] of nodeEls) g.classList.toggle("near", Boolean(active) && active.has(k));
        for (const e of d.edges) {
          const p = edgePaths.get(e.number);
          if (p) p.classList.toggle("near", Boolean(active) && (e.source === key || e.target === key));
        }
      };
      const refreshMarks = () => {
        for (const n of d.nodes) nodeEls.get(n.key).classList.toggle("pinned", pins.has("deps", n.id));
        for (const [k, g] of nodeEls) g.classList.toggle("selected", k === selected);
      };

      const showNode = (n, force) => {
        if (!force && shown === n.key) return;
        shown = n.key;
        const pinned = pins.has("deps", n.id);
        inspector.replaceChildren(
          h("p", { class: "claim-text small", text: n.label }),
          h("dl", {},
            h("dt", { text: "Node id" }), h("dd", { class: "mono", text: n.id }),
            h("dt", { text: "Kind" }), h("dd", { text: n.kind + (n.in_cycle ? ", in a dependency cycle" : "") }),
            n.path ? h("dt", { text: "Introduced by" }) : null, n.path ? h("dd", { class: "mono", text: n.path }) : null,
            h("dt", { text: "Depends on" }), h("dd", { text: plural(outgoing.get(n.key) || 0, "node", "nodes") }),
            h("dt", { text: "Needed by" }), h("dd", { text: plural(incoming.get(n.key) || 0, "node", "nodes") })),
          h("div", { class: "row" },
            h("button", { type: "button", class: "act small", onclick: () => { pins.toggle(entryFor(n)); refreshMarks(); showNode(n, true); renderPins(); }, text: pinned ? "Unpin this id" : "Pin this id" }),
            copyButton(n.id),
            h("button", { type: "button", class: "act small ghost", onclick: () => go("traces", { for: n.id }), text: "Show recorded runs" })),
        );
      };
      const showEdge = (e) => {
        shown = "edge:" + e.number;
        const a = byKey.get(e.source);
        const b = byKey.get(e.target);
        inspector.replaceChildren(
          h("p", { class: "claim-text small", text: "e" + e.number + ": " + e.kind }),
          h("dl", {},
            h("dt", { text: "From" }), h("dd", { class: "mono", text: a ? a.id : "?" }),
            h("dt", { text: "To" }), h("dd", { class: "mono", text: b ? b.id : "?" })),
          h("p", { class: "note", text: "Why this edge exists:" }),
          h("ul", { class: "recs" }, e.evidence.map((ev) => h("li", {}, h("span", { class: "mono", text: ev.path + (ev.line ? ":" + ev.line : "") }), h("span", { class: "sub", text: ev.rule })))),
        );
      };
      const showNothing = () => {
        shown = null;
        inspector.replaceChildren(h("p", { class: "muted", text: "Hover or focus a node to see its id. Click a node to pin it. Hover an edge to see why it exists." }));
      };
      // The clipboard API needs a secure context and permission; fall back to a hidden text area, and
      // always say what happened, because a silent copy cannot be told from a broken one.
      async function copyText(text) {
        try {
          await navigator.clipboard.writeText(text);
          return true;
        } catch (e) {
          // fall through to the older route
        }
        try {
          const area = h("textarea", { readonly: true, class: "offscreen", "aria-hidden": "true", tabindex: "-1" });
          area.value = text;
          document.body.append(area);
          area.select();
          const ok = document.execCommand("copy");
          area.remove();
          return ok;
        } catch (e) {
          return false;
        }
      }
      function copyButton(text) {
        const label = "Copy id";
        const btn = h("button", { type: "button", class: "act small ghost", text: label });
        btn.addEventListener("click", async () => {
          const ok = await copyText(text);
          btn.textContent = ok ? "Copied" : "Copy failed. Select the id above and press Ctrl+C";
          window.setTimeout(() => { btn.textContent = label; }, 2500);
        });
        return btn;
      }

      const renderPins = () => {
        const mine = pins.list();
        pinList.replaceChildren(
          h("h3", { text: "Pinned ids" }),
          mine.length
            ? h("ul", { class: "recs" }, mine.map((p) => h("li", {},
                p.space === "deps"
                  ? h("button", { class: "link", type: "button", onclick: () => go("graph", { path: p.folder || folderPath, focus: p.id }), text: p.id })
                  : h("button", { class: "link", type: "button", onclick: () => go("node", { id: p.id }), text: p.id }),
                h("button", { class: "link ghostlink", type: "button", "aria-label": "Unpin " + p.id, onclick: () => { pins.remove(p.space, p.id); refreshMarks(); renderPins(); if (shown && byKey.has(shown)) showNode(byKey.get(shown), true); }, text: "unpin" }))))
            : h("p", { class: "note", text: "Nothing pinned yet. A pinned id is marked wherever it appears again." }),
        );
      };

      const select = (key, center) => {
        selected = key;
        highlight(key);
        refreshMarks();
        if (key) showNode(byKey.get(key), true);
        else showNothing();
        if (key && center) {
          const n = byKey.get(key);
          const s = size();
          state = centerOn(state, n.x + W / 2, n.y + H / 2, s.w, s.h, Math.max(state.scale, state.fit * 2.5));
          apply();
        }
      };

      // delegated hover, focus and click
      const keyOf = (target) => {
        const g = target.closest ? target.closest(".gn") : null;
        return g ? g.getAttribute("data-key") : null;
      };
      const inspect = (target) => {
        const key = keyOf(target);
        if (key) {
          highlight(key);
          showNode(byKey.get(key));
          return;
        }
        const p = target.closest ? target.closest("path.edge") : null;
        if (p) {
          const e = edgeByNumber.get(p.getAttribute("data-n"));
          if (e) showEdge(e);
        }
      };
      const leave = () => {
        highlight(selected);
      };
      svg.addEventListener("mouseover", (ev) => inspect(ev.target));
      svg.addEventListener("mouseout", (ev) => {
        if (!ev.relatedTarget || !svg.contains(ev.relatedTarget)) leave();
        else if (keyOf(ev.target) && !keyOf(ev.relatedTarget) && !(ev.relatedTarget.closest && ev.relatedTarget.closest("path.edge"))) leave();
      });
      svg.addEventListener("focusin", (ev) => inspect(ev.target));
      svg.addEventListener("focusout", leave);
      let moved = false;
      svg.addEventListener("click", (ev) => {
        if (moved) return;
        const key = keyOf(ev.target);
        if (!key) { select(null); return; }
        const n = byKey.get(key);
        if (!pins.has("deps", n.id)) pins.toggle(entryFor(n));
        select(key);
        renderPins();
      });
      svg.addEventListener("keydown", (ev) => {
        if ((ev.key === "Enter" || ev.key === " ") && keyOf(ev.target)) {
          ev.preventDefault();
          const n = byKey.get(keyOf(ev.target));
          if (!pins.has("deps", n.id)) pins.toggle(entryFor(n));
          select(n.key);
          renderPins();
        }
      });

      // wheel zoom and drag pan
      stage.addEventListener("wheel", (ev) => {
        ev.preventDefault();
        const r = stage.getBoundingClientRect();
        state = zoomAt(state, Math.exp(-ev.deltaY * 0.0015), ev.clientX - r.left, ev.clientY - r.top);
        schedule();
      }, { passive: false });
      let drag = null;
      stage.addEventListener("pointerdown", (ev) => {
        if (ev.button !== 0 || (ev.target.closest && ev.target.closest(".zoombar"))) return;
        drag = { x: ev.clientX, y: ev.clientY, id: ev.pointerId, on: false };
        moved = false;
      });
      stage.addEventListener("pointermove", (ev) => {
        if (!drag) return;
        const dx = ev.clientX - drag.x;
        const dy = ev.clientY - drag.y;
        if (!drag.on && Math.hypot(dx, dy) < 5) return;
        if (!drag.on) {
          drag.on = true;
          moved = true;
          stage.setPointerCapture(drag.id);
          stage.classList.add("dragging");
        }
        state = panBy(state, dx, dy);
        drag.x = ev.clientX;
        drag.y = ev.clientY;
        schedule();
      });
      const endDrag = () => {
        drag = null;
        stage.classList.remove("dragging");
        window.setTimeout(() => { moved = false; }, 0);
      };
      stage.addEventListener("pointerup", endDrag);
      stage.addEventListener("pointercancel", endDrag);
      stage.addEventListener("keydown", (ev) => {
        const step = 80;
        const keys = {
          "+": () => zoomBy(1.4), "=": () => zoomBy(1.4), "-": () => zoomBy(1 / 1.4), _: () => zoomBy(1 / 1.4), 0: fit,
          ArrowLeft: () => { state = panBy(state, step, 0); schedule(); },
          ArrowRight: () => { state = panBy(state, -step, 0); schedule(); },
          ArrowUp: () => { state = panBy(state, 0, step); schedule(); },
          ArrowDown: () => { state = panBy(state, 0, -step); schedule(); },
        };
        if (ev.target === stage && keys[ev.key]) { ev.preventDefault(); keys[ev.key](); }
      });
      if (typeof ResizeObserver !== "undefined") new ResizeObserver(() => schedule()).observe(stage);

      // find a node
      const findInput = h("input", { type: "text", name: "find", size: 30, placeholder: "Part of a node id", "aria-label": "Find a node by id" });
      const findNote = h("span", { class: "note" });
      const find = h("form", { class: "findform", onsubmit: (ev) => {
        ev.preventDefault();
        const q = findInput.value.trim().toLowerCase();
        if (!q) return;
        const hits = d.nodes.filter((n) => n.id.toLowerCase().includes(q) || n.label.toLowerCase().includes(q));
        if (!hits.length) { findNote.textContent = "No node matches that text."; return; }
        findNote.textContent = plural(hits.length, "match", "matches") + (hits.length > 1 ? "; showing the first." : ".");
        select(hits[0].key, true);
      } }, h("label", {}, "Find a node", findInput), h("button", { class: "act small", type: "submit", text: "Find" }), findNote);

      out.append(
        find,
        h("div", { class: "graph-wrap" }, stage, h("aside", { class: "side-panel" }, inspector, pinList)),
        h("p", { class: "note", text: "Scroll or use the buttons to zoom, drag to pan. Hover a node to see only its neighbours. Click a node to pin its id: the id is marked wherever it appears again, in this browser." }),
      );
      showNothing();
      renderPins();
      refreshMarks();
      fit();
      if (focusId && byId.has(focusId)) select(byId.get(focusId).key, true);
    }
  }

  window.CogGraph = { view, math: { fitView, zoomAt, panBy, centerOn, clip } };
})();
