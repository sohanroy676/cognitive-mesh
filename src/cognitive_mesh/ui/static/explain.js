"use strict";
// Plain-language wording for a claim's evidence (no DOM here, so it can be tested under Node).
// Everything is built only from the fields of the assessment the server sent. A status is a
// statement about one code version by a stated method, so no sentence here says a claim is true,
// proven or correct.

(function (root) {
  const short = (fp) => (fp ? fp.slice(0, 12) : null);
  const strip = (id) => String(id).replace(/^(module|package|stdlib|component|unresolved):/, "");
  const plural = (n, one, many) => n + " " + (n === 1 ? one : many);

  // Where a code version stands relative to the code the page checked against.
  function versionWords(fp, current) {
    if (!fp) return "a code version that was not recorded";
    if (!current) return "code version " + short(fp) + " (whether that is the current code is not known)";
    return fp === current ? "the current code (" + short(fp) + ")" : "older code (" + short(fp) + ")";
  }

  // "cart.py depends on pricing.py" from the claim text, or null for a text we do not recognise.
  function aboutClaim(name) {
    const dep = /^(\S+) depends_on (\S+)$/.exec(name);
    if (dep) return strip(dep[1]) + " depends on " + strip(dep[2]);
    const unresolved = /^(\S+) is_unresolved$/.exec(name);
    if (unresolved) return strip(unresolved[1]) + " refers to something that could not be resolved";
    return null;
  }

  function sidesForCurrentCode(a) {
    const cur = a.current_fingerprint;
    const c = a.counts || {};
    const re = a.rederivation;
    const outcomes = (a.outcomes || []).filter((o) => o.source !== "promotion" && o.fingerprint === cur);
    const forIt = [];
    const against = [];
    if (cur) {
      if ((a.supporting || []).some((s) => s.fingerprint === cur && s.source === "ok")) forIt.push("a run that found it");
      if (c.confirmed_current > 0) forIt.push(c.confirmed_current === 1 ? "a person's review" : "people's reviews");
      if (re && re.holding === "holds") forIt.push("re-reading the current source");
      if (outcomes.some((o) => o.result === "succeeded")) forIt.push("a recorded check that succeeded");
      if (c.contradicting_same_version > 0) against.push("a run that listed the dependencies without it");
      if (c.rejected_current > 0) against.push("a person's rejecting review");
      if (re && re.holding === "refuted") against.push("re-reading the current source");
      if (outcomes.some((o) => o.result === "failed")) against.push("a recorded check that failed");
    }
    return { forIt, against };
  }

  const joinList = (items) => (items.length <= 1 ? items.join("") : items.slice(0, -1).join(", ") + " and " + items[items.length - 1]);

  const HOW = { rederived: "re-reading the current source finds it", reviewed: "a person confirmed it", outcome: "a recorded check succeeded" };

  const OTHER = {
    contested: "A recorded result for the current code says the work this claim supported failed. Earlier results stay on record.",
    unknown: "A recorded result for the current code was unclear, so it neither confirms nor weakens the claim.",
    expired: "A review or result gave this claim an end date and that date has passed, so it is listed but not relied on.",
    environment_changed: "It was recorded for a particular environment (such as the Python version) and this environment is different.",
    environment_unverified: "It was recorded for a particular environment (such as the Python version) and the two could not be fully compared.",
    unsupported: "No stored observation of this can be traced back to the run it came from, so nothing here backs it.",
  };

  // One or two sentences saying why the claim has its status, and what to do about it.
  function headline(a) {
    const status = a.status;
    const cur = a.current_fingerprint;
    if (status === "conflicting") {
      const { forIt, against } = sidesForCurrentCode(a);
      const verb = (items) => (items.length === 1 ? "says" : "say");
      const text = forIt.length && against.length
        ? "For the current code, " + joinList(forIt) + " " + verb(forIt) + " it holds, but " + joinList(against) + " " + verb(against) + " it does not. These disagree, so treat the claim as unresolved."
        : "Records disagree about the current code, so treat the claim as unresolved.";
      return { text, next: "To settle it, open the current source and look. Then add a review or a check result from the command line (cognitive-mesh experience review, or experience outcome). Earlier records are never edited." };
    }
    if (status === "validated") {
      const how = (a.validated_by || []).map((k) => HOW[k]).filter(Boolean);
      return { text: "It holds for the current code: " + (how.length ? joinList(how) : "a stated check passed") + ". That describes this code version only. It does not show the design is right.", next: null };
    }
    if (status === "stale") {
      return { text: "Only older runs found it, and nothing confirms it for the current code, so it may no longer hold.", next: cur ? "To check, record a new run on the current code." : null };
    }
    if (status === "supported") {
      return {
        text: "A past run found it and nothing contradicts it. No code folder was given, so nobody checked whether it still holds today.",
        next: "Start the UI with --root FOLDER to check it against your code.",
      };
    }
    return { text: OTHER[status] || a.meaning, next: null };
  }

  function review(r, current) {
    const word = r.verdict === "confirmed" ? "confirmed it" : "rejected it";
    return {
      before: "A person " + word + " (review " + r.id + ").",
      sub: "The reviewer name “" + r.reviewer + "” is not verified. They looked at " + versionWords(r.fingerprint, current) + (r.note ? ". Note: " + r.note : "."),
    };
  }

  // What speaks for the claim, what speaks against it, and what speaks for neither.
  function records(a) {
    const cur = a.current_fingerprint;
    const pro = [];
    const con = [];
    const neither = [];
    for (const s of a.supporting || []) {
      const ok = s.source === "ok";
      pro.push({
        before: "Run ",
        runId: s.run_id,
        after: " found this dependency.",
        sub: "It looked at " + versionWords(s.fingerprint, cur) + "." + (ok ? " Its stored record still matches the run." : " Its stored record no longer matches the run (" + s.source + "), so treat this with caution."),
      });
    }
    for (const c of a.contradicting || []) {
      con.push({
        before: "Run ",
        runId: c.run_id,
        after: " listed this file’s dependencies and this one was not in the list.",
        sub: "It looked at " + versionWords(c.fingerprint, cur) + "." + (c.same_version ? " That is the same code the claim was checked against, so this is a real disagreement." : " The code may simply have changed since."),
      });
    }
    const re = a.rederivation;
    if (re && re.holding === "holds") pro.push({ before: "Re-read the current source: the dependency is there.", sub: "Done with the same import analysis that made the first observation." });
    else if (re && re.holding === "refuted") con.push({ before: "Re-read the current source: the dependency is not there.", sub: "Done with the same import analysis that made the first observation." });
    else if (re) neither.push({ before: "Re-reading the current source could not decide.", sub: "The file this claim is about is not in the source that was checked." });
    for (const r of a.reviews || []) (r.verdict === "confirmed" ? pro : con).push(review(r, cur));
    for (const o of a.outcomes || []) {
      if (o.source === "promotion") {
        neither.push({ before: "A promotion was attempted and refused (record " + o.id + ").", sub: "A refused promotion is not evidence either way." });
        continue;
      }
      const who = o.source === "check" ? "A recorded check" : "A person";
      const line = { before: who + " reported " + o.result + " (record " + o.id + ").", sub: "For " + versionWords(o.fingerprint, cur) + (o.detail ? ". Detail: " + o.detail : ".") };
      if (o.result === "succeeded") pro.push(line);
      else if (o.result === "failed") con.push(line);
      else neither.push(line);
    }
    return { pro, con, neither };
  }

  const CORROBORATION = {
    none: "Nothing independent has confirmed it yet.",
    single: "One independent kind of source supports it.",
    corroborated: "Two or more independent kinds of source agree.",
    contradicted: "Independent sources disagree with each other.",
  };

  // Readable replacement for the raw counts and source lists.
  function reading(a) {
    const c = a.counts || {};
    const out = [];
    out.push(CORROBORATION[a.corroboration] || "");
    const groups = (a.sources || []).map((g) => g.source_class + " (" + plural(g.records.length, "record", "records") + ")");
    out.push("Independent sources for the current code: " + (groups.length ? groups.join(", ") : "none") + ". Records of the same kind count once, so ten runs by the analyzer are one source, not ten.");
    out.push("Runs that found it: " + c.supporting_ok + " with a matching stored record" + (c.supporting_not_ok ? ", " + c.supporting_not_ok + " whose record no longer matches" : "") + ", across " + plural(c.distinct_fingerprints || 0, "code version", "code versions") + ".");
    out.push("Runs that listed the file without it: " + c.contradicting_same_version + " on the current code, " + c.contradicting_other_version + " on other code.");
    out.push("Reviews: " + c.confirmed_current + " confirming and " + c.rejected_current + " rejecting for the current code; " + c.confirmed_other + " and " + c.rejected_other + " for other code.");
    if (a.differs_across_versions) out.push("The runs disagree across code versions, which usually means the code changed.");
    return out;
  }

  function currentCodeNote(a) {
    return a.current_fingerprint
      ? "Compared with the code folder given as --root, code version " + short(a.current_fingerprint) + "."
      : "No code folder was given (--root), so nobody checked this against your code today.";
  }

  const KEY = [
    "A claim is a statement drawn from past runs, such as “cart.py depends on pricing.py”. Treat it as a lead to check, not as a fact.",
    "Supports lists the records that found it or confirmed it. Contradicts lists the records that looked and did not find it, and people who rejected it.",
    "The current code is the folder given with --root. A claim can only be called validated for that code.",
    "A review is a person’s opinion and the reviewer name is not verified. Run ids open the recorded run.",
    "Validated, stale and the other statuses describe one code version by a stated method. None of them means proven.",
  ];

  const api = { versionWords, aboutClaim, headline, records, reading, currentCodeNote, KEY, plural };
  root.CogExplain = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
