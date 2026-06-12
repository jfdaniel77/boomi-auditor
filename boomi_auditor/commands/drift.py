"""Use case 3 — extension drift detection between environments."""

from __future__ import annotations

from pathlib import Path

import typer

from ..analyzers.drift_analyzer import analyze_drift
from ..client import BoomiClient, err_console
from . import (
    audit_command,
    build_client,
    emit,
    fetch_environments,
    fetch_extensions,
    resolve_env,
)


def collect_drift(
    client: BoomiClient,
    connector: str | None = None,
    env_from: str = "DEV",
    env_to: str = "PROD",
) -> list[dict]:
    environments = fetch_environments(client)
    env_from = resolve_env(environments, env_from)["name"]
    env_to = resolve_env(environments, env_to)["name"]
    pair = [e for e in environments if e.get("name") in (env_from, env_to)]
    extensions = fetch_extensions(client, pair)

    if connector:
        for env_name in (env_from, env_to):
            names = extensions.get(env_name, {})
            if not any(connector.lower() in str(name).lower() for name in names):
                err_console.print(
                    f"⚠️  No extensions found for connector {connector} "
                    f"in environment {env_name}"
                )

    findings = analyze_drift(extensions, env_from=env_from, env_to=env_to, connector=connector)
    return [finding.to_row() for finding in findings]


@audit_command
def drift(
    connector: str | None = typer.Option(None, "--connector", help="Filter by connector name"),
    env_from: str = typer.Option("DEV", "--env-from", help="Baseline environment"),
    env_to: str = typer.Option("PROD", "--env-to", help="Environment to audit"),
    fmt: str = typer.Option("table", "--format", help="table | json | csv"),
    output: Path | None = typer.Option(None, "--output", help="Write csv/json to file"),
    force: bool = typer.Option(False, "--force", help="Overwrite output file without asking"),
    delay: float = typer.Option(0.1, "--delay", help="Seconds between API calls"),
) -> None:
    """Detect extension drift (forgotten overrides, missing fields, schema differences)."""
    with build_client(delay=delay, fmt=fmt, output=output) as client:
        rows = collect_drift(client, connector=connector, env_from=env_from, env_to=env_to)
    emit(rows, fmt, output, force, f"Extension drift {env_from} → {env_to}")
