"""duplicates command — all three duplication types from API-shaped data."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from boomi_auditor.cli import app
from boomi_auditor.commands.duplicates import collect_duplicates

runner = CliRunner()


class TestCollect:
    def test_all_three_types_detected(self, fake_client):
        rows = collect_duplicates(fake_client)
        types = {r["type"] for r in rows}
        assert types == {"TYPE A", "TYPE B", "TYPE C"}

    def test_type_a_detail(self, fake_client):
        rows = collect_duplicates(fake_client)
        type_a = next(r for r in rows if r["type"] == "TYPE A")
        assert 'Duplicate name in DEV: "SAP HTTP Client" v3' in type_a["message"]
        assert type_a["count"] == 2

    def test_type_b_detail(self, fake_client):
        rows = collect_duplicates(fake_client)
        type_b = next(r for r in rows if r["type"] == "TYPE B")
        assert "Same endpoint in PROD: https://dev-sap.example.com" in type_b["message"]

    def test_type_c_detail(self, fake_client):
        rows = collect_duplicates(fake_client)
        type_c = next(r for r in rows if r["type"] == "TYPE C")
        assert type_c["severity"] == "info"
        assert 'Identical config across DEV/PROD: "Claims API Connection"' in type_c["message"]

    def test_env_filter_limits_to_one_environment(self, fake_client):
        rows = collect_duplicates(fake_client, env="DEV")
        # only TYPE A lives entirely in DEV; B is PROD-only, C needs two envs
        assert {r["type"] for r in rows} == {"TYPE A"}

    def test_encrypted_values_never_in_findings(self, fake_client):
        rows = collect_duplicates(fake_client)
        assert "raw-prod-secret-DO-NOT-SHOW" not in json.dumps(rows)


class TestCli:
    def test_json_output(self, fake_client, monkeypatch):
        monkeypatch.setattr(
            "boomi_auditor.commands.duplicates.build_client", lambda **kw: fake_client
        )
        result = runner.invoke(app, ["duplicates", "--format", "json"])
        assert result.exit_code == 0
        assert len(json.loads(result.stdout)) == 3
