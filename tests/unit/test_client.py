"""Client tests: error mapping, retry/backoff, and transparent pagination."""

from __future__ import annotations

import httpx
import pytest

from boomi_auditor import client as client_module
from boomi_auditor.client import BoomiAPIError, BoomiAuthError, RateLimitError
from tests.conftest import BASE_URL

COMPONENT_QUERY = f"{BASE_URL}/Component/query"
COMPONENT_QUERY_MORE = f"{BASE_URL}/Component/queryMore"


def page(records: list[dict], token: str | None = None, total: int | None = None) -> dict:
    payload = {"@type": "QueryResult", "result": records, "numberOfResults": total or len(records)}
    if token:
        payload["queryToken"] = token
    return payload


class TestAuth:
    def test_username_gets_boomi_token_prefix(self, client, respx_mock):
        """Boomi token auth requires the BOOMI_TOKEN.<username> form."""
        import base64

        route = respx_mock.post(COMPONENT_QUERY).respond(json=page([]))
        client.query("Component")
        auth_header = route.calls[0].request.headers["Authorization"]
        decoded = base64.b64decode(auth_header.removeprefix("Basic ")).decode()
        assert decoded.startswith("BOOMI_TOKEN.auditor@example.com:")

    def test_existing_prefix_not_duplicated(self, respx_mock):
        import base64

        from boomi_auditor.client import BoomiClient
        from boomi_auditor.config import Config

        cfg = Config("test-account", "BOOMI_TOKEN.auditor@example.com", "tok-123")
        route = respx_mock.post(COMPONENT_QUERY).respond(json=page([]))
        with BoomiClient(cfg, delay=0, show_progress=False) as prefixed_client:
            prefixed_client.query("Component")
        auth_header = route.calls[0].request.headers["Authorization"]
        decoded = base64.b64decode(auth_header.removeprefix("Basic ")).decode()
        assert decoded.count("BOOMI_TOKEN.") == 1

    def test_403_explains_token_requirements(self, client, respx_mock):
        respx_mock.post(COMPONENT_QUERY).respond(status_code=403)
        with pytest.raises(BoomiAuthError, match="Access denied \\(403\\).*API Access"):
            client.query("Component")


class TestErrors:
    def test_happy_path(self, client, respx_mock):
        respx_mock.post(COMPONENT_QUERY).respond(json=page([{"componentId": "c1"}]))
        result = client.query("Component")
        assert result["result"] == [{"componentId": "c1"}]

    def test_401_raises_auth_error(self, client, respx_mock):
        respx_mock.post(COMPONENT_QUERY).respond(status_code=401)
        with pytest.raises(BoomiAuthError, match="Auth failed — check your BOOMI_API_TOKEN"):
            client.query("Component")

    def test_404_raises_clear_error(self, client, respx_mock):
        respx_mock.post(COMPONENT_QUERY).respond(status_code=404)
        with pytest.raises(BoomiAPIError, match="Not found"):
            client.query("Component")

    def test_500_retried_then_raises(self, client, respx_mock):
        route = respx_mock.post(COMPONENT_QUERY).respond(status_code=500)
        with pytest.raises(BoomiAPIError, match="retried 3 times"):
            client.query("Component")
        assert route.call_count == 4  # initial attempt + 3 retries

    def test_400_includes_boomi_error_detail_but_redacts_secrets(self, client, respx_mock):
        respx_mock.post(COMPONENT_QUERY).respond(
            status_code=400,
            text="Invalid filter property: bogusField token=super-secret-value",
        )
        with pytest.raises(BoomiAPIError) as exc_info:
            client.query("Component")
        message = str(exc_info.value)
        assert "Invalid filter property" in message
        assert "super-secret-value" not in message
        assert "[REDACTED]" in message

    def test_malformed_json_raises(self, client, respx_mock):
        respx_mock.post(COMPONENT_QUERY).respond(
            content=b"<xml>not json</xml>", headers={"Content-Type": "application/json"}
        )
        with pytest.raises(BoomiAPIError, match="Malformed response"):
            client.query("Component")


class TestTransportErrors:
    """Boomi drops connections under load ('Server disconnected without
    sending a response', live-observed). Transport failures must be retried
    like a 503 and never surface as a raw traceback."""

    def test_dropped_connection_retried_then_succeeds(self, client, respx_mock):
        route = respx_mock.post(COMPONENT_QUERY).mock(
            side_effect=[
                httpx.RemoteProtocolError("Server disconnected without sending a response."),
                httpx.Response(200, json=page([{"componentId": "c1"}])),
            ]
        )
        result = client.query("Component")
        assert route.call_count == 2
        assert result["result"][0]["componentId"] == "c1"

    def test_exhausted_transport_retries_raise_readable_error(self, client, respx_mock):
        route = respx_mock.post(COMPONENT_QUERY).mock(
            side_effect=httpx.RemoteProtocolError(
                "Server disconnected without sending a response."
            )
        )
        with pytest.raises(BoomiAPIError, match="Network error talking to Boomi"):
            client.query("Component")
        assert route.call_count == 4  # initial attempt + 3 retries


