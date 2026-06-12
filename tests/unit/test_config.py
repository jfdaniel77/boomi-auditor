"""Config tests: priority order, missing values, permissions, token masking."""

from __future__ import annotations

import json
import os

import pytest

from boomi_auditor.config import (
    Config,
    ConfigError,
    init_config,
    load_config,
    mask_token,
    permission_warning,
)


@pytest.fixture
def config_file(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "account_id": "file-account",
                "username": "file-user",
                "api_token": "file-token-123456",
            }
        )
    )
    os.chmod(path, 0o600)
    return path


class TestPriority:
    def test_cli_flags_beat_env_vars(self, config_file):
        cfg = load_config("cli-account", "cli-user", "cli-token", config_path=config_file)
        assert cfg.account_id == "cli-account"
        assert cfg.username == "cli-user"
        assert cfg.api_token == "cli-token"

    def test_env_vars_beat_config_file(self, config_file):
        # test_credentials autouse fixture sets the BOOMI_* env vars
        cfg = load_config(config_path=config_file)
        assert cfg.account_id == "test-account"

    def test_config_file_used_when_nothing_else_set(self, config_file, monkeypatch):
        for var in ("BOOMI_ACCOUNT_ID", "BOOMI_USERNAME", "BOOMI_API_TOKEN"):
            monkeypatch.delenv(var)
        cfg = load_config(config_path=config_file)
        assert cfg.account_id == "file-account"
        assert cfg.api_token == "file-token-123456"


class TestMissingConfig:
    def test_missing_everything_raises_clear_error(self, tmp_path, monkeypatch):
        for var in ("BOOMI_ACCOUNT_ID", "BOOMI_USERNAME", "BOOMI_API_TOKEN"):
            monkeypatch.delenv(var)
        with pytest.raises(ConfigError, match="Missing configuration.*boomi-audit init"):
            load_config(config_path=tmp_path / "missing.json")

    def test_corrupt_config_file_raises(self, tmp_path, monkeypatch):
        for var in ("BOOMI_ACCOUNT_ID", "BOOMI_USERNAME", "BOOMI_API_TOKEN"):
            monkeypatch.delenv(var)
        bad = tmp_path / "config.json"
        bad.write_text("{not json")
        with pytest.raises(ConfigError, match="Could not read config file"):
            load_config(config_path=bad)


class TestPermissions:
    def test_world_readable_config_warns(self, config_file):
        os.chmod(config_file, 0o644)
        warning = permission_warning(config_file)
        assert warning is not None and "chmod 600" in warning

    def test_owner_only_config_is_silent(self, config_file):
        assert permission_warning(config_file) is None

    def test_missing_file_is_silent(self, tmp_path):
        assert permission_warning(tmp_path / "nope.json") is None

    def test_init_config_locks_permissions(self, tmp_path):
        path = init_config("acct", "user", "tok-secret", config_path=tmp_path / "config.json")
        assert path.exists()
        assert (path.stat().st_mode & 0o777) == 0o600
        assert json.loads(path.read_text())["account_id"] == "acct"

    @pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
    def test_init_config_locks_new_directory(self, tmp_path):
        """The token directory is created owner-only, not at the default umask."""
        path = init_config(
            "acct", "user", "tok-secret", config_path=tmp_path / "fresh" / "config.json"
        )
        assert (path.parent.stat().st_mode & 0o777) == 0o700

    @pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
    def test_init_config_tightens_preexisting_loose_file(self, tmp_path):
        """Overwriting a world-readable config re-locks it to 0o600."""
        target = tmp_path / "config.json"
        target.write_text("{}")
        os.chmod(target, 0o644)
        init_config("acct", "user", "tok-secret", config_path=target)
        assert (target.stat().st_mode & 0o777) == 0o600


class TestTokenSafety:
    def test_mask_token(self):
        assert mask_token("") == ""
        assert mask_token("short") == "****"
        assert mask_token("abcdefghijkl-9999") == "****9999"

    def test_repr_never_leaks_token(self):
        cfg = Config("acct", "user", "super-secret-token-value")
        assert "super-secret-token-value" not in repr(cfg)
