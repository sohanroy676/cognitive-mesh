"""Per-step model routing with fallback in catalog order (ADR-0040).

A `RoutedClient` stands where the single `RecordingClient` stood. The orchestrator's `on_attempt`
hook (`begin_attempt`) tells it which step is running; it then uses that step's assigned catalog
entry. When that entry fails with a retryable provider error (rate limit, timeout, unavailable),
the next entries that list the role, in catalog file order, take over, at most `MAX_FALLBACKS`.
Every call, including the failed ones, is recorded under the entry that made it. Errors that a
second model cannot fix (auth, bad response, other) do not switch models.
"""

from collections.abc import Callable
from dataclasses import dataclass

from cognitive_mesh.config import ConfigError, Settings
from cognitive_mesh.providers.base import (
    ModelClient,
    ModelRequest,
    ModelResponse,
    ProviderError,
    ProviderErrorKind,
)
from cognitive_mesh.providers.catalog import Catalog, CatalogEntry
from cognitive_mesh.providers.factory import build_entry_client, entry_problem, remote_warning
from cognitive_mesh.providers.recording import CallLog, RecordingClient

MAX_FALLBACKS = 2
SWITCH_KINDS = frozenset(
    {ProviderErrorKind.RATE_LIMITED, ProviderErrorKind.TIMEOUT, ProviderErrorKind.UNAVAILABLE}
)


@dataclass(frozen=True)
class Switch:
    """One change of model within an attempt: `from_entry` failed with `kind`."""

    agent_task_id: str
    from_entry: str
    to_entry: str
    kind: str


def fallback_order(
    catalog: Catalog,
    assigned: str,
    role: str,
    problem: Callable[[CatalogEntry], str | None],
) -> list[str]:
    """The assigned entry, then up to `MAX_FALLBACKS` more in catalog file order.

    Only entries that list `role` and have no static problem qualify (key set, opt-ins given).
    """
    order = [assigned]
    for entry_id, entry in catalog.models.items():
        if len(order) > MAX_FALLBACKS:
            break
        if entry_id != assigned and role in entry.roles and problem(entry) is None:
            order.append(entry_id)
    return order


class RoutedClient:
    """A `ModelClient` that sends each step's calls to the entry assigned to that step."""

    provider = "routed"
    model = "per-step"

    def __init__(
        self,
        catalog: Catalog,
        settings: Settings,
        calls: CallLog,
        assignments: dict[str, str],
        *,
        warn: Callable[[str], None] = lambda message: None,
        build: Callable[[str, CatalogEntry], ModelClient] | None = None,
    ) -> None:
        self._catalog = catalog
        self._settings = settings
        self._calls = calls
        self.assignments = dict(assignments)
        self._warn = warn
        self._build = build if build is not None else build_entry_client
        self._clients: dict[str, RecordingClient] = {}
        self._step: tuple[str, str] | None = None  # (step_id, agent id = role)
        self._agent_task_id = ""
        self.switches: list[Switch] = []

    def begin_attempt(
        self, agent_task_id: str, step_id: str, agent_id: str, capability: str
    ) -> None:
        """The orchestrator's `on_attempt` hook: also starts the call log's attempt."""
        self._calls.begin_attempt(agent_task_id, step_id, agent_id, capability)
        self._step = (step_id, agent_id)
        self._agent_task_id = agent_task_id

    def complete(self, request: ModelRequest) -> ModelResponse:
        if self._step is None:
            raise ProviderError(ProviderErrorKind.OTHER, "no step is running")
        step_id, role = self._step
        assigned = self.assignments.get(step_id)
        if assigned is None or assigned not in self._catalog.models:
            raise ProviderError(ProviderErrorKind.OTHER, "the step has no assigned model")
        order = fallback_order(
            self._catalog,
            assigned,
            role,
            lambda entry: entry_problem(self._settings, entry),
        )
        last = ProviderError(ProviderErrorKind.UNAVAILABLE, "no model could be used")
        for position, entry_id in enumerate(order):
            client = self._client(entry_id)
            if client is None:  # cannot be built now: counts as unavailable, try the next
                last = ProviderError(ProviderErrorKind.UNAVAILABLE, "the model could not be built")
                self._note_switch(order, position, last.kind)
                continue
            try:
                return client.complete(request)
            except ProviderError as exc:
                if exc.kind not in SWITCH_KINDS:
                    raise
                last = exc
                self._note_switch(order, position, exc.kind)
        raise last

    def _note_switch(self, order: list[str], position: int, kind: ProviderErrorKind) -> None:
        if position + 1 < len(order):
            self.switches.append(
                Switch(self._agent_task_id, order[position], order[position + 1], kind.value)
            )

    def _client(self, entry_id: str) -> RecordingClient | None:
        if entry_id not in self._clients:
            entry = self._catalog.models[entry_id]
            try:
                inner = self._build(entry_id, entry)
            except ConfigError:
                return None
            if entry.remote:
                self._warn(remote_warning(entry_id, entry))
            self._clients[entry_id] = RecordingClient(inner, self._calls, entry_id)
        return self._clients[entry_id]
