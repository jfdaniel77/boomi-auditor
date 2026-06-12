"""Redaction tests — credentials embedded inside otherwise-shown URL values."""

from __future__ import annotations

import pytest

from boomi_auditor.redaction import REDACTED, scrub_url_credentials


class TestUserinfo:
    def test_user_and_password_removed(self):
        out = scrub_url_credentials("https://alice:s3cret@api.example.com/v1")
        assert "s3cret" not in out
        assert "alice" not in out
        assert out == f"https://{REDACTED}@api.example.com/v1"

    def test_user_only_removed(self):
        out = scrub_url_credentials("ftp://serviceacct@files.example.com")
        assert out == f"ftp://{REDACTED}@files.example.com"

    def test_jdbc_at_sign_without_userinfo_untouched(self):
        # jdbc:oracle:thin:@host has an @ but no //userinfo — must not change.
        url = "jdbc:oracle:thin:@ora-shared-01:1521/XE"
        assert scrub_url_credentials(url) == url

    def test_plain_url_untouched(self):
        url = "https://api.example.com/v1/path?limit=50"
        assert scrub_url_credentials(url) == url


class TestQueryParams:
    @pytest.mark.parametrize("key", ["password", "pwd", "secret", "token", "api_key", "apikey"])
    def test_credential_params_redacted(self, key):
        out = scrub_url_credentials(f"https://host/x?{key}=topsecret&page=2")
        assert "topsecret" not in out
        assert f"{key}={REDACTED}" in out
        assert "page=2" in out  # benign params survive

    def test_benign_key_suffix_untouched(self):
        # "sortkey" must not trip the "key" alternative — anchored on param start.
        url = "https://host/x?sortkey=name&monkey=2"
        assert scrub_url_credentials(url) == url


class TestEdgeCases:
    def test_none_returns_empty_string(self):
        assert scrub_url_credentials(None) == ""

    def test_empty_returns_empty(self):
        assert scrub_url_credentials("") == ""

    def test_non_url_value_untouched(self):
        assert scrub_url_credentials("just a hostname or label") == "just a hostname or label"
