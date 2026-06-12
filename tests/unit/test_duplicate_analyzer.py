"""Duplicate analyzer tests — TYPE A/B/C detection with version awareness."""

from __future__ import annotations

import pytest

from boomi_auditor.analyzers.duplicate_analyzer import find_duplicates


@pytest.fixture
def scenario(fixture_loader):
    return fixture_loader("duplicates_scenario.json")


def by_type(findings, dup_type):
    return [f for f in findings if f.dup_type == dup_type]


class TestTypeA:
    def test_same_name_same_version_same_env_flagged(self, scenario):
        findings = by_type(find_duplicates(scenario), "A")
        assert len(findings) == 1
        finding = findings[0]
        assert finding.severity == "warning"
        assert 'Duplicate name in DEV: "SAP HTTP Client" v3 (2 instances)' in finding.message
        assert {c["componentId"] for c in finding.components} == {"c1", "c2"}

    def test_different_versions_not_flagged(self, scenario):
        # c3 is "SAP HTTP Client" v4 in DEV — a new version is expected, not a duplicate
        findings = by_type(find_duplicates(scenario), "A")
        assert all("v4" not in f.message for f in findings)

    def test_deleted_components_ignored(self, scenario):
        # c8/c9 are duplicate-looking but deleted=true
        findings = find_duplicates(scenario)
        assert all("Old FTP Connector" not in f.message for f in findings)


class TestTypeB:
    def test_same_endpoint_different_names_flagged(self, scenario):
        findings = by_type(find_duplicates(scenario), "B")
        assert len(findings) == 1
        assert "Same endpoint in PROD: https://api.example.com → 2 connections" in (
            findings[0].message
        )

    def test_same_endpoint_same_name_not_flagged(self, scenario):
        # c1/c2/c3 share an endpoint in DEV but are all "SAP HTTP Client"
        findings = by_type(find_duplicates(scenario), "B")
        assert all("DEV" not in f.message for f in findings)


class TestTypeC:
    def test_identical_config_cross_env_flagged_as_info(self, scenario):
        findings = by_type(find_duplicates(scenario), "C")
        assert len(findings) == 1
        finding = findings[0]
        assert finding.severity == "info"
        assert 'Identical config across DEV/PROD: "Oracle DB Connector"' in finding.message

    def test_differing_config_not_flagged(self, scenario):
        findings = by_type(find_duplicates(scenario), "C")
        assert all("SAP HTTP Client" not in f.message for f in findings)


class TestNoDuplicates:
    def test_clean_input_returns_empty(self):
        clean = [
            {"componentId": "x1", "name": "A", "version": 1, "environment": "DEV",
             "endpoint": "https://a.example.com", "config": {"url": "a"}, "deleted": False},
            {"componentId": "x2", "name": "B", "version": 1, "environment": "DEV",
             "endpoint": "https://b.example.com", "config": {"url": "b"}, "deleted": False},
        ]
        assert find_duplicates(clean) == []

    def test_empty_input_returns_empty(self):
        assert find_duplicates([]) == []
