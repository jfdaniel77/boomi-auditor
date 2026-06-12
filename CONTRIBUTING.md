# Contributing to boomi-auditor

Thanks for your interest in contributing!

## Local development setup

```bash
git clone https://github.com/jfdaniel77/boomi-auditor.git
cd boomi-auditor
python -m venv .venv && source .venv/bin/activate   # .venv\Scripts\activate on Windows
make install        # pip install -e ".[dev]"
```

You do **not** need a Boomi account to develop or test — the entire test
suite mocks the Boomi API via [respx](https://lundberg.github.io/respx/).

## Running tests

```bash
make test           # full suite with coverage; fails below 80%
pytest tests/unit/test_drift_analyzer.py            # one file
pytest tests/unit/test_client.py -k pagination      # one scenario
make lint           # ruff
```

## Pull request guidelines

- **One feature or fix per PR.** Small, focused PRs get reviewed faster.
- **Tests required.** New behaviour needs unit tests; CLI-visible behaviour
  also needs an integration test. Coverage must stay at or above 80%.
- **Changelog entry required.** Add a line under `[Unreleased]` in
  `CHANGELOG.md` (Keep a Changelog format).
- Match the existing code style: type hints throughout, small
  single-purpose functions, `pathlib.Path` for all file paths, analyzers
  stay pure (no API or CLI dependencies).
- Never log or print API tokens or encrypted extension values — not even in
  test fixtures' expected output.

## Reporting issues

Use the issue templates:

- **Bug report** — include the command you ran, expected vs actual output
  (with tokens redacted!), Python version, and OS.
- **Feature request** — describe the audit question you're trying to answer,
  not just the proposed flag; it helps us design the right interface.

## Release process (maintainers)

1. Move `[Unreleased]` entries to a new version section in `CHANGELOG.md`.
2. Bump `version` in `pyproject.toml` and `boomi_auditor/__init__.py`
   (SemVer: MAJOR.MINOR.PATCH).
3. Tag `vX.Y.Z` and push; build with `make build`.
