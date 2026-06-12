"""connections command — per-environment connection details, secrets masked."""

from __future__ import annotations

import json

from boomi_auditor.commands import connection_settings
from boomi_auditor.commands.connections import collect_connections
from tests.conftest import FakeClient, extensions_by_env_id, load_fixture


def proc_only_client() -> FakeClient:
    """Realistic account: DeployedPackage lists processes only, so connection
    placement comes from extension entries alone."""
    return FakeClient(
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


class TestCollect:
    def test_lists_connections_only(self, fake_client):
        rows = collect_connections(fake_client)
        names = {r["name"] for r in rows}
        assert "HTTP GET Operation" not in names  # operations excluded
        assert "Old FTP Connector" not in names  # deleted excluded by default
        assert len({r["componentId"] for r in rows}) == 6

    def test_no_type_column(self, fake_client):
        """Every row is a connection by definition — a type column would
        repeat one value (removed on owner feedback)."""
        rows = collect_connections(fake_client)
        assert rows and all("type" not in r for r in rows)

    def test_one_row_per_environment_with_extension_values(self, fake_client):
        """Extension values are environment-specific, so each environment a
        connection lives in gets its own row carrying that env's settings."""
        rows = collect_connections(fake_client)
        sap = {r["environment"]: r for r in rows if r["componentId"] == "comp-http-1"}
        assert set(sap) == {"DEV", "PROD"}
        assert sap["DEV"]["url"] == "https://dev-sap.example.com"
        assert sap["DEV"]["user"] == "sap-dev-user"
        assert sap["PROD"]["credentials"] == "[ENCRYPTED]"

    def test_endpoint_heuristic_covers_field_variants(self, fake_client):
        rows = {
            (r["componentId"], r["environment"]): r
            for r in collect_connections(fake_client)
        }
        assert rows[("comp-claims", "PROD")]["url"] == "https://api.claims.example.com"
        assert rows[("comp-ora-1", "PROD")]["url"].startswith("jdbc:oracle:")

    def test_secrets_never_in_rows(self, fake_client):
        dump = json.dumps(collect_connections(fake_client))
        assert "DO-NOT-SHOW" not in dump
        assert "[ENCRYPTED]" in dump

    def test_env_scopes_rows(self, fake_client):
        rows = collect_connections(fake_client, env="PROD")
        assert rows and all(r["environment"] == "PROD" for r in rows)
        assert "comp-http-2" not in {r["componentId"] for r in rows}  # DEV-only

    def test_url_embedded_credentials_scrubbed(self):
        """A password inside the endpoint URL is not in a secret-named field,
        so only the URL scrub keeps it out of output."""
        env = {"id": "env-x", "name": "PROD"}
        ext = [
            {
                "connections": {
                    "connection": [
                        {
                            "id": "comp-1",
                            "name": "Risky Connection",
                            "field": [
                                {
                                    "id": "url",
                                    "value": "https://svc:hunter2@api.example.com/v1",
                                    "encryptedValueSet": False,
                                }
                            ],
                        }
                    ]
                }
            }
        ]
        fake = FakeClient({"EnvironmentExtensions": ext})
        settings = connection_settings(fake, [env])
        url = settings["PROD"]["comp-1"]["url"]
        assert "hunter2" not in url
        assert "[REDACTED]@api.example.com" in url

    def test_malformed_payloads_do_not_crash_or_leak(self):
        """Boomi payload shape varies (flat vs nested connections, a single
        field as a dict, missing ids). None must crash or surface a value
        from a secret-named field."""
        env = {"id": "env-x", "name": "PROD"}
        ext = [
            {"connections": None},  # whole block missing
            {"connections": []},  # flat empty list
            {
                "connections": [  # flat (non-nested) list shape
                    {
                        "id": "comp-flat",
                        "name": "Flat Shape",
                        "field": {  # a single field as a dict, not a list
                            "id": "password",
                            "value": "raw-should-not-show",
                            "encryptedValueSet": True,
                        },
                    },
                    {"name": "No Id — skipped", "field": []},  # missing id
                ]
            },
        ]
        fake = FakeClient({"EnvironmentExtensions": ext})
        settings = connection_settings(fake, [env])
        assert "comp-flat" in settings["PROD"]
        assert settings["PROD"]["comp-flat"]["credentials"] == "[ENCRYPTED]"
        import json

        assert "raw-should-not-show" not in json.dumps(settings)

    def test_deployed_only_hides_unplaced_connections(self):
        fake = proc_only_client()
        all_rows = collect_connections(fake)
        deployed = collect_connections(fake, deployed_only=True)
        # comp-http-2 has no extension entries → empty environment by default
        assert any(r["environment"] == "" for r in all_rows)
        assert deployed and all(r["environment"] for r in deployed)
        assert "comp-http-2" not in {r["componentId"] for r in deployed}
