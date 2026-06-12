"""Shared helpers for audit commands.

Commands fetch and normalize API data here, then hand plain data structures
to the pure analyzers in boomi_auditor.analyzers.
"""

from __future__ import annotations

import functools
import re
from collections import defaultdict
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path
from typing import Any

import typer

from .. import formatters
from ..analyzers.drift_analyzer import ENCRYPTED_PLACEHOLDER, is_encrypted_field
from ..client import BoomiAPIError, BoomiClient, err_console
from ..config import ConfigError, load_config, permission_warning
from ..redaction import scrub_url_credentials

CONNECTION_TYPE = "connector-settings"
OPERATION_TYPE = "connector-action"
# Report rows use the Build-UI labels, not Boomi's API type jargon.
FRIENDLY_TYPES = {CONNECTION_TYPE: "Connection", OPERATION_TYPE: "Operation"}
# Heuristics for spotting the endpoint/user extension fields of a connection.
ENDPOINT_FIELD_PATTERN = re.compile(r"url|host|endpoint", re.IGNORECASE)
USER_FIELD_PATTERN = re.compile(r"user", re.IGNORECASE)

# DeployedPackage holds every deployment ever made; only active ones describe
# the current state, and on a mature account history dwarfs them.
ACTIVE_DEPLOYMENT_FILTER = {
    "expression": {"argument": ["true"], "operator": "EQUALS", "property": "active"}
}


def component_metadata_filter(
    *, connection_only: bool = False, include_deleted: bool = False
) -> dict:
    """Server-side QueryFilter for connector component listings.

    Component listings come from the ComponentMetadata object (the Component
    object itself is not queryable). Filtering on type/deleted/currentVersion
    happens in the query so we never download the account's processes, maps,
    and profiles just to throw them away — on a mature account that is the
    bulk of the components.
    """
    types = (CONNECTION_TYPE,) if connection_only else (CONNECTION_TYPE, OPERATION_TYPE)
    type_expressions = [
        {"argument": [t], "operator": "EQUALS", "property": "type"} for t in types
    ]
    expressions = [
        {"argument": ["true"], "operator": "EQUALS", "property": "currentVersion"},
        type_expressions[0]
        if len(type_expressions) == 1
        else {"operator": "or", "nestedExpression": type_expressions},
    ]
    if not include_deleted:
        expressions.append(
            {"argument": ["false"], "operator": "EQUALS", "property": "deleted"}
        )
    return {"expression": {"operator": "and", "nestedExpression": expressions}}


def process_metadata_filter() -> dict:
    """Server-side QueryFilter for the account's current process components.

    Processes are listed via ComponentMetadata rather than Process/query
    because ComponentReference queries require the parent's *version* —
    which only ComponentMetadata carries.
    """
    return {
        "expression": {
            "operator": "and",
            "nestedExpression": [
                {"argument": ["true"], "operator": "EQUALS", "property": "currentVersion"},
                {"argument": ["process"], "operator": "EQUALS", "property": "type"},
                {"argument": ["false"], "operator": "EQUALS", "property": "deleted"},
            ],
        }
    }


