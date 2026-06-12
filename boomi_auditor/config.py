"""Configuration loading.

Priority (highest first):
1. CLI flags
2. Environment variables (BOOMI_ACCOUNT_ID, BOOMI_USERNAME, BOOMI_API_TOKEN)
3. ~/.boomi-auditor/config.json
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_BASE_URL = "https://api.boomi.com/api/rest/v1"
CONFIG_DIR = Path.home() / ".boomi-auditor"
CONFIG_PATH = CONFIG_DIR / "config.json"

ENV_ACCOUNT_ID = "BOOMI_ACCOUNT_ID"
ENV_USERNAME = "BOOMI_USERNAME"
ENV_API_TOKEN = "BOOMI_API_TOKEN"
ENV_BASE_URL = "BOOMI_BASE_URL"


class ConfigError(Exception):
    """Raised when required configuration is missing or unreadable."""


def mask_token(token: str) -> str:
    """Mask an API token so it is safe to show in output and error messages."""
    if not token:
        return ""
    if len(token) <= 8:
        return "****"
    return "****" + token[-4:]


@dataclass
class Config:
    account_id: str
    username: str
    api_token: str
    base_url: str = DEFAULT_BASE_URL

    def __repr__(self) -> str:
        # The token must never appear in logs, tracebacks, or debug output.
        return (
            f"Config(account_id={self.account_id!r}, username={self.username!r}, "
            f"api_token={mask_token(self.api_token)!r}, base_url={self.base_url!r})"
        )


def _read_config_file(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"❌ Could not read config file {path}: {exc}") from exc


def permission_warning(path: Path | None = None) -> str | None:
    """Return a warning if the config file is readable by group/others, else None."""
    path = path or CONFIG_PATH
    # POSIX permission bits are meaningless on Windows, so skip the check there.
    if os.name == "nt" or not path.exists():
        return None
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        return f"⚠️  Token visible in config file — run: chmod 600 {path}"
    return None


def load_config(
    account_id: str | None = None,
    username: str | None = None,
    api_token: str | None = None,
    *,
    config_path: Path | None = None,
) -> Config:
    """Resolve config from CLI flags, then env vars, then the config file."""
    load_dotenv()  # support .env files in local development
    file_cfg = _read_config_file(config_path or CONFIG_PATH)
    account_id = account_id or os.environ.get(ENV_ACCOUNT_ID) or file_cfg.get("account_id")
    username = username or os.environ.get(ENV_USERNAME) or file_cfg.get("username")
    api_token = api_token or os.environ.get(ENV_API_TOKEN) or file_cfg.get("api_token")
    base_url = os.environ.get(ENV_BASE_URL) or file_cfg.get("base_url") or DEFAULT_BASE_URL

    missing = [
        label
        for label, value in (
            ("account ID", account_id),
            ("username", username),
            ("API token", api_token),
        )
        if not value
    ]
    if missing:
        raise ConfigError(
            "❌ Missing configuration: "
            + ", ".join(missing)
            + f". Set {ENV_ACCOUNT_ID}/{ENV_USERNAME}/{ENV_API_TOKEN} or run: boomi-audit init"
        )
    return Config(account_id, username, api_token, base_url)


def init_config(
    account_id: str,
    username: str,
    api_token: str,
    *,
    base_url: str = DEFAULT_BASE_URL,
    config_path: Path = CONFIG_PATH,
) -> Path:
    """Write the config file with owner-only permissions from creation.

    The token is created directly at 0o600 (and the directory at 0o700) rather
    than written world-readable and chmod-ed afterward — that ordering left a
    brief window where the token sat on disk under the default umask.
    """
    config_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {
            "account_id": account_id,
            "username": username,
            "api_token": api_token,
            "base_url": base_url,
        },
        indent=2,
    ) + "\n"
    if os.name == "nt":
        config_path.write_text(payload)  # POSIX mode bits are meaningless here
        return config_path
    if config_path.parent.exists():
        os.chmod(config_path.parent, 0o700)
    # O_CREAT with mode 0o600 means the file never exists at wider permissions.
    fd = os.open(config_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(payload)
    os.chmod(config_path, 0o600)  # enforce 0o600 even if the file pre-existed
    return config_path
