"""connectors command — filtering, deletion handling, deployment models."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from boomi_auditor.cli import app
from boomi_auditor.client import BoomiAPIError
from boomi_auditor.commands.connectors import collect_connectors
from tests.conftest import FakeClient, extensions_by_env_id, load_fixture

runner = CliRunner()


class TestCollect:
    def test_excludes_deleted_by_default(self, fake_client):
        rows = collect_connectors(fake_client)
        names = [r["name"] for r in rows]
        assert "Old FTP Connector" not in names
        assert len(rows) == 7  # 6 connections + 1 operation; processes/maps excluded

    def test_include_deleted_opt_in(self, fake_client):
        rows = collect_connectors(fake_client, include_deleted=True)
        deleted = [r for r in rows if r["status"] == "deleted"]
        assert len(deleted) == 1
        assert deleted[0]["name"] == "Old FTP Connector"

    def test_env_filter_scopes_environment_column(self, fake_client):
        rows = collect_connectors(fake_client, env="PROD")
        assert len(rows) == 5
        # the answer to "what is in PROD?" should not list other environments
        assert all(r["environments"] == ["PROD"] for r in rows)

    def test_unknown_env_lists_available(self, fake_client):
        expected = "'STAGING' not found. Available: DEV, PROD, QAT, SIT"
        with pytest.raises(BoomiAPIError, match=expected):
            collect_connectors(fake_client, env="STAGING")

    def test_unknown_env_fails_before_heavy_fetches(self, fake_client):
        with pytest.raises(BoomiAPIError):
            collect_connectors(fake_client, env="STAGING")
        assert "ComponentMetadata" not in fake_client.calls
        assert "DeployedPackage" not in fake_client.calls

    def test_env_substring_resolves_when_unique(self, fake_client, capsys):
        """Real accounts use names like "05. Production APIM" — a unique
        case-insensitive substring is accepted and announced on stderr."""
        rows = collect_connectors(fake_client, env="pro")
        assert len(rows) == 5
        assert all("PROD" in r["environments"] for r in rows)
        assert "Using environment 'PROD'" in capsys.readouterr().err

    def test_env_substring_ambiguous_lists_candidates(self, fake_client):
        # "t" matches both SIT and QAT
        with pytest.raises(BoomiAPIError, match="ambiguous. Matches: QAT, SIT"):
            collect_connectors(fake_client, env="t")

    def test_env_membership_from_extensions_when_not_individually_packaged(self):
        """Real accounts deploy connections inside process packages, so
        DeployedPackage never lists them — presence must come from the
        per-environment extension entries (verified against the live API)."""
        fake = FakeClient(
            {
                "ComponentMetadata": load_fixture("connectors_response.json")["result"],
                "Environment": load_fixture("environments_response.json")["result"],
                "DeployedPackage": [
                    r
                    for r in load_fixture("packaged_components.json")["result"]
                    if r["componentId"].startswith("proc-")
                ],
                "Atom": load_fixture("atoms_response.json")["result"],
                "EnvironmentExtensions": extensions_by_env_id,
            }
        )
        rows = collect_connectors(fake, env="PROD")
        names = {r["name"] for r in rows}
        assert names == {
            "SAP HTTP Client",
            "Legacy SAP Connection",
            "Oracle DB Connector",
            "Salesforce Connector",
            "Claims API Connection",
        }
    def test_type_filter_matches_name_or_type(self, fake_client):
        rows = collect_connectors(fake_client, type_filter="HTTP")
        assert {r["name"] for r in rows} == {"SAP HTTP Client", "HTTP GET Operation"}

    def test_deployed_only_excludes_unplaced_rows(self, fake_client):
        rows = collect_connectors(fake_client, deployed_only=True)
        assert rows and all(r["environments"] for r in rows)
        # the operation has no deployment/extension data in the fixtures
        assert "HTTP GET Operation" not in {r["name"] for r in rows}

    def test_type_column_uses_friendly_labels(self, fake_client):
        """Boomi's API type names (connector-settings/connector-action) are
        jargon — rows show the Build-UI labels instead."""
        rows = collect_connectors(fake_client)
        assert {r["type"] for r in rows} == {"Connection", "Operation"}

    def test_type_filter_matches_friendly_label(self, fake_client):
        rows = collect_connectors(fake_client, type_filter="operation")
        assert {r["name"] for r in rows} == {"HTTP GET Operation"}

    def test_offline_atom_warning_printed(self, fake_client, capsys):
        collect_connectors(fake_client)
        err = capsys.readouterr().err
        assert "offline Atom: dev-atom-01" in err


class TestProcessingStatus:
    """After the DeployedPackage fetch, the extension scan and report build
    are the longest silent stretch — table mode covers them with a spinner."""

    def spy_statuses(self, monkeypatch) -> list[str]:
        from contextlib import nullcontext

        statuses: list[str] = []

        def spy(message, *args, **kwargs):
            statuses.append(str(message))
            return nullcontext()

        monkeypatch.setattr("boomi_auditor.client.err_console.status", spy)
        return statuses

    def test_table_mode_shows_processing_status(self, fake_client, monkeypatch):
        statuses = self.spy_statuses(monkeypatch)
        fake_client.show_progress = True
        collect_connectors(fake_client)
        assert any("generating report" in s for s in statuses)

    def test_no_status_when_progress_disabled(self, fake_client, monkeypatch):
        statuses = self.spy_statuses(monkeypatch)
        collect_connectors(fake_client)  # FakeClient defaults to json/csv mode
        assert statuses == []


class TestServerSideFilter:
    """Type/deleted/currentVersion are filtered in the query itself so large
    accounts don't download every process/map/profile component."""

    def component_filter(self, fake_client) -> dict:
        return next(qf for obj, qf in fake_client.queries if obj == "ComponentMetadata")

    def test_filters_current_version_type_and_deleted(self, fake_client):
        collect_connectors(fake_client)
        nested = self.component_filter(fake_client)["expression"]["nestedExpression"]
        properties = [e.get("property") for e in nested]
        assert "currentVersion" in properties
        assert "deleted" in properties
        type_group = next(e for e in nested if "nestedExpression" in e)
        assert {e["argument"][0] for e in type_group["nestedExpression"]} == {
            "connector-settings",
            "connector-action",
        }

    def test_include_deleted_drops_deleted_clause(self, fake_client):
        collect_connectors(fake_client, include_deleted=True)
        nested = self.component_filter(fake_client)["expression"]["nestedExpression"]
        assert "deleted" not in [e.get("property") for e in nested]

    def test_deployed_packages_filtered_to_active(self, fake_client):
        collect_connectors(fake_client)
        query_filter = next(qf for obj, qf in fake_client.queries if obj == "DeployedPackage")
        assert query_filter["expression"] == {
            "argument": ["true"],
            "operator": "EQUALS",
            "property": "active",
        }


class TestCli:
    def test_json_output(self, fake_client, monkeypatch):
        monkeypatch.setattr(
            "boomi_auditor.commands.connectors.build_client", lambda **kw: fake_client
        )
        result = runner.invoke(app, ["connectors", "--format", "json"])
        assert result.exit_code == 0
        rows = json.loads(result.stdout)
        assert len(rows) == 7

    def test_output_requires_csv_or_json(self, fake_client, monkeypatch, tmp_path):
        monkeypatch.setattr(
            "boomi_auditor.commands.connectors.build_client", lambda **kw: fake_client
        )
        result = runner.invoke(app, ["connectors", "--output", str(tmp_path / "x.csv")])
        assert result.exit_code == 1