def audit_command(fn: Callable) -> Callable:
    """Convert internal errors into human-readable messages + exit code 1."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except (ConfigError, BoomiAPIError) as exc:
            err_console.print(str(exc))
            raise typer.Exit(1) from exc

    return wrapper


def build_client(
    delay: float = 0.1, fmt: str = "table", output: Path | None = None
) -> BoomiClient:
    config = load_config()
    warning = permission_warning()
    if warning:
        err_console.print(warning)
    # Progress (stderr) is hidden in json/csv mode so piped stdout reads
    # cleanly — but when --output writes the report to a file, stdout isn't
    # the report stream, so keep progress on rather than run silent for
    # what can be a 30-minute audit.
    # CLI runs get extra retry patience (up to ~60s of backoff): audits are
    # long batch jobs and Boomi throttles sustained traffic with 503s.
    show_progress = fmt == "table" or output is not None
    return BoomiClient(config, delay=delay, show_progress=show_progress, max_retries=5)


def processing_status(client: BoomiClient, message: str) -> AbstractContextManager:
    """Spinner for post-fetch processing phases so long runs never look hung.

    Mirrors paginate()'s first-request spinner: shown in table mode only
    (client.show_progress), silent for json/csv pipes.
    """
    return err_console.status(message) if client.show_progress else nullcontext()


def emit(rows: list[dict], fmt: str, output: Path | None, force: bool, title: str) -> None:
    if output is not None and fmt == "table":
        raise BoomiAPIError("❌ --output requires --format csv or --format json")
    content = formatters.render(rows, fmt=fmt, title=title)
    if output is not None and content is not None:
        formatters.write_output(content, output, force=force)


def is_truthy(value: Any) -> bool:
    return value is True or str(value).lower() == "true"


def fetch_environments(client: BoomiClient) -> list[dict]:
    return client.paginate("Environment", show_progress=False)


def resolve_env(environments: list[dict], name: str) -> dict:
    """Find an environment by exact name, falling back to a unique
    case-insensitive substring match — real accounts use names like
    "05. Production APIM" that nobody should have to type exactly."""
    for env in environments:
        if env.get("name") == name:
            return env
    needle = name.lower()
    matches = [e for e in environments if needle in str(e.get("name", "")).lower()]
    if len(matches) == 1:
        err_console.print(f"→ Using environment '{matches[0]['name']}'")
        return matches[0]
    if matches:
        candidates = ", ".join(sorted(str(e.get("name")) for e in matches))
        raise BoomiAPIError(
            f"❌ Environment '{name}' is ambiguous. Matches: {candidates}"
        )
    available = ", ".join(sorted(str(e.get("name")) for e in environments))
    raise BoomiAPIError(f"❌ Environment '{name}' not found. Available: {available}")


def fetch_connector_components(
    client: BoomiClient,
    *,
    include_deleted: bool = False,
    connection_only: bool = False,
    max_records: int = 0,
) -> list[dict]:
    """Fetch Component records, keeping only connector types.

    Default behaviour excludes deleted/archived components — callers must
    opt in via include_deleted. The query filters server-side; the loop below
    re-applies the same rules as a defensive layer (and to keep behaviour
    identical if Boomi returns more than asked for).
    """
    components = client.paginate(
        "ComponentMetadata",
        component_metadata_filter(
            connection_only=connection_only, include_deleted=include_deleted
        ),
        max_records=max_records,
        description="Fetching connections..." if connection_only else "Fetching connectors...",
    )
    kept = []
    for component in components:
        ctype = str(component.get("type") or "")
        if not ctype.startswith("connector"):
            continue
        if connection_only and ctype != CONNECTION_TYPE:
            continue
        if not include_deleted and is_truthy(component.get("deleted")):
            continue
        kept.append(component)
    return kept


def extension_presence(client: BoomiClient, environments: list[dict]) -> dict[str, set]:
    """Map connection componentId → environment names with extension entries.

    Connections are normally deployed *inside* process packages, so
    DeployedPackage rarely lists them directly — their per-environment
    presence is discovered through EnvironmentExtensions instead. Reuses the
    same per-env queries as fetch_extensions, so the client cache makes this
    free when both run.
    """
    presence: dict[str, set] = defaultdict(set)
    for env in environments:
        for item in _env_extensions(client, env):
            connections = item.get("connections") or {}
            if isinstance(connections, dict):
                connections = connections.get("connection") or []
            for connection in connections:
                if connection.get("id"):
                    presence[connection["id"]].add(env["name"])
    return presence


def connection_settings(
    client: BoomiClient, environments: list[dict]
) -> dict[str, dict[str, dict[str, str]]]:
    """Per-environment connection settings for report rows.

    Returns {env_name: {connection_id: {"url", "user", "credentials"}}} pulled
    from the extension fields. Encrypted fields never surface a value: they
    render as [ENCRYPTED] and flip the credentials indicator. Reuses the same
    per-env queries as extension_presence, so the client cache makes this free.
    """
    settings: dict[str, dict[str, dict[str, str]]] = defaultdict(dict)
    for env in environments:
        for item in _env_extensions(client, env):
            connections = item.get("connections") or {}
            if isinstance(connections, dict):
                connections = connections.get("connection") or []
            for connection in connections:
                if not connection.get("id"):
                    continue
                fields = connection.get("field") or []
                if isinstance(fields, dict):
                    fields = [fields]
                url = user = credentials = ""
                for field in fields:
                    field_id = str(field.get("id") or "")
                    if not field_id:
                        continue
                    encrypted = is_encrypted_field(
                        field_id,
                        {"encryptedValueSet": is_truthy(field.get("encryptedValueSet"))},
                    )
                    value = (
                        ENCRYPTED_PLACEHOLDER
                        if encrypted
                        else str(field.get("value") or "")
                    )
                    if encrypted:
                        credentials = ENCRYPTED_PLACEHOLDER
                    if not url and ENDPOINT_FIELD_PATTERN.search(field_id):
                        # A URL can embed credentials (user:pass@host) that no
                        # field-name check would catch — scrub before display.
                        url = scrub_url_credentials(value)
                    elif not user and USER_FIELD_PATTERN.search(field_id):
                        user = value
                settings[env["name"]][connection["id"]] = {
                    "url": url,
                    "user": user,
                    "credentials": credentials,
                }
    return settings


def merge_deployments(
    client: BoomiClient, components: list[dict]
) -> tuple[list[dict], list[dict]]:
    """Merge Component records with DeployedPackage data, deduped by componentId."""
    environments = fetch_environments(client)
    env_names = {e.get("id"): e.get("name") for e in environments}
    deployments = client.paginate("DeployedPackage", ACTIVE_DEPLOYMENT_FILTER)

    deployed_envs: dict[str, set] = defaultdict(set)
    for deployment in deployments:
        if is_truthy(deployment.get("active", True)):
            env_name = env_names.get(deployment.get("environmentId"))
            if env_name:
                deployed_envs[deployment.get("componentId")].add(env_name)

    # The longest silent stretch of the run: presence scanning queries
    # EnvironmentExtensions once per environment with no pagination progress
    # of its own, so cover it (and the row assembly) with a status spinner.
    with processing_status(
        client, "Scanning environment extensions and generating report..."
    ):
        presence = extension_presence(client, environments)

        rows: list[dict] = []
        seen: set[str] = set()
        for component in components:
            component_id = component.get("componentId")
            if component_id in seen:
                continue
            seen.add(component_id)
            rows.append(
                {
                    "componentId": component_id,
                    "name": component.get("name"),
                    "type": FRIENDLY_TYPES.get(
                        component.get("type"), component.get("type")
                    ),
                    "version": component.get("version"),
                    "status": "deleted"
                    if is_truthy(component.get("deleted"))
                    else "active",
                    # Union of DeployedPackage entries and extension presence:
                    # connections deploy inside process packages, so packaging
                    # data alone never places them in an environment.
                    "environments": sorted(
                        deployed_envs.get(component_id, set())
                        | presence.get(component_id, set())
                    ),
                }
            )
    return rows, environments


def warn_offline_atoms(client: BoomiClient) -> list[dict]:
    """Print a warning for every Atom that is not ONLINE."""
    atoms = client.paginate("Atom", show_progress=False)
    for atom in atoms:
        if str(atom.get("status") or "").upper() != "ONLINE":
            err_console.print(
                f"⚠️  WARNING — Connector deployed to offline Atom: {atom.get('name')} "
                f"(last seen: {atom.get('dateInstalled', 'unknown')})"
            )
    return atoms


def normalize_extensions(item: dict) -> dict[str, dict[str, dict]]:
    """Normalize one EnvironmentExtensions record.

    Returns {connector_name: {field_id: {"value", "encryptedValueSet", "type"}}}.
    Handles both the nested JSON shape ({"connections": {"connection": [...]}})
    and the flat shape ({"connections": [...]}).
    """
    connections = item.get("connections") or {}
    if isinstance(connections, dict):
        connections = connections.get("connection") or []
    normalized: dict[str, dict[str, dict]] = {}
    for connection in connections:
        fields = connection.get("field") or []
        if isinstance(fields, dict):
            fields = [fields]
        normalized[connection.get("name")] = {
            field["id"]: {
                "value": field.get("value"),
                "encryptedValueSet": is_truthy(field.get("encryptedValueSet")),
                "type": field.get("type"),
            }
            for field in fields
            if field.get("id")
        }
    return normalized


def _env_extensions(client: BoomiClient, env: dict) -> list[dict]:
    query_filter = {
        "expression": {
            "argument": [env["id"]],
            "operator": "EQUALS",
            "property": "environmentId",
        }
    }
    return client.paginate("EnvironmentExtensions", query_filter, show_progress=False)


def fetch_extensions(
    client: BoomiClient, environments: list[dict]
) -> dict[str, dict[str, dict[str, dict]]]:
    """Fetch and normalize extensions for each environment, keyed by env name."""
    extensions: dict[str, dict[str, dict[str, dict]]] = {}
    # One un-paginated query per environment — silent without this spinner.
    with processing_status(client, "Scanning environment extensions..."):
        for env in environments:
            merged: dict[str, dict[str, dict]] = {}
            for item in _env_extensions(client, env):
                merged.update(normalize_extensions(item))
            extensions[env["name"]] = merged
    return extensions
