# Ghost-install sweep

Standalone script that enumerates installed tool-shed repositories on Galaxy,
flags ghost/broken ones, and repairs them via the same endpoint the runtime
installer uses (`_ensure_repository_installable` in
`app/GX_integration/workflows/worklfow_installer.py`).

## What counts as unhealthy

A repository is treated as unhealthy when:

- `status` is `Error`, `Uninstalled`, or `New`, **or**
- `deleted` or `uninstalled` is truthy

Post-repair verification also treats a repo as unhealthy if:

- Its declared `tool_ids` fail to load in the current toolbox, or
- The loaded tool's `changeset_revision` does not match the repo revision

## Why repair instead of uninstall + reinstall

`install_repository_revision` silently no-ops when a stale "Installed" DB row
exists — the row must be cleared or the fetch forced. The repair endpoint
force-refetches files and re-resolves conda deps without touching dependents,
which is exactly the ghost-install failure mode. See the design note in the
runtime installer for the full rationale.

## Env

Uses the same `.env` as the app — no new setup:

- `GALAXY_URL` — Galaxy base URL
- `GALAXY_API_KEY` — admin API key

## Run

```bash
# list only, no changes
uv run python scripts/ghost_installs/sweep.py --list

# repair unhealthy repos
uv run python scripts/ghost_installs/sweep.py --repair

# repair + reload toolbox + verify every repaired repo's tools resolve
uv run python scripts/ghost_installs/sweep.py --repair --verify

# machine-readable summary
uv run python scripts/ghost_installs/sweep.py --repair --verify --json
```

Flags:

- `--concurrency N` — parallel repairs (default 3). Higher values can cause
  Galaxy to serialize or contend on repo mutations.
- `--verify-wait S` — max seconds to poll for tools after repair (default 60).
  Conda resolution can take time; the poll reloads the toolbox each round.

Exit code is non-zero if any repair or verification failed.

## Safety

- Repair does not cascade-uninstall other repos' dependencies.
- Running while user workflows are executing is generally safe (repair is
  additive), but a fresh install triggered mid-invocation can still race
  Galaxy's own install path. Prefer running when the instance is quiet.
- The script talks TLS with `verify=False` to match the app's runtime
  behavior on self-signed staging certs.
