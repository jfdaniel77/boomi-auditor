"""Drift analyzer tests — forgotten overrides, missing fields, encrypted masking."""

from __future__ import annotations

from boomi_auditor.analyzers.drift_analyzer import (
    ENCRYPTED_PLACEHOLDER,
    analyze_drift,
    is_encrypted_field,
)


def extensions(dev: dict, prod: dict) -> dict:
    return {"DEV": dev, "PROD": prod}


class TestDrift:
    def test_prod_equals_dev_flagged(self):
        ext = extensions(
            {"SAP HTTP Client": {"url": {"value": "https://dev-sap.example.com"}}},
            {"SAP HTTP Client": {"url": {"value": "https://dev-sap.example.com"}}},
        )
        findings = analyze_drift(ext)
        assert len(findings) == 1
        assert findings[0].kind == "drift"
        assert "DRIFT" in findings[0].message
        assert findings[0].severity == "error"

    def test_different_values_not_flagged(self):
        ext = extensions(
            {"SAP HTTP Client": {"url": {"value": "https://dev-sap.example.com"}}},
            {"SAP HTTP Client": {"url": {"value": "https://prod-sap.example.com"}}},
        )
        assert analyze_drift(ext) == []

    def test_secret_like_values_are_redacted_from_reported_values(self):
        raw = "super-secret-token-123"
        ext = extensions(
            {"Conn": {"custom_field": {"value": raw}}},
            {"Conn": {"custom_field": {"value": raw}}},
        )
        finding = analyze_drift(ext)[0]
        assert finding.value_from == "[REDACTED]"
        assert finding.value_to == "[REDACTED]"
        assert raw not in str(finding.to_row())

    def test_url_credentials_scrubbed_from_reported_values(self):
        """A drifted URL carrying embedded credentials must not leak them in
        the finding's value_from/value_to."""
        ext = extensions(
            {"SAP": {"url": {"value": "https://svc:hunter2@sap.example.com"}}},
            {"SAP": {"url": {"value": "https://svc:hunter2@sap.example.com"}}},
        )
        finding = analyze_drift(ext)[0]
        assert "hunter2" not in finding.value_from
        assert "hunter2" not in finding.value_to
        assert "[REDACTED]" in finding.value_to


class TestMissing:
    def test_field_in_prod_empty_in_dev_flagged(self):
        ext = extensions(
            {"Oracle DB": {"db_host": {"value": ""}}},
            {"Oracle DB": {"db_host": {"value": "ora-prod-01"}}},
        )
        findings = analyze_drift(ext)
        assert len(findings) == 1
        assert findings[0].kind == "missing"
        assert "MISSING" in findings[0].message

    def test_field_absent_in_dev_flagged(self):
        ext = extensions({}, {"Oracle DB": {"db_host": {"value": "ora-prod-01"}}})
        findings = analyze_drift(ext)
        assert len(findings) == 1
        assert findings[0].kind == "missing"

    def test_empty_in_both_not_flagged(self):
        ext = extensions(
            {"Oracle DB": {"db_host": {"value": ""}}},
            {"Oracle DB": {"db_host": {"value": ""}}},
        )
        assert analyze_drift(ext) == []


class TestSchema:
    def test_type_mismatch_flagged(self):
        ext = extensions(
            {"Oracle DB": {"db_port": {"value": "1521", "type": "string"}}},
            {"Oracle DB": {"db_port": {"value": "1522", "type": "integer"}}},
        )
        findings = analyze_drift(ext)
        assert len(findings) == 1
        assert findings[0].kind == "schema"


class TestEncrypted:
    def test_metadata_flag_detected(self):
        assert is_encrypted_field("some_field", {"encryptedValueSet": True})

    def test_name_patterns_detected(self):
        for name in ("password", "api_token", "client_secret", "private_key"):
            assert is_encrypted_field(name, {})
        assert not is_encrypted_field("url", {})

    def test_encrypted_values_never_shown(self):
        raw = "raw-secret-value-DO-NOT-SHOW"
        ext = extensions(
            {"Salesforce": {"client_secret": {"value": raw, "encryptedValueSet": True}}},
            {"Salesforce": {"client_secret": {"value": raw, "encryptedValueSet": True}}},
        )
        findings = analyze_drift(ext)
        assert len(findings) == 1
        finding = findings[0]
        assert finding.kind == "encrypted"
        assert finding.value_from == ENCRYPTED_PLACEHOLDER
        assert finding.value_to == ENCRYPTED_PLACEHOLDER
        # The raw value must not appear anywhere in the finding
        assert raw not in str(finding.to_row())
        assert raw not in finding.message

    def test_encrypted_by_metadata_without_name_pattern(self):
        raw = "raw-secret-value"
        ext = extensions({}, {"Conn": {"credential": {"value": raw, "encryptedValueSet": True}}})
        findings = analyze_drift(ext)
        assert findings[0].kind == "encrypted"
        assert raw not in str(findings[0].to_row())


class TestCleanAndFilters:
    def test_clean_extensions_return_no_findings(self):
        ext = extensions(
            {"Conn": {"url": {"value": "https://dev.example.com"}}},
            {"Conn": {"url": {"value": "https://prod.example.com"}}},
        )
        assert analyze_drift(ext) == []

    def test_connector_filter_is_case_insensitive_substring(self):
        ext = extensions(
            {
                "SAP HTTP Client": {"url": {"value": "same"}},
                "Oracle DB": {"db_url": {"value": "same"}},
            },
            {
                "SAP HTTP Client": {"url": {"value": "same"}},
                "Oracle DB": {"db_url": {"value": "same"}},
            },
        )
        findings = analyze_drift(ext, connector="http client")
        assert len(findings) == 1
        assert findings[0].connector == "SAP HTTP Client"

    def test_custom_env_pair(self):
        ext = {
            "SIT": {"Conn": {"url": {"value": "same"}}},
            "QAT": {"Conn": {"url": {"value": "same"}}},
        }
        findings = analyze_drift(ext, env_from="SIT", env_to="QAT")
        assert len(findings) == 1
        assert "QAT url = SIT url" in findings[0].message
