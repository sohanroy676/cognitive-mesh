"""Helpers for conductor tests (ADR-0040): a catalog of local entries, scripted clients, replies."""

import json
from pathlib import Path

from cognitive_mesh.agents import AgentRegistry, default_registry
from cognitive_mesh.config import Settings, load_settings
from cognitive_mesh.ingestion import ingest_application
from cognitive_mesh.orchestrator.conduct import ConductorInputs
from cognitive_mesh.orchestrator.routing_evidence import EvidenceTable
from cognitive_mesh.providers import ModelClient, ModelRequest, ModelResponse, load_catalog
from cognitive_mesh.providers.catalog import Catalog, CatalogEntry
from cognitive_mesh.providers.factory import entry_problem
from cognitive_mesh.traces import CallListing

FIXTURES = Path(__file__).parent / "fixtures"
EXPLAIN_OK = '{"summary": "a project file", "symbols": []}'


def _entry(name: str, roles: str, model: str = "llama3.2") -> str:
    return (
        f'[models.{name}]\nwire = "ollama"\nbase_url = "http://localhost:11434"\n'
        f'model = "{model}"\ncontext_tokens = 8192\ntier = "free"\ndata_use = "no_training"\n'
        f"roles = {roles}\n\n"
    )


CATALOG = (
    _entry("alpha", '["code_explainer", "conductor"]')
    + _entry("beta", '["code_explainer"]')
    + _entry("gamma", '["code_explainer"]')
    + _entry("delta", '["code_explainer"]')
    + _entry("nocode", '["conductor"]')
    + '[models.remote]\nwire = "openai_compatible"\nbase_url = "https://far.example.test/v1"\n'
    'model = "far-1"\napi_key_env = "FAR_API_KEY"\ncontext_tokens = 8192\ntier = "free"\n'
    'data_use = "may_train"\nroles = ["code_explainer"]\n'
)


def write_catalog(directory: Path, text: str = CATALOG) -> Path:
    path = directory / "catalog.toml"
    path.write_text(text, encoding="utf-8")
    return path


def catalog_and_settings(directory: Path, text: str = CATALOG) -> tuple[Catalog, Settings]:
    path = write_catalog(directory, text)
    settings = load_settings(provider="catalog", model="alpha", model_catalog=path)
    return load_catalog(path), settings


def empty_listing() -> CallListing:
    return CallListing(rows=(), malformed_runs=(), runs_total=0, runs_with_calls=0)


def make_inputs(
    directory: Path,
    *,
    registry: AgentRegistry | None = None,
    evidence: EvidenceTable | None = None,
    current_project: str | None = None,
) -> ConductorInputs:
    catalog, settings = catalog_and_settings(directory)
    ingestion = ingest_application(FIXTURES / "sample_app")
    return ConductorInputs(
        registry=registry or default_registry(),
        catalog=catalog,
        problem=lambda entry: entry_problem(settings, entry),
        evidence=evidence or EvidenceTable(empty_listing()),
        files=[f.ref.path for f in ingestion.files],
        current_project=current_project,
    )


STEPS = [
    {"step_id": "report", "capability": "unresolved_report"},
    {
        "step_id": "explain",
        "capability": "explain_file",
        "inputs": {"path": "pyproject.toml"},
        "depends_on": ["report"],
    },
]
ASSIGN = {"explain": {"model": "alpha", "reason": "a small file"}}


def reply(
    steps: object = None, assignments: object = None, reasoning: object = "keeps it simple"
) -> str:
    return json.dumps(
        {
            "steps": STEPS if steps is None else steps,
            "assignments": ASSIGN if assignments is None else assignments,
            "reasoning": reasoning,
        }
    )


def response(text: str, provider: str = "fake", model: str = "fake-1") -> ModelResponse:
    return ModelResponse(provider=provider, model=model, text=text)


class ScriptedClient:
    """Plays outcomes in order: a ModelResponse, a string (wrapped), or an exception to raise."""

    provider = "fake"
    model = "fake-1"

    def __init__(self, *outcomes: object, repeat: bool = False) -> None:
        self._outcomes = list(outcomes)
        self._repeat = repeat
        self.requests: list[ModelRequest] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        outcome = self._outcomes[0] if self._repeat else self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        if isinstance(outcome, str):
            return response(outcome)
        assert isinstance(outcome, ModelResponse)
        return outcome

    def payloads(self) -> list[dict[str, object]]:
        return [json.loads(r.prompt) for r in self.requests]


class Fleet:
    """Builds one scripted client per entry id and remembers them."""

    def __init__(self, scripts: dict[str, list[object]]) -> None:
        self.scripts = scripts
        self.clients: dict[str, ScriptedClient] = {}

    def __call__(self, entry_id: str, entry: CatalogEntry) -> ModelClient:
        client = ScriptedClient(*self.scripts.get(entry_id, [response(EXPLAIN_OK)]), repeat=False)
        client.provider, client.model = "fake", f"model-of-{entry_id}"
        self.clients[entry_id] = client
        return client
