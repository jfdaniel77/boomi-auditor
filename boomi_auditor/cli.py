"""boomi-audit CLI entry point."""

from __future__ import annotations

import typer

from . import __version__
from .commands import all as all_command
from .commands import connections, connectors, drift, duplicates, processes
from .config import CONFIG_PATH, init_config

app = typer.Typer(
    name="boomi-audit",
    help="Audit Boomi connectors, connections, and environment extensions.",
    no_args_is_help=True,
)

app.command("connectors")(connectors.connectors)
app.command("connections")(connections.connections)
app.command("duplicates")(duplicates.duplicates)
app.command("drift")(drift.drift)
app.command("processes")(processes.processes)
app.command("all")(all_command.full_audit)


@app.command("init")
def init(
    account_id: str = typer.Option(..., prompt="Boomi account ID"),
    username: str = typer.Option(..., prompt="Boomi username"),
    api_token: str = typer.Option(..., prompt="Boomi API token", hide_input=True),
) -> None:
    """Store credentials in ~/.boomi-auditor/config.json (chmod 600)."""
    path = init_config(account_id, username, api_token, config_path=CONFIG_PATH)
    typer.echo(f"✅ Config written to {path} (permissions locked to 600)")


@app.command("version")
def version() -> None:
    """Print the boomi-auditor version."""
    typer.echo(__version__)


if __name__ == "__main__":
    app()
