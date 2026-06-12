# boomi-auditor

[![CI](https://github.com/jfdaniel77/boomi-auditor/actions/workflows/ci.yml/badge.svg)](https://github.com/jfdaniel77/boomi-auditor/actions/workflows/ci.yml)
![Coverage](https://img.shields.io/badge/coverage-80%25%2B-brightgreen)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)

A command-line tool for Boomi integration teams to audit connector components,
connection accounts, and environment extensions across DEV/SIT/QAT/PROD
environments. It answers three questions every Boomi team eventually asks:

1. **What connectors do we currently have deployed?** (excluding deleted/archived)
2. **Are there any duplicated connectors?**
3. **Do the same connectors have inconsistent extensions across environments?**

## Install

```bash
pip install boomi-auditor        # from PyPI (when published)
# or from source:
git clone https://github.com/jfdaniel77/boomi-auditor.git
cd boomi-auditor
make install
```

Requires Python 3.10+.

## Quickstart

```bash
# Store credentials (written to ~/.boomi-auditor/config.json, chmod 600)
boomi-audit init

# Or use environment variables instead:
export BOOMI_ACCOUNT_ID=your-account-id
export BOOMI_USERNAME=you@example.com
export BOOMI_API_TOKEN=your-api-token

boomi-audit connectors
```

Config priority: CLI flags → environment variables → `~/.boomi-auditor/config.json`.

`BOOMI_USERNAME` is your normal Boomi login email — the `BOOMI_TOKEN.`
prefix that Boomi requires for token authentication is added automatically.

## Use Case 1 — Show current connectors

```bash
boomi-audit connectors                        # all active connectors
boomi-audit connectors --env PROD             # scoped to one environment
boomi-audit connectors --type "HTTP Client"
boomi-audit connectors --include-deleted      # opt in to show deleted
boomi-audit connectors --deployed-only        # license-review view
boomi-audit connectors --format csv --output report.csv
```

Deleted/archived components are excluded automatically. Because connections
are normally deployed inside process packages rather than individually, a
connector's environment list combines packaged deployments with the
environments where it has extension entries. With `--env`, the environments
column shows only the requested environment — omit it for the
cross-environment view. Connectors deployed to offline Atoms are flagged
with a warning.

Environment names (`--env`, `--env-from`, `--env-to`) match exactly or by
unique case-insensitive substring — `--env prod` finds an environment named
"05. Production APIM". Ambiguous or unknown names fail immediately with the
candidate list, before any data is fetched.

### Connection configuration view

```bash
boomi-audit connections                  # one row per connection per environment
boomi-audit connections --env PROD
```

Where `connectors` is the inventory (Connections *and* Operations),
`connections` is the configuration view: one row per connection per
environment, showing that environment's endpoint URL, user, and a masked
credentials indicator pulled from its extension values. Encrypted fields are
never shown — a connection with a stored secret displays `[ENCRYPTED]`.
Connections not deployed to any environment appear once with an empty
environment column.

`--deployed-only` (both commands) keeps only rows placed in at least one
environment — deployed connections are what count against Boomi connection
licenses. It is not the default for two reasons: an empty environment column
can also mean the connection is live but was never set up as environment
extensions (so the tool cannot see where it runs), and the undeployed rows
are exactly the cleanup candidates that free licenses.

## Use Case 2 — Duplication detection

```bash
boomi-audit duplicates
boomi-audit duplicates --env PROD
```

Three duplication types are flagged separately:

```
⚠️  TYPE A — Duplicate name in DEV: "SAP HTTP Client" v3 (2 instances)
⚠️  TYPE B — Same endpoint in PROD: https://api.example.com → 2 connections
ℹ️  TYPE C — Identical config across DEV/PROD: "Oracle DB Connector"
```

Version-aware: the same component at *different* versions is expected and is
**not** flagged — only same name + same version counts as TYPE A.

## Use Case 3 — Extension drift detection

```bash
boomi-audit drift
boomi-audit drift --connector "HTTP Client"
boomi-audit drift --env-from DEV --env-to PROD
```

```
❌ DRIFT — "SAP HTTP Client" [PROD url = DEV url] — same as DEV, likely misconfigured
⚠️  MISSING — "Oracle DB" [field in PROD, empty in DEV] field db_host
🔒 ENCRYPTED — "Salesforce Connector" field client_secret → [ENCRYPTED] (value not shown)
```

Encrypted fields (detected via `encryptedValueSet` metadata or field-name
patterns like `password`/`token`/`secret`/`key`) are **always** shown as
`[ENCRYPTED]` — raw values never appear in any output.

## Use Case 4 — Process dependency map

```bash
boomi-audit processes
boomi-audit processes --connector "HTTP Client"
boomi-audit processes --env PROD
```

For each connector, shows which processes use it — "if I change this
connector, what breaks?" Processes deployed to PROD but missing in DEV are
flagged, as are connectors no process references at all.

Reference lookups are one API call per process (a Boomi API constraint), so
they run in parallel — `--workers 4` by default. Raise it to speed up large
accounts, or lower it to `--workers 1` if your account is rate-limited. The
full Boomi reference chain is resolved (process → operation → connection),
so connections used through operations are attributed correctly.

"In X but missing in Y" gap flags compare DEV and PROD by default; if your
environments are named differently, set `--env-from`/`--env-to` (also
accepted by `all`, where they drive the drift section too). Without a
resolvable pair the flags are simply disabled with a note.

## Full audit

```bash
boomi-audit all --format json > report.json
```

## Column reference

What each report column means, how to read it, and when to look at it.

### `connectors` — the inventory (Use Case 1)

| Column | Meaning and how to read it |
|---|---|
| `componentId` | Boomi's unique id for the component. Use it to find the component in the Build UI or in other API calls, and to tell apart components that share a name. |
| `name` | The component name from Build. Names are **not** unique — duplicates are exactly what `duplicates` hunts for. |
| `type` | `Connection` (the endpoint/credentials part, Boomi API type `connector-settings`) or `Operation` (the action performed against it, `connector-action`). Only connections carry extension values and count against connection licenses. |
| `version` | Current component version. |
| `status` | `active`, or `deleted` for archived components (shown only with `--include-deleted`). |
| `environments` | Where the component is verifiably placed: the union of active packaged deployments and environments holding extension entries for it. With `--env` it shows just the requested environment. |

An **empty `environments` cell does not prove the component is unused** — a
connection configured directly in the component (never set up as environment
extensions) is invisible here even when it runs in production, and operations
never have placement data. Use `processes` for ground truth on usage, and
`--deployed-only` when you only want placeable rows (license review).

### `connections` — the configuration view

`componentId`, `name`, `version`, `status` as above, plus:

| Column | Meaning and how to read it |
|---|---|
| `environment` | One row per environment the connection is placed in. Empty when the tool cannot place the connection anywhere (see the note above). |
| `url` | The endpoint from that environment's extension values (first field whose id matches url/host/endpoint). Compare across rows of the same connection to spot wrong endpoints — PROD pointing at a DEV URL is what `drift` flags. Credentials embedded in the URL are shown as `[REDACTED]`. |
| `user` | The user/login extension field for that environment. |
| `credentials` | `[ENCRYPTED]` when the connection has a secret-bearing field (matching password/token/secret/key, or marked encrypted by Boomi). Values are **never** shown. Empty means no credential field is extension-managed for that environment. |

### `duplicates` (Use Case 2)

| Column | Meaning and how to read it |
|---|---|
| `type` | `TYPE A`: same name + same version more than once in one environment — a true copy-paste duplicate; delete the extras. `TYPE B`: differently-named connections sharing one endpoint — consolidation candidates (each may consume a license). `TYPE C`: identical config across environments — informational; check whether PROD should really equal DEV. |
| `severity` | `warning` = worth acting on; `info` = review when convenient. |
| `message` | The finding spelled out with names, versions, and environments. |
| `count` | How many components are involved in the finding. |

### `drift` (Use Case 3)

| Column | Meaning and how to read it |
|---|---|
| `kind` | `DRIFT`: the target env's value equals the source env's — an override was probably forgotten (the classic "PROD still points at DEV"). `MISSING`: set in the target env but empty in the source. `SCHEMA`: field type differs between envs. `ENCRYPTED`: a secret field exists — reported for awareness, value never shown. |
| `severity` | `error` = fix it; `warning` = verify it; `info` = be aware. |
| `connector` | The connection whose extensions drifted. |
| `field` | The extension field id in question. |
| `value_from` / `value_to` | The field's value in `--env-from` / `--env-to` (always `[ENCRYPTED]` for secret fields). |
| `message` | The finding spelled out. |

### `processes` (Use Case 4)

| Column | Meaning and how to read it |
|---|---|
| `connector` | The connection a process depends on, resolved through the full Boomi chain (process → operation → connection). |
| `process` | The process using it. Empty on unused-connector rows. |
| `environments` | Where the process is deployed. Always the full list, even with `--env` — the notes column needs it. |
| `notes` | Empty = nothing wrong with this pairing. `⚠️ in X but missing in Y` = environment gap: the process runs in X with no counterpart in Y (requires a resolvable `--env-from`/`--env-to` pair). `ℹ️ unused — no processes reference this connector` = no process anywhere uses this connection: a cleanup and license-reclaim candidate (only reported when the full process list was scanned). |

## Output formats and large accounts

```bash
--format table   # default: Rich table with color-coded severity
--format json    # machine-readable
--format csv     # use with --output report.csv (prompts before overwrite; --force to skip)
--max-records 200   # safety cap (0 = unlimited, the default)
--delay 0.5         # seconds between API calls for rate-limited accounts
--workers 8         # parallel reference lookups (processes/all commands)
```

Pagination is fully transparent — every command returns complete results
regardless of record count, with a progress bar for multi-page fetches and
status spinners while fetching and generating the report. Progress is
hidden when json/csv goes to stdout so piped output stays clean, but stays
on (it writes to stderr) when `--output` sends the report to a file — long
audits never run silently. Progress labels name what is
being fetched ("Fetching processes...", "Fetching connectors..."), so
commands that query the same API object more than once — `processes`
fetches both the process list and the connector list — show two distinct
bars, not one bar twice. 429/5xx responses are retried with exponential
backoff (1s → 2s → 4s).

Component queries are filtered server-side (connector types, current
versions, deleted excluded), so only connector metadata is downloaded — not
the whole account. Within one run, repeated identical queries are served
from an in-memory cache, which keeps `boomi-audit all` from re-fetching
shared data for every sub-audit.

## Token and secret handling

- `boomi-audit init` creates `~/.boomi-auditor/config.json` at permissions
  `600` from the moment it is written (directory `700`), and warns if the
  file later becomes readable by others. The file is still plaintext on
  disk — prefer environment variables or a secrets manager in CI, and never
  commit tokens to source control. Rotate Boomi API tokens regularly via the
  AtomSphere UI.
- API tokens are never logged or printed; they are masked in all output and
  error messages.
- Secret-bearing extension fields (by `encryptedValueSet` metadata or names
  matching `password`/`token`/`secret`/`key`) always render as `[ENCRYPTED]`
  — values are never shown.
- As defense in depth against secrets the heuristics cannot see, credentials
  embedded inside endpoint URLs (`https://user:pass@host`, or `?password=…`
  query parameters) are scrubbed to `[REDACTED]` before any URL value is
  displayed by `connections` or `drift`.
- These protections depend on Boomi returning the field metadata and payload
  shapes we have observed. This is alpha software: treat its output as
  sensitive and review it before sharing.

## Windows compatibility

All file handling uses `pathlib.Path`, so the tool works on Windows.
POSIX-style permission checks (chmod 600) are skipped on Windows — protect
the config file with NTFS permissions instead.

## Development

```bash
make install   # install with dev dependencies
make test      # pytest with coverage (fails below 80%)
make lint      # ruff
make build     # build sdist + wheel
```

See [CONTRIBUTING.md](CONTRIBUTING.md). All tests mock the Boomi API via
respx — no real API calls are made.

## License

MIT
