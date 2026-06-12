"""Duplication detection across connector components and connections.

TYPE A: same name + same environment + same VERSION (likely a mistake).
        Same name at different versions is expected and never flagged.
TYPE B: same endpoint URL behind multiple differently-named connections
        in the same environment (consolidation candidate).
TYPE C: identical config for the same connector across environments
        (informational — may be intentional for some connector types).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field


@dataclass
class DuplicateFinding:
    dup_type: str  # "A" | "B" | "C"
    severity: str  # "warning" | "info"
    message: str
    components: list[dict] = field(default_factory=list)

    def to_row(self) -> dict:
        return {
            "type": f"TYPE {self.dup_type}",
            "severity": self.severity,
            "message": self.message,
            "count": len(self.components),
        }


def find_duplicates(connectors: list[dict]) -> list[DuplicateFinding]:
    """Run all three duplication checks over normalized connector records.

    Expected record shape:
    {name, version, environment, endpoint, config: dict, componentId, deleted}
    """
    active = [c for c in connectors if not c.get("deleted")]
    return (
        find_name_duplicates(active)
        + find_endpoint_duplicates(active)
        + find_cross_env_identicals(active)
    )


def find_name_duplicates(connectors: list[dict]) -> list[DuplicateFinding]:
    """TYPE A — same name, same environment, same version."""
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for conn in connectors:
        key = (conn.get("environment"), conn.get("name"), conn.get("version"))
        groups[key].append(conn)
    findings = []
    for (env, name, version), items in groups.items():
        if len(items) > 1:
            findings.append(
                DuplicateFinding(
                    "A",
                    "warning",
                    f'⚠️  TYPE A — Duplicate name in {env}: "{name}" v{version} '
                    f"({len(items)} instances)",
                    items,
                )
            )
    return findings


def find_endpoint_duplicates(connectors: list[dict]) -> list[DuplicateFinding]:
    """TYPE B — same endpoint URL, multiple differently-named connections."""
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for conn in connectors:
        endpoint = conn.get("endpoint")
        if endpoint:
            groups[(conn.get("environment"), endpoint)].append(conn)
    findings = []
    for (env, endpoint), items in groups.items():
        names = {c.get("name") for c in items}
        if len(names) > 1:
            findings.append(
                DuplicateFinding(
                    "B",
                    "warning",
                    f"⚠️  TYPE B — Same endpoint in {env}: {endpoint} → "
                    f"{len(names)} connections",
                    items,
                )
            )
    return findings


def find_cross_env_identicals(connectors: list[dict]) -> list[DuplicateFinding]:
    """TYPE C — identical non-empty config for the same connector across environments."""
    by_name: dict[str, list[dict]] = defaultdict(list)
    for conn in connectors:
        if conn.get("name") and conn.get("environment"):
            by_name[conn["name"]].append(conn)
    findings = []
    for name, items in by_name.items():
        envs = sorted({c["environment"] for c in items})
        if len(envs) < 2:
            continue
        configs = [c.get("config") or {} for c in items]
        if all(configs) and all(cfg == configs[0] for cfg in configs[1:]):
            findings.append(
                DuplicateFinding(
                    "C",
                    "info",
                    f'ℹ️  TYPE C — Identical config across {"/".join(envs)}: "{name}"',
                    items,
                )
            )
    return findings
