"""End-to-end CLI tests via Typer CliRunner with the full API surface mocked."""

from __future__ import annotations

import json

import httpx
from typer.testing import CliRunner

from boomi_auditor.cli import app
from tests.conftest import BASE_URL, load_fixture

runner = CliRunner()


def stderr_of(result) -> str:
    try:
        return result.stderr
    except ValueError:  # older click versions mix streams
        return result.output


class TestConnectors:
    def test_lists_active_connectors_json(self, mock_boomi):
        result = runner.invoke(app, ["connectors", "--format", "json"])
        assert result.exit_code == 0
        rows = json.loads(result.stdout)
        names = {r["name"] for r in rows}
        assert "Old FTP Connector" not in names  # deleted components filtered out
        assert "SAP HTTP Client" in names

    def test_include_deleted(self, mock_boomi):
        result = runner.invoke(app, ["connectors", "--include-deleted", "--format", "json"])
        names = {r["name"] for r in json.loads(result.stdout)}
        assert "Old FTP Connector" in names

    def test_env_filter_and_table_output(self, mock_boomi, monkeypatch):
        monkeypatch.setenv("COLUMNS", "200")  # stop Rich truncating cells in the test terminal
        result = runner.invoke(app, ["connectors", "--env", "PROD"])
        assert result.exit_code == 0
        assert "SAP HTTP Client" in result.stdout

    def test_unknown_environment_human_readable(self, mock_boomi):
        result = runner.invoke(app, ["connectors", "--env", "STAGING"])
        assert result.exit_code == 1
        combined = result.stdout + stderr_of(result)
        assert "Environment 'STAGING' not found" in combined
        assert "DEV" in combined and "PROD" in combined

    def test_environment_substring_match(self, mock_boomi):
        exact = runner.invoke(app, ["connectors", "--env", "PROD", "--format", "json"])
        fuzzy = runner.invoke(app, ["connectors", "--env", "pro", "--format", "json"])
        assert fuzzy.exit_code == 0
        assert json.loads(fuzzy.stdout) == json.loads(exact.stdout)

    def test_csv_output(self, mock_boomi):
        result = runner.invoke(app, ["connectors", "--format", "csv"])
        assert result.exit_code == 0
        assert result.stdout.startswith("componentId,")

    def test_env_filter_works_without_connection_packages(self, mock_boomi):
        """Connections ride inside process packages on real accounts —
        extension entries must still place them in their environments."""
        processes_only = [
            r
            for r in load_fixture("packaged_components.json")["result"]
            if r["componentId"].startswith("proc-")
        ]
        mock_boomi.post(f"{BASE_URL}/DeployedPackage/query").respond(
            json={"numberOfResults": len(processes_only), "result": processes_only}
        )
        result = runner.invoke(app, ["connectors", "--env", "PROD", "--format", "json"])
        assert result.exit_code == 0
        rows = json.loads(result.stdout)
        names = {r["name"] for r in rows}
        assert {"SAP HTTP Client", "Salesforce Connector"} <= names
        # --env scopes the environments column to the asked-for environment
        assert all(r["environments"] == ["PROD"] for r in rows)

    def test_deployed_package_query_filtered_to_active(self, mock_boomi):
        result = runner.invoke(app, ["connectors", "--format", "json"])
        assert result.exit_code == 0
        deployment_calls = [
            call
            for call in mock_boomi.calls
            if call.request.url.path.endswith("/DeployedPackage/query")
        ]
        body = json.loads(deployment_calls[0].request.content)
        assert body["QueryFilter"]["expression"]["property"] == "active"


class TestConnections:
    def test_no_type_column_and_friendly_connector_types(self, mock_boomi):
        connections = runner.invoke(app, ["connections", "--format", "json"])
        assert connections.exit_code == 0
        rows = json.loads(connections.stdout)
        assert rows and all("type" not in r for r in rows)

        connectors = runner.invoke(app, ["connectors", "--format", "json"])
        types = {r["type"] for r in json.loads(connectors.stdout)}
        assert types == {"Connection", "Operation"}

    def test_deployed_only_flag(self, mock_boomi):
        connectors = runner.invoke(
            app, ["connectors", "--deployed-only", "--format", "json"]
        )
        assert connectors.exit_code == 0
        rows = json.loads(connectors.stdout)
        assert rows and all(r["environments"] for r in rows)

        connections = runner.invoke(
            app, ["connections", "--deployed-only", "--format", "json"]
        )
        assert connections.exit_code == 0
        rows = json.loads(connections.stdout)
        assert rows and all(r["environment"] for r in rows)

    def test_extension_values_present_and_secrets_masked(self, mock_boomi):
        result = runner.invoke(app, ["connections", "--env", "PROD", "--format", "json"])
        assert result.exit_code == 0
        rows = json.loads(result.stdout)
        assert rows and all(r["environment"] == "PROD" for r in rows)
        by_name = {r["name"]: r for r in rows}
        assert by_name["Claims API Connection"]["url"] == "https://api.claims.example.com"
        assert by_name["Salesforce Connector"]["credentials"] == "[ENCRYPTED]"
        assert "DO-NOT-SHOW" not in result.stdout