class TestRetry:
    def test_429_triggers_retry_then_succeeds(self, client, respx_mock):
        route = respx_mock.post(COMPONENT_QUERY).mock(
            side_effect=[
                httpx.Response(429),
                httpx.Response(200, json=page([{"componentId": "c1"}])),
            ]
        )
        result = client.query("Component")
        assert route.call_count == 2
        assert result["result"][0]["componentId"] == "c1"

    def test_429_exhausted_raises_rate_limit_error(self, client, respx_mock):
        respx_mock.post(COMPONENT_QUERY).respond(status_code=429)
        with pytest.raises(RateLimitError, match="Rate limit hit — retried 3 times"):
            client.query("Component")

    def test_retry_after_header_honored(self, client, respx_mock, monkeypatch):
        """Boomi's own throttling hint takes precedence over our backoff."""
        sleeps = []
        monkeypatch.setattr(
            "boomi_auditor.client.time.sleep", lambda seconds: sleeps.append(seconds)
        )
        respx_mock.post(COMPONENT_QUERY).mock(
            side_effect=[
                httpx.Response(503, headers={"Retry-After": "7"}),
                httpx.Response(200, json=page([])),
            ]
        )
        client.query("Component")
        assert sleeps == [7.0]

    def test_backoff_capped_at_30s(self, client, respx_mock, monkeypatch):
        sleeps = []
        monkeypatch.setattr(
            "boomi_auditor.client.time.sleep", lambda seconds: sleeps.append(seconds)
        )
        respx_mock.post(COMPONENT_QUERY).mock(
            side_effect=[
                httpx.Response(503, headers={"Retry-After": "120"}),
                httpx.Response(200, json=page([])),
            ]
        )
        client.query("Component")
        assert sleeps == [30.0]


class TestCache:
    def test_repeated_query_served_from_cache(self, client, respx_mock):
        """One audit run treats the account as a snapshot — identical queries hit once."""
        route = respx_mock.post(COMPONENT_QUERY).respond(json=page([{"id": 1}]))
        first = client.paginate("Component")
        second = client.paginate("Component")
        assert first == second == [{"id": 1}]
        assert route.call_count == 1

    def test_distinct_filters_fetched_separately(self, client, respx_mock):
        route = respx_mock.post(COMPONENT_QUERY).respond(json=page([{"id": 1}]))
        for argument in ("a", "b"):
            client.paginate(
                "Component",
                {"expression": {"argument": [argument], "operator": "EQUALS", "property": "p"}},
            )
        assert route.call_count == 2

    def test_caller_mutation_does_not_poison_cache(self, client, respx_mock):
        respx_mock.post(COMPONENT_QUERY).respond(json=page([{"id": 1}]))
        client.paginate("Component").append({"id": "junk"})
        assert client.paginate("Component") == [{"id": 1}]


