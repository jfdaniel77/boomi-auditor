"""drift command — drift/missing/encrypted findings from API-shaped data."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from boomi_auditor.cli import app
from boomi_auditor.client import BoomiAPIError
from boomi_auditor.commands.drift import collect_drift

runner = CliRunner()


class TestCollect:
    def test_finds_all_drift_kinds(self, fake_client):
        rows = collect_drift(fake_client)
        kinds = [r["kind"] for r in rows]
        assert kinds.count("DRIFT") == 3  # SAP url, Oracle db_url, Claims base_url
        assert kinds.count("MISSING") == 3  # Legacy url, Oracle db_host, SF login_endpoint
        assert kinds.count("ENCRYPTED") == 2  # SAP password, SF client_secret

    def test_prod_equals_dev_flagged(self, fake_client):
        rows = collect_drift(fake_client)
        sap_drift = next(
            r for r in rows if r["kind"] == "DRIFT" and r["connector"] == "SAP HTTP Client"
        )
        assert sap_drift["field"] == "url"
        assert sap_drift["value_to"] == "https://dev-sap.example.com"

    def test_encrypted_values_masked_everywhere(self, fake_client):
        rows = collect_drift(fake_client)
        dump = json.dumps(rows)
        assert "raw-prod-secret-DO-NOT-SHOW" not in dump
        assert "raw-sf-secret-DO-NOT-SHOW" not in dump
        assert "[ENCRYPTED]" in dump

    def test_connector_filter(self, fake_client):
        rows = collect_drift(fake_client, connector="Oracle")
        assert {r["field"] for r in rows} == {"db_url", "db_host"}

    def test_missing_extensions_warning(self, fake_client, capsys):
        collect_drift(fake_client, connector="Nonexistent Connector")
        err = capsys.readouterr().err
        assert "No extensions found for connector Nonexistent Connector" in err

    def test_unknown_env_errors_with_available_list(self, fake_client):
        with pytest.raises(BoomiAPIError, match="'STAGING' not found"):
            collect_drift(fake_client, env_to="STAGING")


class TestCli:
    def test_json_output(self, fake_client, monkeypatch):
        monkeypatch.setattr("boomi_auditor.commands.drift.build_client", lambda **kw: fake_client)
        result = runner.invoke(app, ["drift", "--format", "json"])
        assert result.exit_code == 0
        assert len(json.loads(result.stdout)) == 8
