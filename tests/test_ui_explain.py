"""The plain-language wording of the claims page (`ui/static/explain.js`), run under Node.

The wording must say what holds for which code, point at the records on both sides, and never say a
claim is true or proven. These tests skip when Node is not installed.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from ui_support import A_UTIL, World, serving

NODE = shutil.which("node")
SCRIPT = Path(__file__).parent.parent / "src" / "cognitive_mesh" / "ui" / "static" / "explain.js"
RUNNER = """
const x = require(process.argv[1]);
const { fn, args } = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const v = fn === 'KEY' ? x.KEY : x[fn](...args);
process.stdout.write(JSON.stringify(v));
"""
CUR = "e" * 40
OLD = "a" * 40
OVERCLAIMS = re.compile(r"\b(true|proven|proof|correct|guaranteed|certain)\b", re.IGNORECASE)

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def call(fn: str, *args: Any) -> Any:
    done = subprocess.run(  # noqa: S603 - fixed arguments, node found on PATH
        [str(NODE), "-e", RUNNER, str(SCRIPT)],
        input=json.dumps({"fn": fn, "args": list(args)}),
        capture_output=True,
        encoding="utf-8",
        check=True,
        timeout=60,
    )
    return json.loads(done.stdout)


def found(run_id: str, fingerprint: str, source: str = "ok") -> dict[str, Any]:
    return {
        "attempt_id": f"a-{run_id}",
        "run_id": run_id,
        "doc_id": None,
        "fingerprint": fingerprint,
        "source": source,
    }


def missing(run_id: str, fingerprint: str, same_version: bool) -> dict[str, Any]:
    return {
        "attempt_id": f"a-{run_id}",
        "run_id": run_id,
        "doc_id": None,
        "fingerprint": fingerprint,
        "same_version": same_version,
    }


def review(verdict: str = "confirmed", note: str = "checked by hand") -> dict[str, Any]:
    return {"id": 1, "verdict": verdict, "reviewer": "me", "fingerprint": CUR, "note": note}


def counts(**over: int) -> dict[str, int]:
    base = dict.fromkeys(
        [
            "supporting_ok",
            "supporting_not_ok",
            "distinct_fingerprints",
            "contradicting_same_version",
            "contradicting_other_version",
            "confirmed_current",
            "confirmed_other",
            "rejected_current",
            "rejected_other",
        ],
        0,
    )
    return {**base, **over}


def assessment(status: str, **over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "claim_id": "claim:x",
        "name": "module:src/shop/cart.py depends_on module:src/shop/pricing.py",
        "status": status,
        "meaning": "formal meaning text",
        "current_fingerprint": CUR,
        "validated_by": [],
        "outcome": "none",
        "outcomes": [],
        "rederivation": None,
        "supporting": [],
        "contradicting": [],
        "reviews": [],
        "counts": counts(),
        "differs_across_versions": False,
        "corroboration": "none",
        "sources": [],
    }
    return {**base, **over}


def conflicting() -> dict[str, Any]:
    """The demo: an old run found it, a person confirmed it for the current code, and the current
    source and a current run say it is not there."""
    return assessment(
        "conflicting",
        supporting=[found("run-old", OLD)],
        contradicting=[missing("run-new", CUR, True)],
        reviews=[review()],
        rederivation={"holding": "refuted", "evidence": []},
        counts=counts(
            supporting_ok=1,
            distinct_fingerprints=1,
            contradicting_same_version=1,
            confirmed_current=1,
        ),
        corroboration="contradicted",
        sources=[{"source_class": "person", "records": ["review:#1"]}],
    )


def joined(records: list[dict[str, Any]]) -> list[str]:
    return [r["before"] + (r.get("after") or "") + " " + r["sub"] for r in records]


def test_a_conflict_names_what_is_on_each_side_for_the_current_code() -> None:
    say = call("headline", conflicting())
    text = say["text"]
    assert "For the current code" in text
    assert "a person's review says it holds" in text
    assert "a run that listed the dependencies without it and re-reading the current source" in text
    assert "say it does not" in text
    assert "unresolved" in text
    assert "experience review" in say["next"] and "never edited" in say["next"]


def test_records_are_sentences_that_say_which_code_each_one_looked_at() -> None:
    recs = call("records", conflicting())
    pro, con = joined(recs["pro"]), joined(recs["con"])
    assert len(pro) == 2 and len(con) == 2
    assert any("older code (" + OLD[:12] in p and "stored record still matches" in p for p in pro)
    assert any("A person confirmed it" in p and "checked by hand" in p for p in pro)
    assert any("not verified" in p for p in pro)
    assert any("same code the claim was checked against" in c for c in con)
    assert any("real disagreement" in c for c in con)
    assert any("not there" in c for c in con)
    assert recs["pro"][0]["runId"] == "run-old" and recs["con"][0]["runId"] == "run-new"


def test_a_difference_on_other_code_is_not_called_a_disagreement() -> None:
    a = assessment(
        "validated",
        validated_by=["rederived"],
        rederivation={"holding": "holds", "evidence": []},
        contradicting=[missing("run-old", OLD, False)],
    )
    con = call("records", a)["con"][0]
    assert "older code" in con["sub"] and "may simply have changed" in con["sub"]
    assert "real disagreement" not in con["sub"]


def test_a_supporting_run_whose_record_no_longer_matches_carries_a_warning() -> None:
    a = assessment("supported", supporting=[found("r", OLD, "source_changed")])
    sub = call("records", a)["pro"][0]["sub"]
    assert "no longer matches" in sub and "source_changed" in sub and "caution" in sub


def test_validated_says_how_and_that_it_only_describes_this_code() -> None:
    text = call("headline", assessment("validated", validated_by=["rederived", "reviewed"]))["text"]
    assert "re-reading the current source finds it" in text and "a person confirmed it" in text
    assert "this code version only" in text


def test_stale_and_supported_explain_why_nobody_can_say_it_holds_now() -> None:
    stale = call("headline", assessment("stale"))
    assert "Only older runs" in stale["text"] and "may no longer hold" in stale["text"]
    no_root = call("headline", assessment("supported", current_fingerprint=None))
    assert "No code folder was given" in no_root["text"] and "--root" in no_root["next"]


@pytest.mark.parametrize(
    "status",
    [
        "conflicting",
        "contested",
        "validated",
        "unknown",
        "unsupported",
        "stale",
        "supported",
        "expired",
        "environment_changed",
        "environment_unverified",
    ],
)
def test_every_status_has_a_plain_sentence_that_never_overclaims(status: str) -> None:
    say = call("headline", assessment(status, validated_by=["rederived"]))
    assert len(say["text"]) > 30
    assert not OVERCLAIMS.search(say["text"]), say["text"]
    assert not OVERCLAIMS.search(say["next"] or ""), say["next"]


def test_the_key_never_says_a_status_is_proof() -> None:
    lines = call("KEY")
    assert len(lines) >= 4
    text = " ".join(lines)
    assert "None of them means proven" in text and "lead to check, not as a fact" in text


def test_code_versions_are_named_relative_to_the_code_that_was_checked() -> None:
    assert call("versionWords", CUR, CUR).startswith("the current code (")
    assert call("versionWords", OLD, CUR).startswith("older code (")
    unknown = call("versionWords", OLD, None)
    assert "not known" in unknown and OLD[:12] in unknown
    assert "not recorded" in call("versionWords", None, CUR)


def test_the_claim_text_is_restated_in_words() -> None:
    said = call("aboutClaim", "module:src/shop/cart.py depends_on module:src/shop/pricing.py")
    assert said == "src/shop/cart.py depends on src/shop/pricing.py"
    assert "could not be resolved" in call("aboutClaim", "unresolved:import:foo is_unresolved")
    assert call("aboutClaim", "something else entirely") is None


def test_the_reading_replaces_raw_counts_with_sentences() -> None:
    text = " ".join(call("reading", conflicting()))
    assert "Independent sources for the current code: person (1 record)" in text
    assert "count once" in text and "1 on the current code, 0 on other code" in text
    assert "{" not in text and '"' not in text  # no raw JSON leaks into the sentences


def test_the_current_code_note_says_when_nothing_was_checked() -> None:
    assert "code version " + CUR[:12] in call("currentCodeNote", assessment("validated"))
    unchecked = assessment("supported", current_fingerprint=None)
    assert "nobody checked" in call("currentCodeNote", unchecked)


def test_it_works_on_the_assessments_the_real_server_sends(ui_world: World) -> None:
    with serving(ui_world.config()) as c:
        rows = c.get("/api/claims").json()["data"]["claims"]
    by_id = {r["assessment"]["claim_id"]: r["assessment"] for r in rows}
    validated = by_id[A_UTIL]
    assert "holds for the current code" in call("headline", validated)["text"]
    recs = call("records", validated)
    assert recs["pro"] and recs["con"]
    for a in by_id.values():
        assert call("headline", a)["text"] and call("reading", a)
