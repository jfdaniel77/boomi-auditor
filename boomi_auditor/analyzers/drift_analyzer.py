"""Extension drift detection between two environments.

Flags:
- DRIFT:   target-env value equals source-env value (override likely forgotten)
- MISSING: field has a value in the target env but is missing/empty in the source env
- SCHEMA:  field type metadata differs between environments
- ENCRYPTED: encrypted fields are reported but values are NEVER shown
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..redaction import scrub_url_credentials

ENCRYPTED_PLACEHOLDER = "[ENCRYPTED]"
# Fallback when API metadata is absent: common secret-bearing field names.
ENCRYPTED_NAME_PATTERN = re.compile(r"password|token|secret|key", re.IGNORECASE)

EMPTY = (None, "")


@dataclass
class DriftFinding:
    kind: str  # "drift" | "missing" | "schema" | "encrypted"
    connector: str
    field: str
    value_from: str | None
    value_to: str | None
    message: str

    @property
    def severity(self) -> str:
        return {"drift": "error", "missing": "warning", "schema": "warning"}.get(
            self.kind, "info"
        )

    def to_row(self) -> dict:
        return {
            "kind": self.kind.upper(),
            "severity": self.severity,
            "connector": self.connector,
            "field": self.field,
            "value_from": self.value_from,
            "value_to": self.value_to,
            "message": self.message,
        }


def is_encrypted_field(field_id: str, meta: dict | None) -> bool:
    if meta and meta.get("encryptedValueSet"):
        return True
    return bool(ENCRYPTED_NAME_PATTERN.search(field_id))


def analyze_drift(
    extensions_by_env: dict[str, dict[str, dict[str, dict]]],
    env_from: str = "DEV",
    env_to: str = "PROD",
    connector: str | None = None,
) -> list[DriftFinding]:
    """Compare extension fields between env_from and env_to.

    Input shape: {env: {connector_name: {field_id: {"value": ..., "encryptedValueSet": ...,
    "type": ...}}}}
    """
    source = extensions_by_env.get(env_from, {})
    target = extensions_by_env.get(env_to, {})
    findings: list[DriftFinding] = []

    for conn_name, target_fields in target.items():
        if connector and connector.lower() not in conn_name.lower():
            continue
        source_fields = source.get(conn_name, {})
        for field_id, target_meta in target_fields.items():
            source_meta = source_fields.get(field_id)
            if is_encrypted_field(field_id, target_meta):
                findings.append(
                    DriftFinding(
                        "encrypted",
                        conn_name,
                        field_id,
                        ENCRYPTED_PLACEHOLDER,
                        ENCRYPTED_PLACEHOLDER,
                        f'🔒 ENCRYPTED — "{conn_name}" field {field_id} → '
                        f"{ENCRYPTED_PLACEHOLDER} (value not shown)",
                    )
                )
                continue

            target_value = (target_meta or {}).get("value")
            source_value = (source_meta or {}).get("value")

            if source_meta is None or source_value in EMPTY:
                if target_value not in EMPTY:
                    findings.append(
                        DriftFinding(
                            "missing",
                            conn_name,
                            field_id,
                            scrub_url_credentials(source_value),
                            scrub_url_credentials(target_value),
                            f'⚠️  MISSING — "{conn_name}" [field in {env_to}, '
                            f"empty in {env_from}] field {field_id}",
                        )
                    )
                continue

            source_type = source_meta.get("type")
            target_type = (target_meta or {}).get("type")
            if source_type and target_type and source_type != target_type:
                findings.append(
                    DriftFinding(
                        "schema",
                        conn_name,
                        field_id,
                        source_type,
                        target_type,
                        f'⚠️  SCHEMA — "{conn_name}" field {field_id} type differs: '
                        f"{env_from}={source_type} {env_to}={target_type}",
                    )
                )
                continue

            if target_value == source_value and target_value not in EMPTY:
                findings.append(
                    DriftFinding(
                        "drift",
                        conn_name,
                        field_id,
                        scrub_url_credentials(source_value),
                        scrub_url_credentials(target_value),
                        f'❌ DRIFT — "{conn_name}" [{env_to} {field_id} = {env_from} '
                        f"{field_id}] — same as {env_from}, likely misconfigured",
                    )
                )
    return findings
