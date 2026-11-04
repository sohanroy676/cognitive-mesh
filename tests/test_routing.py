"""Per-step routing and fallback in catalog order (ADR-0040)."""

from pathlib import Path

import pytest

from cognitive_mesh.providers import ModelClient, ModelRequest, ProviderError, ProviderErrorKind
from cognitive_mesh.providers.catalog import CatalogEntry
from cognitive_mesh.providers.recording import CallLog, CallResult
from cognitive_mesh.providers.routing import MAX_FALLBACKS, RoutedClient, fallback_order
from conductor_support import (
    CATALOG,
    Fleet,
    ScriptedClient,
    catalog_and_settings,
    response,
)

REQUEST = ModelRequest(system="s", prompt="p", timeout_s=5.0, max_output_chars=1000)


def _router(
    tmp_path: Path, scripts: dict[str, list[object]], assigned: str = "alpha"
) -> tuple[RoutedClient, CallLog, Fleet]:
    catalog, settings = catalog_and_settings(tmp_path)
    log, fleet = CallLog(), Fleet(scripts)
    router = RoutedClient(catalog, settings, log, {"explain": assigned}, build=fleet)
    router.begin_attempt("t.explain.1", "explain", "code_explainer", "explain_file")
    return router, log, fleet


def test_a_step_uses_its_assigned_entry(tmp_path: Path) -> None:
    router, log, fleet = _router(tmp_path, {"beta": [response("from beta")]}, assigned="beta")
    assert router.complete(REQUEST).text == "from beta"
    assert list(fleet.clients) == ["beta"]
    (record,) = log.records
    assert (record.entry_id, record.model, record.role, record.task_type) == (
        "beta",
        "model-of-beta",
        "code_explainer",
        "explain_file",
    )


@pytest.mark.parametrize(
    "kind",
    [ProviderErrorKind.RATE_LIMITED, ProviderErrorKind.TIMEOUT, ProviderErrorKind.UNAVAILABLE],
)
def test_a_retryable_failure_moves_to_the_next_entry_in_catalog_order(
    tmp_path: Path, kind: ProviderErrorKind
) -> None:
    router, log, fleet = _router(
        tmp_path, {"alpha": [ProviderError(kind)], "beta": [response("from beta")]}
    )
    assert router.complete(REQUEST).text == "from beta"
    assert [(r.entry_id, r.result.value) for r in log.records] == [
        ("alpha", kind.value),
        ("beta", "ok"),
    ]
    assert [r.call_no for r in log.records] == [1, 2]
    (switch,) = router.switches
    assert (switch.from_entry, switch.to_entry, switch.kind) == ("alpha", "beta", kind.value)
    assert "gamma" not in fleet.clients  # never built: not needed


def test_fallback_stops_after_two_more_entries_and_the_last_error_is_raised(
    tmp_path: Path,
) -> None:
    limited = ProviderError(ProviderErrorKind.RATE_LIMITED)
    router, log, fleet = _router(
        tmp_path,
        {
            "alpha": [limited],
            "beta": [limited],
            "gamma": [ProviderError(ProviderErrorKind.TIMEOUT)],
        },
    )
    with pytest.raises(ProviderError) as caught:
        router.complete(REQUEST)
    assert caught.value.kind is ProviderErrorKind.TIMEOUT
    assert [r.entry_id for r in log.records] == ["alpha", "beta", "gamma"]
    assert "delta" not in fleet.clients and MAX_FALLBACKS == 2
    assert all(r.result is not CallResult.OK for r in log.records)  # never a silent success
    assert [(s.from_entry, s.to_entry) for s in router.switches] == [
        ("alpha", "beta"),
        ("beta", "gamma"),
    ]


@pytest.mark.parametrize(
    "kind", [ProviderErrorKind.AUTH, ProviderErrorKind.BAD_RESPONSE, ProviderErrorKind.OTHER]
)
def test_errors_another_model_cannot_fix_do_not_switch(
    tmp_path: Path, kind: ProviderErrorKind
) -> None:
    router, log, fleet = _router(tmp_path, {"alpha": [ProviderError(kind)]})
    with pytest.raises(ProviderError) as caught:
        router.complete(REQUEST)
    assert caught.value.kind is kind
    assert [r.entry_id for r in log.records] == ["alpha"] and router.switches == []
    assert list(fleet.clients) == ["alpha"]


def test_a_single_usable_entry_fails_explicitly_when_it_is_limited(tmp_path: Path) -> None:
    only_alpha = CATALOG.split("[models.beta]")[0]  # alpha is the only entry
    catalog, settings = catalog_and_settings(tmp_path, only_alpha)
    log = CallLog()
    fleet = Fleet({"alpha": [ProviderError(ProviderErrorKind.RATE_LIMITED)]})
    router = RoutedClient(catalog, settings, log, {"explain": "alpha"}, build=fleet)
    router.begin_attempt("t.explain.1", "explain", "code_explainer", "explain_file")
    with pytest.raises(ProviderError, match="rate_limited"):
        router.complete(REQUEST)
    assert router.switches == []


def test_fallback_skips_entries_without_the_role_or_with_a_static_problem(tmp_path: Path) -> None:
    catalog, settings = catalog_and_settings(tmp_path)
    from cognitive_mesh.providers.factory import entry_problem

    order = fallback_order(catalog, "alpha", "code_explainer", lambda e: entry_problem(settings, e))
    # nocode lacks the role; remote has no opt-in and no key; four candidates remain, two are used
    assert order == ["alpha", "beta", "gamma"]
    assert fallback_order(catalog, "alpha", "conductor", lambda e: entry_problem(settings, e)) == [
        "alpha",
        "nocode",
    ]


def test_an_entry_that_cannot_be_built_counts_as_unavailable(tmp_path: Path) -> None:
    from cognitive_mesh.config import ConfigError

    catalog, settings = catalog_and_settings(tmp_path)
    log, built = CallLog(), []

    def build(entry_id: str, entry: CatalogEntry) -> ModelClient:
        built.append(entry_id)
        if entry_id == "alpha":
            raise ConfigError("cannot build")
        return ScriptedClient(response("from beta"))

    router = RoutedClient(catalog, settings, log, {"explain": "alpha"}, build=build)
    router.begin_attempt("t.explain.1", "explain", "code_explainer", "explain_file")
    assert router.complete(REQUEST).text == "from beta"
    assert built == ["alpha", "beta"]
    assert [s.kind for s in router.switches] == ["unavailable"]


def test_a_step_with_no_assignment_is_an_explicit_failure(tmp_path: Path) -> None:
    router, _, _ = _router(tmp_path, {})
    router.begin_attempt("t.other.1", "other", "code_explainer", "explain_file")
    with pytest.raises(ProviderError, match="no assigned model"):
        router.complete(REQUEST)


def test_a_call_before_any_attempt_is_refused(tmp_path: Path) -> None:
    catalog, settings = catalog_and_settings(tmp_path)
    router = RoutedClient(catalog, settings, CallLog(), {}, build=Fleet({}))
    with pytest.raises(ProviderError, match="no step is running"):
        router.complete(REQUEST)
