"""Dependency analyzer tests — connector→process mapping and gap flags."""

from __future__ import annotations

from boomi_auditor.analyzers.dependency_analyzer import map_dependencies

PROCESSES = [
    {"name": "CRM_Customer_Sync_v2", "environments": ["DEV", "PROD"],
     "connectors": ["HTTP Client", "Oracle DB"]},
    {"name": "SAP_GL_Journal_Post", "environments": ["PROD"], "connectors": ["HTTP Client"]},
    {"name": "Claims_Intake_Inbound", "environments": ["DEV", "SIT", "PROD"],
     "connectors": ["HTTP Client"]},
    {"name": "Payroll_Extract", "environments": ["PROD"], "connectors": ["HTTP Client"]},
]

CONNECTORS = [{"name": "HTTP Client"}, {"name": "Oracle DB"}, {"name": "Salesforce Connector"}]


class TestMapping:
    def test_connector_used_by_multiple_processes(self):
        rows = map_dependencies(PROCESSES, CONNECTORS)
        http_rows = [r for r in rows if r["connector"] == "HTTP Client" and r["process"]]
        assert len(http_rows) == 4
        assert {r["process"] for r in http_rows} == {
            "CRM_Customer_Sync_v2",
            "SAP_GL_Journal_Post",
            "Claims_Intake_Inbound",
            "Payroll_Extract",
        }

    def test_process_in_prod_but_not_dev_flagged(self):
        rows = map_dependencies(PROCESSES, CONNECTORS)
        flagged = {r["process"]: r["notes"] for r in rows if r["notes"] and r["process"]}
        assert "SAP_GL_Journal_Post" in flagged
        assert "Payroll_Extract" in flagged
        assert "missing in DEV" in flagged["SAP_GL_Journal_Post"]

    def test_process_in_both_envs_not_flagged(self):
        rows = map_dependencies(PROCESSES, CONNECTORS)
        edu = [r for r in rows if r["process"] == "CRM_Customer_Sync_v2"]
        assert all(r["notes"] is None for r in edu)


class TestUnused:
    def test_connector_with_zero_processes_flagged(self):
        rows = map_dependencies(PROCESSES, CONNECTORS)
        unused = [r for r in rows if r["process"] is None]
        assert len(unused) == 1
        assert unused[0]["connector"] == "Salesforce Connector"
        assert "unused" in unused[0]["notes"]

    def test_no_processes_at_all(self):
        rows = map_dependencies([], CONNECTORS)
        assert len(rows) == 3
        assert all("unused" in r["notes"] for r in rows)


class TestFlagsDisabled:
    def test_none_envs_disable_gap_flags(self):
        """Accounts without a clean DEV/PROD pair pass None — no false flags."""
        rows = map_dependencies(PROCESSES, CONNECTORS, reference_env=None, baseline_env=None)
        dependency_rows = [r for r in rows if r["process"]]
        assert dependency_rows
        assert all(r["notes"] is None for r in dependency_rows)