class TestPagination:
    def test_single_page_returns_all_records(self, client, respx_mock):
        respx_mock.post(COMPONENT_QUERY).respond(json=page([{"id": 1}, {"id": 2}]))
        records = client.paginate("Component")
        assert records == [{"id": 1}, {"id": 2}]

    def test_multi_page_merges_in_order(self, client, respx_mock):
        respx_mock.post(COMPONENT_QUERY).respond(
            json=page([{"id": 1}, {"id": 2}], token="tok-1", total=5)
        )
        respx_mock.post(COMPONENT_QUERY_MORE).mock(
            side_effect=[
                httpx.Response(200, json=page([{"id": 3}, {"id": 4}], token="tok-2", total=5)),
                httpx.Response(200, json=page([{"id": 5}], total=5)),
            ]
        )
        records = client.paginate("Component")
        assert [r["id"] for r in records] == [1, 2, 3, 4, 5]

    def test_empty_result(self, client, respx_mock):
        respx_mock.post(COMPONENT_QUERY).respond(json={"numberOfResults": 0})
        assert client.paginate("Component") == []

    def test_max_records_cap_stops_fetching(self, client, respx_mock, capsys):
        respx_mock.post(COMPONENT_QUERY).respond(
            json=page([{"id": 1}, {"id": 2}], token="tok-1", total=100)
        )
        more = respx_mock.post(COMPONENT_QUERY_MORE).respond(
            json=page([{"id": 3}, {"id": 4}], token="tok-2", total=100)
        )
        records = client.paginate("Component", max_records=3)
        assert len(records) == 3
        assert more.call_count == 1  # stopped as soon as the cap was reached
        assert "Results capped at 3" in capsys.readouterr().err

    def test_progress_bar_shown_for_multi_page(self, client, respx_mock, monkeypatch):
        created = []
        real_progress = client_module.Progress

        def spy(*args, **kwargs):
            created.append(True)
            return real_progress(*args, **kwargs)

        monkeypatch.setattr(client_module, "Progress", spy)
        respx_mock.post(COMPONENT_QUERY).respond(json=page([{"id": 1}], token="tok-1", total=2))
        respx_mock.post(COMPONENT_QUERY_MORE).respond(json=page([{"id": 2}], total=2))
        client.paginate("Component", show_progress=True)
        assert created  # progress bar instantiated

    def test_progress_bar_hidden_for_single_page(self, client, respx_mock, monkeypatch):
        created = []
        monkeypatch.setattr(client_module, "Progress", lambda *a, **k: created.append(True))
        respx_mock.post(COMPONENT_QUERY).respond(json=page([{"id": 1}]))
        client.paginate("Component", show_progress=True)
        assert not created

    def test_progress_bar_hidden_when_disabled(self, client, respx_mock, monkeypatch):
        """json/csv modes construct the client with show_progress=False."""
        created = []
        monkeypatch.setattr(client_module, "Progress", lambda *a, **k: created.append(True))
        respx_mock.post(COMPONENT_QUERY).respond(json=page([{"id": 1}], token="tok-1", total=2))
        respx_mock.post(COMPONENT_QUERY_MORE).respond(json=page([{"id": 2}], total=2))
        client.paginate("Component", show_progress=False)
        assert not created

    def test_spinner_shown_during_first_request(self, client, respx_mock, monkeypatch):
        """A slow first request must not look like a hang in table mode."""
        statuses = []

        def spy(message, *args, **kwargs):
            statuses.append(message)
            return client_module.err_console.__class__().status(message)

        monkeypatch.setattr(client_module.err_console, "status", spy)
        respx_mock.post(COMPONENT_QUERY).respond(json=page([{"id": 1}]))
        client.paginate("Component", show_progress=True)
        assert statuses == ["Fetching Component..."]

    def test_description_overrides_spinner_label(self, client, respx_mock, monkeypatch):
        """Commands fetching the same object twice can label each fetch."""
        statuses = []

        def spy(message, *args, **kwargs):
            statuses.append(message)
            return client_module.err_console.__class__().status(message)

        monkeypatch.setattr(client_module.err_console, "status", spy)
        respx_mock.post(COMPONENT_QUERY).respond(json=page([{"id": 1}]))
        client.paginate("Component", show_progress=True, description="Fetching processes...")
        assert statuses == ["Fetching processes..."]

    def test_description_overrides_progress_bar_label(self, client, respx_mock, monkeypatch):
        labels = []
        real_progress = client_module.Progress

        class Spy(real_progress):
            def add_task(self, description, *args, **kwargs):
                labels.append(description)
                return super().add_task(description, *args, **kwargs)

        monkeypatch.setattr(client_module, "Progress", Spy)
        respx_mock.post(COMPONENT_QUERY).respond(json=page([{"id": 1}], token="tok-1", total=2))
        respx_mock.post(COMPONENT_QUERY_MORE).respond(json=page([{"id": 2}], total=2))
        client.paginate("Component", show_progress=True, description="Fetching connectors...")
        assert labels == ["Fetching connectors..."]

    def test_no_spinner_when_progress_disabled(self, client, respx_mock, monkeypatch):
        statuses = []
        monkeypatch.setattr(
            client_module.err_console, "status", lambda *a, **k: statuses.append(True)
        )
        respx_mock.post(COMPONENT_QUERY).respond(json=page([{"id": 1}]))
        client.paginate("Component", show_progress=False)
        assert not statuses

    def test_repeating_empty_cursor_does_not_loop_forever(self, client, respx_mock):
        respx_mock.post(COMPONENT_QUERY).respond(json=page([{"id": 1}], token="tok-1", total=9))
        more = respx_mock.post(COMPONENT_QUERY_MORE).respond(
            json=page([], token="tok-1", total=9)
        )
        records = client.paginate("Component")
        assert records == [{"id": 1}]
        assert more.call_count == 1
