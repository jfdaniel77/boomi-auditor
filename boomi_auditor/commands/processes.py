"""Use case 4 — process dependency map: which processes use which connectors."""

from __future__ import annotations

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import typer
from rich.progress import BarColumn, Progress, TextColumn

from ..analyzers.dependency_analyzer import map_dependencies
from ..client import BoomiAPIError, BoomiAuthError, BoomiClient, err_console
from . import (
    ACTIVE_DEPLOYMENT_FILTER,
    CONNECTION_TYPE,
    OPERATION_TYPE,
    audit_command,
    build_client,
    emit,
    fetch_connector_components,
    fetch_environments,
    process_metadata_filter,
    resolve_env,
)

# Per-process ComponentReference round-trips are independent and latency-bound,
# so a small worker pool gives a near-linear speedup. Kept conservative by
# default to stay well under Boomi's rate limits (429s are retried anyway).
DEFAULT_WORKERS = 4


def _flatten_references(records: list[dict]):
    """ComponentReference results sometimes nest entries under "references"."""
    for record in records:
        nested = record.get("references")
        if isinstance(nested, list):
            yield from nested
        else:
            yield record


def _fetch_references(client: BoomiClient, process_id: str, version: object) -> list[dict]:
    # ComponentReference cannot be queried unfiltered, and (verified live)
    # parentComponentId must be accompanied by parentVersion — which is why
    # processes are listed via ComponentMetadata in the first place.
    query_filter = {
        "expression": {
            "operator": "and",
            "nestedExpression": [
                {
                    "argument": [process_id],
                    "operator": "EQUALS",
                    "property": "parentComponentId",
                },
                {
                    "argument": [str(version)],
                    "operator": "EQUALS",
                    "property": "parentVersion",
                },
            ],
        }
    }
    return client.paginate("ComponentReference", query_filter, show_progress=False)


def _references_by_parent(
    client: BoomiClient,
    parents: list[tuple[str, object]],
    workers: int,
    description: str,
) -> tuple[dict[str, list[dict]], list[str]]:
    """Fetch ComponentReference for each (component_id, version) concurrently.

    httpx.Client is thread-safe. A parent whose query keeps failing after the
    client's retries is reported in the second return value instead of
    aborting — on a 5k-process account one throttling blip must not throw
    away a quarter-hour of completed work. Auth errors still abort: they are
    systemic, not transient.
    """
    results: dict[str, list[dict]] = {}
    failed: list[str] = []
    last_error: BoomiAPIError | None = None
    progress: Progress | None = None
    task_id = None
    if getattr(client, "show_progress", False) and len(parents) > 1:
        progress = Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            console=err_console,
            transient=True,
        )
        progress.start()
        task_id = progress.add_task(description, total=len(parents))
    try:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = {
                pool.submit(_fetch_references, client, pid, version): pid
                for pid, version in parents
            }
            for future in as_completed(futures):
                parent_id = futures[future]
                try:
                    results[parent_id] = future.result()
                except BoomiAuthError:
                    raise
                except BoomiAPIError as exc:
                    failed.append(parent_id)
                    last_error = exc
                if progress is not None and task_id is not None:
                    progress.advance(task_id)
    finally:
        if progress is not None:
            progress.stop()
    if failed and not results:
        # Nothing succeeded — this is systemic, not a transient blip.
        raise last_error  # type: ignore[misc]
    return results, failed


def _gap_envs(
    environments: list[dict], env_from: str | None, env_to: str | None
) -> tuple[str, str] | None:
    """Resolve the env pair used for "in X but missing in Y" flags.

    Explicit values resolve strictly (typos/ambiguity error out). The
    DEV/PROD defaults are best-effort: accounts with names like "04. PRD"
    have no unambiguous match, and that should disable the flags — not
    break the command.
    """
    if env_from or env_to:
        return (
            resolve_env(environments, env_from or "DEV")["name"],
            resolve_env(environments, env_to or "PROD")["name"],
        )
    try:
        return (
            resolve_env(environments, "DEV")["name"],
            resolve_env(environments, "PROD")["name"],
        )
    except BoomiAPIError:
        err_console.print(
            "ℹ️  No unambiguous DEV/PROD pair in this account — environment-gap "
            "flags disabled (set --env-from/--env-to to enable them)."
        )
        return None


