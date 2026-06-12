"""Full audit — runs every check and combines the results into one report."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from ..client import BoomiAPIError, BoomiAuthError, BoomiClient, err_console
from ..formatters import build_table, console, write_output
from . import audit_command, build_client, emit
from .connectors import collect_connectors
from .drift import collect_drift
from .duplicates import collect_duplicates
from .processes import DEFAULT_WORKERS, collect_processes


def collect_all(
    client: BoomiClient,
    max_records: int = 0,
    workers: int = DEFAULT_WORKERS,
    env_from: str | None = None,
    env_to: str | None = None,
) -> dict[str, list[dict]]:
    report = {
        "connectors": collect_connectors(client, max_records=max_records),
        "duplicates": collect_duplicates(client, max_records=max_records),
    }
    try:
        report["drift"] = collect_drift(
            client, env_from=env_from or "DEV", env_to=env_to or "PROD"
        )
    except BoomiAuthError:
        raise
    except BoomiAPIError as exc:
        if env_from or env_to:
            raise  # an explicitly requested pair must not fail silently
        # The DEV/PROD defaults don't resolve on this account — better an
        # honest gap in the report than no report at all.
        err_console.print(f"⚠️  Drift section skipped: {exc}")
        report["drift"] = []
    report["processes"] = collect_processes(
        client, max_records=max_records, workers=workers, env_from=env_from, env_to=env_to
    )
    return report


@audit_command
def full_audit(
    fmt: str = typer.Option("table", "--format", help="table | json | csv"),
    output: Path | None = typer.Option(None, "--output", help="Write csv/json to file"),
    force: bool = typer.Option(False, "--force", help="Overwrite output file without asking"),
    max_records: int = typer.Option(0, "--max-records", help="Safety cap (0 = unlimited)"),
    delay: float = typer.Option(0.1, "--delay", help="Seconds between API calls"),
    workers: int = typer.Option(
        DEFAULT_WORKERS,
        "--workers",
        help="Parallel API requests for reference lookups (lower if rate limited)",
    ),
    env_from: str | None = typer.Option(
        None, "--env-from", help="Baseline environment for drift/gap checks (default: DEV)"
    ),
    env_to: str | None = typer.Option(
        None, "--env-to", help="Target environment for drift/gap checks (default: PROD)"
    ),
) -> None:
    """Run the full audit: connectors, duplicates, drift, and process dependencies."""
    with build_client(delay=delay, fmt=fmt, output=output) as client:
        report = collect_all(
            client,
            max_records=max_records,
            workers=workers,
            env_from=env_from,
            env_to=env_to,
        )

    if fmt == "json":
        content = json.dumps(report, indent=2, default=str)
        print(content)
        if output is not None:
            write_output(content, output, force=force)
        return
    if fmt == "csv":
        # CSV is flat, so sections become a leading column.
        combined = [
            {"section": section, **row} for section, rows in report.items() for row in rows
        ]
        emit(combined, fmt, output, force, "Full audit")
        return
    for section, rows in report.items():
        console.print(build_table(rows, title=section))
