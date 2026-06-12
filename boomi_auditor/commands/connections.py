"""List connection components (connector-settings) and where they are deployed."""

from __future__ import annotations

from pathlib import Path

import typer

from ..client import BoomiClient
from . import (
    audit_command,
    build_client,
    connection_settings,
    emit,
    fetch_connector_components,
    fetch_environments,
    merge_deployments,
    resolve_env,
)


def collect_connections(
    client: BoomiClient,
    env: str | None = None,
    include_deleted: bool = False,
    deployed_only: bool = False,
    max_records: int = 0,
) -> list[dict]:
    if env:
        env = resolve_env(fetch_environments(client), env)["name"]  # fail fast on a bad name
    components = fetch_connector_components(
        client,
        include_deleted=include_deleted,
        connection_only=True,
        max_records=max_records,
    )
    base_rows, environments = merge_deployments(client, components)
    settings = connection_settings(client, environments)

    # One row per connection per environment: the useful columns (endpoint,
    # user, credentials) come from extensions, which are environment-specific
    # — a single row with an environment *list* could not carry them. No type
    # column either: every row here is a connection by definition.
    rows: list[dict] = []
    for base in base_rows:
        env_names = base["environments"]
        if deployed_only and not env_names:
            # License-review view. Not the default: an empty environment can
            # also mean "deployed but not extension-managed", and undeployed
            # rows are the cleanup candidates that free licenses.
            continue
        if env:
            if env not in env_names:
                continue
            env_names = [env]
        for env_name in env_names or [""]:  # keep undeployed connections visible
            values = settings.get(env_name, {}).get(base["componentId"], {})
            rows.append(
                {
                    "componentId": base["componentId"],
                    "name": base["name"],
                    "version": base["version"],
                    "status": base["status"],
                    "environment": env_name,
                    "url": values.get("url", ""),
                    "user": values.get("user", ""),
                    "credentials": values.get("credentials", ""),
                }
            )
    return rows


@audit_command
def connections(
    env: str | None = typer.Option(None, "--env", help="Filter by environment name"),
    include_deleted: bool = typer.Option(
        False, "--include-deleted", help="Include deleted/archived components"
    ),
    deployed_only: bool = typer.Option(
        False,
        "--deployed-only",
        help="Only rows placed in at least one environment (license review)",
    ),
    fmt: str = typer.Option("table", "--format", help="table | json | csv"),
    output: Path | None = typer.Option(None, "--output", help="Write csv/json to file"),
    force: bool = typer.Option(False, "--force", help="Overwrite output file without asking"),
    max_records: int = typer.Option(0, "--max-records", help="Safety cap (0 = unlimited)"),
    delay: float = typer.Option(0.1, "--delay", help="Seconds between API calls"),
) -> None:
    """List connection accounts (connector-settings components)."""
    with build_client(delay=delay, fmt=fmt, output=output) as client:
        rows = collect_connections(
            client,
            env=env,
            include_deleted=include_deleted,
            deployed_only=deployed_only,
            max_records=max_records,
        )
    emit(rows, fmt, output, force, "Connections")
