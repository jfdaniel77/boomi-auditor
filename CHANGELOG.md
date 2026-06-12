# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Progress labels now describe what is being fetched ("Fetching
  processes...", "Fetching connectors...", "Fetching connections...")
  instead of the raw API object type, so the two ComponentMetadata fetches
  in `processes` no longer render two identical progress bars.
- Progress bars and status spinners stay on (stderr) when `--output` writes
  the report to a file: stdout isn't the report stream in that case, so a
  long json/csv audit no longer runs in complete silence. Progress is still
  hidden when json/csv goes to stdout, keeping piped output clean.

### Security

- Hardened error-message redaction so sensitive values embedded in Boomi API
  error bodies (for example query parameters or assignment-style values) are
  masked before they reach user-facing output.

## [0.1.0] - 2026-06-10

### Added

- `boomi-audit connectors` — list deployed connectors, excluding
  deleted/archived components by default (`--include-deleted` to opt in),
  with `--env` and `--type` filters; `--env` scopes the environments column
  to the requested environment, and the type column shows the Build-UI
  labels (Connection/Operation) instead of Boomi's API type names.
- `boomi-audit connections` — the connection configuration view: one row per
  connection per environment, with the endpoint URL, user, and a masked
  credentials indicator from that environment's extension values (no type
  column — every row is a connection).
- `--deployed-only` on `connectors` and `connections` — keep only rows
  placed in at least one environment, for license reviews (deployed
  connections are what count against Boomi connection licenses).
- `boomi-audit duplicates` — version-aware duplication detection:
  TYPE A (same name + same version in one environment),
  TYPE B (same endpoint URL behind differently-named connections),
  TYPE C (identical config across environments, informational).
- `boomi-audit drift` — extension drift detection between environments:
  forgotten overrides (PROD = DEV), fields missing/empty in the baseline,
  schema differences; encrypted fields always masked as `[ENCRYPTED]`.
- `boomi-audit processes` — connector → process dependency map with a notes
  column flagging environment gaps and unused connectors.
- `boomi-audit all` — full audit combining every check into one report.
- `boomi-audit init` — credential setup creating `~/.boomi-auditor/config.json`
  at permissions `0o600` from the moment of writing (directory `0o700`),
  closing the brief default-umask window left by writing then chmod-ing.
- Defense in depth for secrets the field-name heuristics cannot see:
  credentials embedded in URL values (`https://user:pass@host`, `?password=…`
  query parameters) are scrubbed to `[REDACTED]` before any URL is displayed
  by `connections` or `drift`.
- Python 3.10+ supported (was 3.13+); CI verifies the full 3.10–3.13 range.
- Transparent pagination over the Boomi queryToken cursor with a Rich
  progress bar (hidden for json/csv output) and an optional `--max-records`
  safety cap.
- Exponential backoff retry (1s → 2s → 4s) for 429 and 5xx responses, plus a
  `--delay` flag for large accounts.
- Performance on large accounts: component queries filtered server-side
  (connector types / current version / deleted), deployment queries limited
  to active packages, parallel process-reference lookups (`--workers`,
  default 4) with a progress bar, and a per-run query cache so
  `boomi-audit all` fetches shared data once.
- Status spinners during slow fetches and post-fetch processing ("Scanning
  environment extensions and generating report...") so long-running commands
  never look hung; `--env` names are validated before any heavy fetching
  starts.
- Environment name matching by unique case-insensitive substring
  (`--env prod` finds "05. Production APIM"); ambiguous names list the
  candidates.
- Connector environment membership derived from environment extension
  entries in addition to packaged deployments — connections deployed inside
  process packages (the normal case) are now attributed to their
  environments.
- Process dependency mapping resolves the full Boomi reference chain
  (process → operation → connection), so connections used through operations
  are attributed correctly; reference queries pair `parentComponentId` with
  the required `parentVersion`.
- Unused-connector detection reports connections only and is skipped when
  `--max-records` caps the process list (partial data cannot prove a
  connector unused).
- Resilience for long audits: CLI runs retry throttled responses up to five
  times with capped exponential backoff honoring `Retry-After`, and a
  process whose references repeatedly fail is skipped with a warning
  instead of aborting the whole run. Network-level failures (dropped
  connections, timeouts) are retried the same way and end in a readable
  message, never a raw traceback.
- `--env-from`/`--env-to` on `processes` and `all` for accounts whose
  environments aren't named DEV/PROD; when the defaults don't resolve,
  gap flags and the drift section degrade gracefully instead of failing.
- Output formats: Rich table (default), JSON, CSV, with `--output` file
  writing, overwrite prompt, and `--force`.
- Config priority: CLI flags → environment variables → config file; token
  masking in all output; config file permission warnings.
- GitHub Actions CI (pytest with 80% coverage gate + ruff lint).
- Column reference in the README: every report column explained — what it
  means, how to interpret it, and which use case needs it.

[Unreleased]: https://github.com/jfdaniel77/boomi-auditor/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/jfdaniel77/boomi-auditor/releases/tag/v0.1.0
