"""processes command — connector→process dependency map from API-shaped data."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from boomi_auditor.cli import app
from boomi_auditor.commands.processes import collect_processes

runner = CliRunner()


class TestCollect:
    def test_connector_mapped_to_all_processes(self, fake_client):
        rows = collect_processes(fake_client)
        sap_rows = [r for r in rows if r["connector"] == "SAP HTTP Client" and r["process"]]
        assert {r["process"] for r in sap_rows} == {
            "CRM_Customer_Sync_v2",
            "SAP_GL_Journal_Post",
            "Claims_Intake_Inbound",
            "Payroll_Extract",
        }

    def test_prod_only_processes_flagged(self, fake_client):
        rows = collect_processes(fake_client)
        flagged = {r["process"] for r in rows if r["notes"] and r["process"]}
        assert flagged == {"SAP_GL_Journal_Post", "Payroll_Extract"}

    def test_unused_connectors_flagged(self, fake_client):
        """Connections only — operations are excluded from unused detection,
        and Legacy SAP Connection counts as used via the HTTP GET Operation."""
        rows = collect_processes(fake_client)
        unused = {r["connector"] for r in rows if r["process"] is None}
        assert unused == {"Salesforce Connector"}

    def test_connection_resolved_through_operation(self, fake_client):
        """Processes reference operations, operations reference connections —
        the second hop must attribute the connection to the process."""
        rows = collect_processes(fake_client)
        legacy = [r for r in rows if r["connector"] == "Legacy SAP Connection" and r["process"]]
        assert {r["process"] for r in legacy} == {"Payroll_Extract"}

    def test_explicit_gap_env_pair_resolved(self, fake_client):
        rows = collect_processes(fake_client, env_from="DEV", env_to="PROD")
        flagged = {r["process"] for r in rows if r["notes"] and r["process"]}
        assert flagged == {"SAP_GL_Journal_Post", "Payroll_Extract"}

    def test_gap_flags_disabled_without_dev_prod_pair(self, capsys):
        """Two DEV-ish names + a PRD: defaults can't resolve, flags turn off
        with a note instead of breaking the command."""
        from tests.conftest import (
            FakeClient,
            component_metadata_by_type,
            references_by_parent_id,
        )

        fake = FakeClient(
            {
                "ComponentMetadata": component_metadata_by_type,
                "Environment": [
                    {"id": "env-dev", "name": "01. DEV (Deprecated)"},
                    {"id": "env-sit", "name": "97 NUS DEV BIGMEM"},
                    {"id": "env-prod", "name": "04. PRD"},
                ],
                "DeployedPackage": [],
                "Atom": [],
                "ComponentReference": references_by_parent_id,
                "EnvironmentExtensions": lambda qf: [],
            }
        )
        rows = collect_processes(fake)
        err = capsys.readouterr().err
        assert "No unambiguous DEV/PROD pair" in err
        dependency_rows = [r for r in rows if r["process"]]
        assert dependency_rows
        assert all(r["notes"] is None for r in dependency_rows)

    def test_capped_process_list_skips_unused_detection(self, fake_client, capsys):
        rows = collect_processes(fake_client, max_records=2)
        assert all(r["process"] for r in rows)
        assert "skipping unused-connector detection" in capsys.readouterr().err

    def test_persistent_reference_failure_degrades_gracefully(self, capsys):
        """A 5k-process run must survive one process whose references keep
        erroring: warn, skip unused detection, keep everything else."""
        from boomi_auditor.client import BoomiAPIError
        from tests.conftest import (
            FakeClient,
            extensions_by_env_id,
            filter_args,
            load_fixture,
            references_by_parent_id,
        )

        def flaky_references(query_filter):
            if "proc-crm" in filter_args(query_filter, "parentComponentId"):
                raise BoomiAPIError("❌ Boomi API error 503 — retried 5 times.")
            return references_by_parent_id(query_filter)

        from tests.conftest import component_metadata_by_type

        fake = FakeClient(
            {
                "ComponentMetadata": component_metadata_by_type,
                "Environment": load_fixture("environments_response.json")["result"],
                "DeployedPackage": load_fixture("packaged_components.json")["result"],
                "Atom": load_fixture("atoms_response.json")["result"],
                "ComponentReference": flaky_references,
                "EnvironmentExtensions": extensions_by_env_id,
            }
        )
        rows = collect_processes(fake)
        err = capsys.readouterr().err
        assert "References unavailable for 1 component(s)" in err
        assert "skipping unused-connector detection" in err
        # the other processes are still fully mapped
        assert {r["process"] for r in rows if r["connector"] == "Legacy SAP Connection"} == {
            "Payroll_Extract"
        }
        assert all(r["process"] for r in rows)  # no unreliable unused rows

    def test_connector_filter(self, fake_client):
        rows = collect_processes(fake_client, connector="Oracle")
        assert len(rows) == 1
        assert rows[0]["process"] == "CRM_Customer_Sync_v2"

    def test_env_filter(self, fake_client):
        rows = collect_processes(fake_client, env="SIT")
        assert {r["process"] for r in rows} == {"Claims_Intake_Inbound"}

    def test_parallel_workers_match_serial_results(self, fake_client):
        serial = collect_processes(fake_client, workers=1)
        parallel = collect_processes(fake_client, workers=8)
        assert parallel == serial
        assert len(parallel) == 9

    def test_reference_queries_pair_parent_id_with_version(self, fake_client):
        """Verified live: parentComponentId without parentVersion is a 400."""
        collect_processes(fake_client)
        ref_filters = [qf for obj, qf in fake_client.queries if obj == "ComponentReference"]
        assert len(ref_filters) == 5  # one per fixture process + one referenced operation
        for query_filter in ref_filters:
            properties = {
                e["property"] for e in query_filter["expression"]["nestedExpression"]
            }
            assert properties == {"parentComponentId", "parentVersion"}


class TestCli:
    def test_json_output(self, fake_client, monkeypatch):
        monkeypatch.setattr(
            "boomi_auditor.commands.processes.build_client", lambda **kw: fake_client
        )
        result = runner.invoke(app, ["processes", "--format", "json"])
        assert result.exit_code == 0
        rows = json.loads(result.stdout)
        assert len(rows) == 9  # 6 dependency rows + 3 unused connectors