class TestProcessingStatus:
    def spy_statuses(self, monkeypatch) -> list[str]:
        from contextlib import nullcontext

        statuses: list[str] = []

        def spy(message, *args, **kwargs):
            statuses.append(str(message))
            return nullcontext()

        monkeypatch.setattr("boomi_auditor.client.err_console.status", spy)
        return statuses

    def test_table_mode_shows_post_fetch_status(self, mock_boomi, monkeypatch):
        statuses = self.spy_statuses(monkeypatch)
        result = runner.invoke(app, ["connectors"])
        assert result.exit_code == 0
        assert any("generating report" in s for s in statuses)

    def test_json_mode_shows_no_status(self, mock_boomi, monkeypatch):
        statuses = self.spy_statuses(monkeypatch)
        result = runner.invoke(app, ["connectors", "--format", "json"])
        assert result.exit_code == 0
        assert statuses == []

    def test_csv_to_file_keeps_status(self, mock_boomi, monkeypatch, tmp_path):
        """With --output the report goes to a file, so progress stays on."""
        statuses = self.spy_statuses(monkeypatch)
        result = runner.invoke(
            app,
            ["connectors", "--format", "csv", "--output", str(tmp_path / "report.csv")],
        )
        assert result.exit_code == 0
        assert any("generating report" in s for s in statuses)

    def test_processes_fetch_labels_are_distinct(self, mock_boomi, monkeypatch):
        """The two ComponentMetadata fetches must not show identical labels."""
        statuses = self.spy_statuses(monkeypatch)
        result = runner.invoke(app, ["processes"])
        assert result.exit_code == 0
        assert "Fetching processes..." in statuses
        assert "Fetching connectors..." in statuses
        assert "Fetching ComponentMetadata..." not in statuses


class TestPagination:
    def test_multi_page_returns_all_records(self, mock_boomi):
        page_one = {
            "numberOfResults": 13,
            "queryToken": "tok-1",
            "result": load_fixture("connectors_response.json")["result"][:5],
        }
        page_two = {
            "numberOfResults": 13,
            "result": load_fixture("connectors_response.json")["result"][5:],
        }
        mock_boomi.post(f"{BASE_URL}/ComponentMetadata/query").respond(json=page_one)
        mock_boomi.post(f"{BASE_URL}/ComponentMetadata/queryMore").respond(json=page_two)

        result = runner.invoke(app, ["connectors", "--format", "json"])
        assert result.exit_code == 0
        # all 7 active connectors present despite the data arriving in two pages
        assert len(json.loads(result.stdout)) == 7


class TestDuplicates:
    def test_all_three_types_surfaced(self, mock_boomi):
        result = runner.invoke(app, ["duplicates", "--format", "json"])
        assert result.exit_code == 0
        rows = json.loads(result.stdout)
        assert {r["type"] for r in rows} == {"TYPE A", "TYPE B", "TYPE C"}


class TestDrift:
    def test_flags_prod_equals_dev_and_masks_encrypted(self, mock_boomi):
        result = runner.invoke(app, ["drift", "--format", "json"])
        assert result.exit_code == 0
        rows = json.loads(result.stdout)
        kinds = {r["kind"] for r in rows}
        assert {"DRIFT", "MISSING", "ENCRYPTED"} <= kinds
        dump = json.dumps(rows)
        assert "raw-prod-secret-DO-NOT-SHOW" not in dump
        assert "raw-sf-secret-DO-NOT-SHOW" not in dump
        assert "[ENCRYPTED]" in dump

    def test_env_pair_flags(self, mock_boomi):
        result = runner.invoke(
            app, ["drift", "--env-from", "DEV", "--env-to", "PROD", "--format", "json"]
        )
        sap = [
            r
            for r in json.loads(result.stdout)
            if r["connector"] == "SAP HTTP Client" and r["kind"] == "DRIFT"
        ]
        assert sap and sap[0]["field"] == "url"


