"""Connector → process dependency mapping.

Answers: "If I change this connector, what breaks?" Flags processes deployed
to the reference environment but missing from the baseline environment, and
connectors that no process references at all.
"""

from __future__ import annotations

from collections import defaultdict


def map_dependencies(
    processes: list[dict],
    connectors: list[dict],
    reference_env: str | None = "PROD",
    baseline_env: str | None = "DEV",
) -> list[dict]:
    """Build one row per connector/process pair.

    Input shapes:
    - processes:  [{"name": str, "environments": [str], "connectors": [str]}]
    - connectors: [{"name": str, ...}]

    Passing None for reference_env/baseline_env disables environment-gap
    flags (used when the account has no unambiguous env pair to compare).
    """
    by_connector: dict[str, list[dict]] = defaultdict(list)
    for proc in processes:
        for conn_name in proc.get("connectors", []):
            by_connector[conn_name].append(proc)

    rows: list[dict] = []
    for conn_name in sorted(by_connector):
        for proc in by_connector[conn_name]:
            envs = proc.get("environments", [])
            note = None
            envs_known = bool(reference_env and baseline_env)
            if envs_known and reference_env in envs and baseline_env not in envs:
                note = f"⚠️  in {reference_env} but missing in {baseline_env}"
            rows.append(
                {
                    "connector": conn_name,
                    "process": proc.get("name"),
                    "environments": sorted(envs),
                    "notes": note,
                }
            )

    known_names = {c.get("name") for c in connectors if c.get("name")}
    for unused in sorted(known_names - set(by_connector)):
        rows.append(
            {
                "connector": unused,
                "process": None,
                "environments": [],
                "notes": "ℹ️  unused — no processes reference this connector",
            }
        )
    return rows
