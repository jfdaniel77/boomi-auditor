"""Use case 2 — duplication detection (TYPE A/B/C, version-aware)."""

from __future__ import annotations

from pathlib import Path

import typer

from ..analyzers.drift_analyzer import ENCRYPTED_PLACEHOLDER, is_encrypted_field
from ..analyzers.duplicate_analyzer import find_duplicates
from ..client import BoomiClient
from . import (
    ENDPOINT_FIELD_PATTERN,
    audit_command,
    build_client,
    emit,
    fetch_connector_components,
    fetch_environments,
    fetch_extensions,
    merge_deployments,
    resolve_env,
)


def _connector_records(
    rows: list[dict],
    extensions: dict[str, dict[str, dict[str, dict]]],
    env: str | None,
) -> list[dict]:
    """Expand one merged component row into one record per deployed environment."""
    records: list[dict] = []
    for row in rows:
        for env_name in row["environments"] or [None]:
            if env and env_name != env:
                continue
            fields = (extensions.get(env_name) or {}).get(row["name"]) or {}
            endpoint = None
            config: dict = {}
            for field_id, meta in fields.items():
                if is_encrypted_field(field_id, meta):
                    config[field_id] = ENCRYPTED_PLACEHOLDER
                    continue
                value = meta.get("value")
                config[field_id] = value
                if endpoint is None and value and ENDPOINT_FIELD_PATTERN.search(field_id):
                    endpoint = value
            records.append(
                {
                    "componentId": row["componentId"],
                    "name": row["name"],
                    "version": row["version"],
                    "environment": env_name,
                    "endpoint": endpoint,
                    "config": config,
                    "deleted": row["status"] == "deleted",
                }
            )
    return records


def collect_duplicates(
    client: BoomiClient, env: str | None = None, max_records: int = 0
) -> list[dict]:
    if env:
        env = resolve_env(fetch_environments(client), env)["name"]  # fail fast on a bad name
    components = fetch_connector_components(
        client, connection_only=True, max_records=max_records
    )
    rows, environments = merge_deployments(client, components)
    extensions = fetch_extensions(client, environments)
    records = _connector_records(rows, extensions, env)
    return [finding.to_row() for finding in find_duplicates(records)]


@audit_command
def duplicates(
    env: str | None = typer.Option(None, "--env", help="Limit analysis to one environment"),
    fmt: str = typer.Option("table", "--format", help="table | json | csv"),
    output: Path | None = typer.Option(None, "--output", help="Write csv/json to file"),
    force: bool = typer.Option(False, "--force", help="Overwrite output file without asking"),
    max_records: int = typer.Option(0, "--max-records", help="Safety cap (0 = unlimited)"),
    delay: float = typer.Option(0.1, "--delay", help="Seconds between API calls"),
) -> None:
    """Detect duplicate connectors: same name+version, shared endpoints, identical config."""
    with build_client(delay=delay, fmt=fmt, output=output) as client:
        rows = collect_duplicates(client, env=env, max_records=max_records)
    emit(rows, fmt, output, force, "Duplicate connectors")