class TestProcesses:
    def test_dependency_map(self, mock_boomi):
        result = runner.invoke(app, ["processes", "--format", "json"])
        assert result.exit_code == 0
        rows = json.loads(result.stdout)
        sap = {r["process"] for r in rows if r["connector"] == "SAP HTTP Client"}
        assert "CRM_Customer_Sync_v2" in sap
        assert any(r["notes"] and "missing in DEV" in r["notes"] for r in rows)

    def test_workers_option_matches_default(self, mock_boomi):
        default = runner.invoke(app, ["processes", "--format", "json"])
        tuned = runner.invoke(app, ["processes", "--workers", "2", "--format", "json"])
        assert tuned.exit_code == 0
        assert json.loads(tuned.stdout) == json.loads(default.stdout)


class TestFullAudit:
    def test_all_json_report(self, mock_boomi):
        result = runner.invoke(app, ["all", "--format", "json"])
        assert result.exit_code == 0
        report = json.loads(result.stdout)
        assert set(report) == {"connectors", "duplicates", "drift", "processes"}
        assert report["duplicates"]

    def test_all_skips_drift_when_default_envs_ambiguous(self, mock_boomi):
        """An account with no clean DEV/PROD pair still gets a full report —
        the drift section is empty with a warning, not a hard failure."""
        ambiguous_envs = {
            "numberOfResults": 3,
            "result": [
                {"id": "env-dev", "name": "01. DEV (Deprecated)"},
                {"id": "env-sit", "name": "97 NUS DEV BIGMEM"},
                {"id": "env-prod", "name": "04. PRD"},
            ],
        }
        mock_boomi.post(f"{BASE_URL}/Environment/query").respond(json=ambiguous_envs)
        result = runner.invoke(app, ["all", "--format", "json"])
        assert result.exit_code == 0
        report = json.loads(result.stdout)
        assert report["drift"] == []
        assert report["connectors"]
        assert "Drift section skipped" in stderr_of(result)

    def test_all_caches_repeated_queries(self, mock_boomi):
        """Sub-audits share one client; identical queries must hit the API once."""
        result = runner.invoke(app, ["all", "--format", "json"])
        assert result.exit_code == 0
        env_calls = [
            call
            for call in mock_boomi.calls
            if call.request.url.path.endswith("/Environment/query")
        ]
        assert len(env_calls) == 1


class TestOutputFile:
    def test_writes_file(self, mock_boomi, tmp_path):
        target = tmp_path / "report.csv"
        result = runner.invoke(
            app, ["connectors", "--format", "csv", "--output", str(target)]
        )
        assert result.exit_code == 0
        assert target.exists()

    def test_overwrite_prompt_declined(self, mock_boomi, tmp_path):
        target = tmp_path / "report.csv"
        target.write_text("precious data")
        result = runner.invoke(
            app,
            ["connectors", "--format", "csv", "--output", str(target)],
            input="n\n",
        )
        assert result.exit_code == 0
        assert target.read_text() == "precious data"

    def test_force_skips_prompt(self, mock_boomi, tmp_path):
        target = tmp_path / "report.csv"
        target.write_text("old")
        result = runner.invoke(
            app,
            ["connectors", "--format", "csv", "--output", str(target), "--force"],
        )
        assert result.exit_code == 0
        assert target.read_text() != "old"


class TestErrors:
    def test_bad_credentials_human_readable(self, respx_mock):
        respx_mock.post(f"{BASE_URL}/ComponentMetadata/query").respond(status_code=401)
        result = runner.invoke(app, ["connectors", "--format", "json"])
        assert result.exit_code == 1
        assert "Auth failed — check your BOOMI_API_TOKEN" in (
            result.stdout + stderr_of(result)
        )

    def test_dropped_connection_human_readable(self, respx_mock):
        """Boomi closing the socket mid-run (live-observed under load) must
        end in a readable message, never a raw traceback."""
        respx_mock.post(f"{BASE_URL}/ComponentMetadata/query").mock(
            side_effect=httpx.RemoteProtocolError(
                "Server disconnected without sending a response."
            )
        )
        result = runner.invoke(app, ["connectors", "--format", "json"])
        assert result.exit_code == 1
        combined = result.stdout + stderr_of(result)
        assert "Network error talking to Boomi" in combined
        assert "Traceback" not in combined

    def test_init_writes_config(self, tmp_path, monkeypatch):
        config_path = tmp_path / "cfg" / "config.json"
        monkeypatch.setattr("boomi_auditor.cli.CONFIG_PATH", config_path)
        result = runner.invoke(
            app, ["init"], input="acct-1\nuser@example.com\ntok-secret\n"
        )
        assert result.exit_code == 0
        assert config_path.exists()
        assert (config_path.stat().st_mode & 0o777) == 0o600
