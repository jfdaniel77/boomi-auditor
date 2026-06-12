"""Defensive secret redaction for values that reach report output.

The field-name/metadata heuristics in drift_analyzer catch *fields* that are
secrets. This module catches secrets hidden *inside* otherwise-shown values —
chiefly credentials embedded in connection URLs — which no field-name check
would ever flag. Applied to every URL-bearing value before display so a
mis-shaped or unexpected Boomi payload cannot leak a credential.
"""

from __future__ import annotations

import re

REDACTED = "[REDACTED]"

# RFC-3986 userinfo: scheme://[user[:password]@]host. The [^/@\s] class stops
# the match at the authority boundary, so it never touches a credential-free
# "jdbc:oracle:thin:@host" (no //) or an empty "://@host".
_USERINFO = re.compile(r"(://)[^/@\s]+@")

# Credentials passed as query parameters (?password=…&token=…). Anchored on
# the param-name boundary so "sortkey=" / "monkey=" are left alone. Bare "key"
# is intentionally excluded — it is too often a benign lookup parameter.
_CRED_PARAM = re.compile(
    r"([?&](?:password|passwd|pwd|secret|token|api[_-]?key|apikey)=)([^&\s]+)",
    re.IGNORECASE,
)
_CRED_ASSIGN = re.compile(
    r"(\b(?:password|passwd|pwd|secret|token|api[_-]?key|apikey)\s*=\s*)([^,&\s;]+)",
    re.IGNORECASE,
)
# Values that are obviously secret-like should be treated as sensitive even when
# they are not embedded in a URL.
_SECRET_LIKE = re.compile(
    r"(?:password|passwd|pwd|secret|token|api[_-]?key|apikey|credential|private[_-]?key|access[_-]?token)",
    re.IGNORECASE,
)


def scrub_sensitive_value(value: str | None) -> str:
    """Redact obvious secret values and embedded credentials before display."""
    if not value:
        return value or ""
    text = str(value)
    scrubbed = _USERINFO.sub(rf"\1{REDACTED}@", text)
    scrubbed = _CRED_PARAM.sub(rf"\1{REDACTED}", scrubbed)
    scrubbed = _CRED_ASSIGN.sub(rf"\1{REDACTED}", scrubbed)

    # Only redact a whole standalone value when it is clearly just a secret,
    # not a larger message that should keep its surrounding context.
    stripped = scrubbed.strip()
    if scrubbed == text and _SECRET_LIKE.search(stripped):
        if not any(ch.isspace() for ch in stripped) and not any(
            ch in stripped for ch in ":/=?&@"
        ):
            return REDACTED
    return scrubbed


def scrub_url_credentials(value: str | None) -> str:
    """Backward-compatible alias for the defensively redacted display value."""
    return scrub_sensitive_value(value)
