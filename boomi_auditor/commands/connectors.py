"""Use case 1 — list currently deployed connectors (deleted excluded by default)."""

from __future__ import annotations

from pathlib import Path

import typer

from ..client import BoomiClient
from . import (
    audit_command,
    build_client,
    emit,
    fetch_connector_components,
    fetch_environments,
    merge_deployments,
    resolve_env,
    warn_offline_atoms,
)


def collect_connectors(
    client: BoomiClient,
    env: str | None = None,
    type_filter: str | None = None,
    include_deleted: bool = False,
    deployed_only: bool = False,
    max_records: int = 0,
) -> list[dict]:
    if env:
        # Resolve the name up front (errors list alternatives) — a typo must
        # not cost the user the full component/deployment fetch first.
        env = resolve_env(fetch_environments(client), env)["name"]
    components = fetch_connector_components(
        client, include_deleted=include_deleted, max_records=max_records
    )
    rows, _environments = merge_deployments(client, components)
    if deployed_only:
        # License-review view. Not the default: an empty environment list can
        # also mean "deployed but not extension-managed", and undeployed rows
        # are the cleanup candidates that free licenses.
        rows = [r for r in rows if r["environments"]]
    if env:
        # The question asked is "what is in <env>?" — scope the environments
        # column to it; omit --env for the cross-environment view.
        rows = [
            {**r, "environments": [env]} for r in rows if env in r["environments"]
        ]
    if type_filter:
        needle = type_filter.lower()
        rows = [
            r
            for r in rows
            if needle in str(r["name"]).lower() or needle in str(r["type"]).lower()
        ]
    warn_offline_atoms(client)
    return rows


@audit_command
def connectors(
    env: str | None = typer.Option(None, "--env", help="Filter by environment name"),
    type_filter: str | None = typer.Option(None, "--type", help="Filter by connector type/name"),
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
    """List all active connectors, excluding deleted/archived by default."""
    with build_client(delay=delay, fmt=fmt, output=output) as client:
        rows = collect_connectors(
            client,
            env=env,
            type_filter=type_filter,
            include_deleted=include_deleted,
            deployed_only=deployed_only,
            max_records=max_records,
        )
    emit(rows, fmt, output, force, "Deployed connectors")
