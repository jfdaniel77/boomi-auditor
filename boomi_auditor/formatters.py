"""Output rendering: Rich table (default), JSON, and CSV."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.prompt import Confirm
from rich.table import Table

console = Console()
err_console = Console(stderr=True)

FORMATS = ("table", "json", "csv")

SEVERITY_STYLES = {
    "error": "bold red",
    "warning": "yellow",
    "info": "cyan",
}


def to_json(rows: list[dict]) -> str:
    return json.dumps(rows, indent=2, default=str)


def _fieldnames(rows: list[dict]) -> list[str]:
    names: list[str] = []
    for row in rows:
        for key in row:
            if key not in names:
                names.append(key)
    return names


def to_csv(rows: list[dict]) -> str:
    if not rows:
        return ""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=_fieldnames(rows), extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: _flatten(v) for k, v in row.items()})
    return buffer.getvalue()


def _flatten(value: Any) -> Any:
    if isinstance(value, (list, tuple)):
        return "; ".join(str(v) for v in value)
    if isinstance(value, dict):
        return json.dumps(value, default=str)
    return value


def build_table(rows: list[dict], title: str | None = None) -> Table:
    table = Table(title=title, expand=False)
    if not rows:
        table.add_column("result")
        table.add_row("✅ No findings")
        return table
    names = _fieldnames(rows)
    for name in names:
        table.add_column(name)
    for row in rows:
        style = SEVERITY_STYLES.get(str(row.get("severity", "")).lower())
        table.add_row(*(str(_flatten(row.get(name, ""))) for name in names), style=style)
    return table


def render(rows: list[dict], fmt: str = "table", title: str | None = None) -> str | None:
    """Render to stdout. Returns the raw string for json/csv (None for table)."""
    if fmt == "json":
        content = to_json(rows)
        print(content)
        return content
    if fmt == "csv":
        content = to_csv(rows)
        print(content, end="")
        return content
    console.print(build_table(rows, title=title))
    return None


def write_output(content: str, path: Path, force: bool = False) -> bool:
    """Write content to path, prompting before overwriting unless --force."""
    if path.exists() and not force:
        if not Confirm.ask(f"⚠️  File {path.name} already exists. Overwrite?", default=False):
            err_console.print("Aborted — no file written.")
            return False
    path.write_text(content)
    err_console.print(f"✅ Wrote {path}")
    return True