def collect_processes(
    client: BoomiClient,
    connector: str | None = None,
    env: str | None = None,
    max_records: int = 0,
    workers: int = DEFAULT_WORKERS,
    env_from: str | None = None,
    env_to: str | None = None,
) -> list[dict]:
    environments = fetch_environments(client)
    env_names = {e.get("id"): e.get("name") for e in environments}
    if env:
        env = resolve_env(environments, env)["name"]  # fail fast before the heavy fetches
    gap_envs = _gap_envs(environments, env_from, env_to)  # also fails fast
    processes = client.paginate(
        "ComponentMetadata",
        process_metadata_filter(),
        max_records=max_records,
        description="Fetching processes...",
    )
    connectors = fetch_connector_components(client)
    deployments = client.paginate("DeployedPackage", ACTIVE_DEPLOYMENT_FILTER)

    process_envs: dict[str, set] = defaultdict(set)
    for deployment in deployments:
        env_name = env_names.get(deployment.get("environmentId"))
        if env_name:
            process_envs[deployment.get("componentId")].add(env_name)

    connector_by_id = {c.get("componentId"): c for c in connectors}
    id_version_pairs = [
        (p.get("componentId") or p.get("id"), p.get("version")) for p in processes
    ]
    # Hop 1: process → directly referenced connector components (mostly
    # operations — processes rarely reference connections directly).
    refs_by_parent: dict[str, set] = defaultdict(set)
    process_refs, failed_processes = _references_by_parent(
        client, id_version_pairs, workers, "Mapping process references..."
    )
    for process_id, results in process_refs.items():
        for ref in _flatten_references(results):
            if ref.get("componentId") in connector_by_id:
                refs_by_parent[process_id].add(ref["componentId"])

    # Hop 2: operation → connection. Without this, connections used through
    # operations (the normal Boomi pattern) all look "unused".
    operation_ids = {
        component_id
        for ids in refs_by_parent.values()
        for component_id in ids
        if str(connector_by_id[component_id].get("type")) == OPERATION_TYPE
    }
    operation_pairs = [
        (op_id, connector_by_id[op_id].get("version")) for op_id in sorted(operation_ids)
    ]
    operation_connections: dict[str, set] = defaultdict(set)
    operation_refs, failed_operations = _references_by_parent(
        client, operation_pairs, workers, "Resolving operation connections..."
    )
    for op_id, results in operation_refs.items():
        for ref in _flatten_references(results):
            if ref.get("componentId") in connector_by_id:
                operation_connections[op_id].add(ref["componentId"])

    failures = len(failed_processes) + len(failed_operations)
    if failures:
        err_console.print(
            f"⚠️  References unavailable for {failures} component(s) after retries — "
            "the dependency map may be incomplete."
        )

    process_records = []
    for process in processes:
        process_id = process.get("componentId") or process.get("id")
        direct = refs_by_parent.get(process_id, set())
        expanded = set(direct)
        for component_id in direct:
            expanded |= operation_connections.get(component_id, set())
        process_records.append(
            {
                "name": process.get("name"),
                "environments": sorted(process_envs.get(process_id, set())),
                "connectors": sorted(
                    {connector_by_id[c].get("name") for c in expanded}
                ),
            }
        )

    if max_records or failures:
        # A capped or partially-fetched process list cannot prove a connector
        # is unused.
        err_console.print(
            "⚠️  Process data incomplete — skipping unused-connector detection."
        )
        unused_seed: list[dict] = []
    else:
        # Unused detection covers connections only: operations are build-time
        # artifacts, and listing thousands of them drowns the real signal
        # (connection accounts nobody references).
        unused_seed = [
            {"name": name}
            for name in sorted(
                {
                    c.get("name")
                    for c in connectors
                    if str(c.get("type")) == CONNECTION_TYPE
                }
            )
        ]

    reference_env, baseline_env = (
        (gap_envs[1], gap_envs[0]) if gap_envs else (None, None)
    )
    rows = map_dependencies(
        process_records, unused_seed, reference_env=reference_env, baseline_env=baseline_env
    )
    if connector:
        rows = [r for r in rows if connector.lower() in str(r["connector"]).lower()]
    if env:
        rows = [r for r in rows if env in r["environments"]]
    return rows


@audit_command
def processes(
    connector: str | None = typer.Option(None, "--connector", help="Filter by connector name"),
    env: str | None = typer.Option(None, "--env", help="Filter by environment name"),
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
        None, "--env-from", help="Baseline environment for gap flags (default: DEV)"
    ),
    env_to: str | None = typer.Option(
        None, "--env-to", help="Reference environment for gap flags (default: PROD)"
    ),
) -> None:
    """Show which processes use each connector ("if I change this, what breaks?")."""
    with build_client(delay=delay, fmt=fmt, output=output) as client:
        rows = collect_processes(
            client,
            connector=connector,
            env=env,
            max_records=max_records,
            workers=workers,
            env_from=env_from,
            env_to=env_to,
        )
    emit(rows, fmt, output, force, "Connector → process dependencies")
