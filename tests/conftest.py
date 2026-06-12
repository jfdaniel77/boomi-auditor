"""Shared test fixtures. All API traffic is mocked via respx — no real calls."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from boomi_auditor.client import BoomiClient
from boomi_auditor.config import Config

FIXTURES_DIR = Path(__file__).parent / "fixtures"
TEST_ACCOUNT = "test-account"
BASE_URL = f"https://api.boomi.com/api/rest/v1/{TEST_ACCOUNT}"

EMPTY_RESULT = {"@type": "QueryResult", "numberOfResults": 0, "result": []}


def load_fixture(name: str) -> dict | list:
    return json.loads((FIXTURES_DIR / name).read_text())


@pytest.fixture
def fixture_loader():
    return load_fixture


@pytest.fixture(autouse=True)
def test_credentials(monkeypatch, tmp_path):
    """Force predictable credentials and keep the developer's real config out of tests."""
    monkeypatch.setenv("BOOMI_ACCOUNT_ID", TEST_ACCOUNT)
    monkeypatch.setenv("BOOMI_USERNAME", "auditor@example.com")
    monkeypatch.setenv("BOOMI_API_TOKEN", "test-token-1234567890")
    monkeypatch.delenv("BOOMI_BASE_URL", raising=False)
    monkeypatch.setattr("boomi_auditor.config.CONFIG_PATH", tmp_path / "config.json")
    # A real .env in the project root (used for live testing) must never
    # leak into the suite — tests that clear env vars rely on this.
    monkeypatch.setattr("boomi_auditor.config.load_dotenv", lambda *a, **kw: None)


@pytest.fixture
def config() -> Config:
    return Config(TEST_ACCOUNT, "auditor@example.com", "test-token-1234567890")


@pytest.fixture
def client(config):
    with BoomiClient(config, delay=0, show_progress=False) as boomi_client:
        yield boomi_client


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """Retry backoff and inter-call delays must not slow the test suite down."""
    monkeypatch.setattr("boomi_auditor.client.time.sleep", lambda _seconds: None)


class FakeClient:
    """Stand-in for BoomiClient in command unit tests — no HTTP involved.

    responses maps object_type → list of records, or a callable taking the
    query filter (used for per-environment EnvironmentExtensions responses).
    """

    def __init__(self, responses: dict):
        self.responses = responses
        self.calls: list[str] = []
        self.queries: list[tuple[str, dict | None]] = []
        self.show_progress = False

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return None

    def paginate(
        self, object_type, query_filter=None, *, max_records=0, show_progress=None, description=None
    ):
        self.calls.append(object_type)
        self.queries.append((object_type, query_filter))
        data = self.responses.get(object_type, [])
        if callable(data):
            data = data(query_filter)
        if max_records:
            data = data[:max_records]
        return list(data)


def filter_args(query_filter: dict | None, prop: str) -> list:
    """Collect EQUALS arguments for one property from a (possibly nested) filter."""
    if not query_filter:
        return []
    found: list = []

    def walk(expression: dict) -> None:
        if expression.get("property") == prop:
            found.extend(expression.get("argument") or [])
        for nested in expression.get("nestedExpression") or []:
            walk(nested)

    walk(query_filter.get("expression") or {})
    return found


def extensions_by_env_id(query_filter) -> list[dict]:
    env_ids = filter_args(query_filter, "environmentId")
    fixture_name = {"env-dev": "extensions_dev.json", "env-prod": "extensions_prod.json"}.get(
        env_ids[0] if env_ids else None
    )
    if fixture_name is None:
        return []
    return load_fixture(fixture_name)["result"]


def references_by_parent_id(query_filter) -> list[dict]:
    parent_ids = filter_args(query_filter, "parentComponentId")
    return [
        ref
        for ref in load_fixture("component_references.json")["result"]
        if ref["parentComponentId"] in parent_ids
    ]


def component_metadata_by_type(query_filter) -> list[dict]:
    """ComponentMetadata serves both connector and process listings — dispatch
    on the type filter the way the real API would."""
    if "process" in filter_args(query_filter, "type"):
        return load_fixture("processes_response.json")["result"]
    return load_fixture("connectors_response.json")["result"]


@pytest.fixture
def fake_client() -> FakeClient:
    """FakeClient wired with the full fixture dataset."""
    return FakeClient(
        {
            "ComponentMetadata": component_metadata_by_type,
            "Environment": load_fixture("environments_response.json")["result"],
            "DeployedPackage": load_fixture("packaged_components.json")["result"],
            "Atom": load_fixture("atoms_response.json")["result"],
            "ComponentReference": references_by_parent_id,
            "EnvironmentExtensions": extensions_by_env_id,
        }
    )


@pytest.fixture
def mock_boomi(respx_mock):
    """respx router mocking the full Boomi API surface used by the CLI."""

    def body_filter(request: httpx.Request) -> dict:
        return json.loads(request.content).get("QueryFilter") or {}

    def metadata_side_effect(request: httpx.Request) -> httpx.Response:
        records = component_metadata_by_type(body_filter(request))
        return httpx.Response(200, json={"numberOfResults": len(records), "result": records})

    def extensions_side_effect(request: httpx.Request) -> httpx.Response:
        items = extensions_by_env_id(body_filter(request))
        payload = {"numberOfResults": len(items), "result": items} if items else EMPTY_RESULT
        return httpx.Response(200, json=payload)

    def references_side_effect(request: httpx.Request) -> httpx.Response:
        refs = references_by_parent_id(body_filter(request))
        return httpx.Response(200, json={"numberOfResults": len(refs), "result": refs})

    respx_mock.post(f"{BASE_URL}/ComponentMetadata/query").mock(
        side_effect=metadata_side_effect
    )
    respx_mock.post(f"{BASE_URL}/Environment/query").respond(
        json=load_fixture("environments_response.json")
    )
    respx_mock.post(f"{BASE_URL}/DeployedPackage/query").respond(
        json=load_fixture("packaged_components.json")
    )
    respx_mock.post(f"{BASE_URL}/Atom/query").respond(json=load_fixture("atoms_response.json"))
    respx_mock.post(f"{BASE_URL}/ComponentReference/query").mock(
        side_effect=references_side_effect
    )
    respx_mock.post(f"{BASE_URL}/EnvironmentExtensions/query").mock(
        side_effect=extensions_side_effect
    )
    return respx_mock
