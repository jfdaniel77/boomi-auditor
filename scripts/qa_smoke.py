"""Lightweight QA smoke script for the Boomi auditor collectors.

This is intentionally fixture-driven and does not make real API calls.
It exercises the main command collectors across representative options so
release validation can be repeated quickly.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.conftest import (  # noqa: E402
    FakeClient,
    component_metadata_by_type,
    extensions_by_env_id,
    load_fixture,
    references_by_parent_id,
)

from boomi_auditor.commands.connections import collect_connections
from boomi_auditor.commands.connectors import collect_connectors
from boomi_auditor.commands.drift import collect_drift
from boomi_auditor.commands.duplicates import collect_duplicates
from boomi_auditor.commands.processes import collect_processes


def build_client() -> FakeClient:
    return FakeClient(
        {
            "ComponentMetadata": component_metadata_by_type,
            "Environment": load_fixture("environments_response.json")["result"],
            "DeployedPackage": load_fixture("packaged_components.json")["result"],
            "Atom": load_fixture("atoms_response.json")["result"],
            "ComponentReference": references_by_parent_id,
            "EnvironmentExtensions": extensions_by_env_id,
        }
    )


def main() -> None:
    client = build_client()

    scenarios = [
        ("connectors/default", collect_connectors(client)),
        ("connectors/env-prod", collect_connectors(client, env="prod")),
        ("connectors/deployed-only", collect_connectors(client, deployed_only=True)),
        ("connections/default", collect_connections(client)),
        ("connections/env-prod", collect_connections(client, env="prod")),
        ("duplicates/default", collect_duplicates(client)),
        ("drift/default", collect_drift(client)),
        ("processes/default", collect_processes(client, workers=1)),
        ("processes/env-prod", collect_processes(client, env="prod", workers=1)),
    ]

    for name, rows in scenarios:
        print(f"{name}: {len(rows)} rows")


if __name__ == "__main__":
    main()
