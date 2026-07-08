# Ghost-install sweep

Standalone script that enumerates installed tool-shed repositories on Galaxy,
flags ghost/broken ones, and clears them by DELETE'ing the stale rows. A
subsequent workflow submission will trigger a fresh install via the runtime
installer (`_ensure_repository_installable` in
`app/GX_integration/workflows/worklfow_installer.py`).

## What counts as unhealthy

A repository is treated as unhealthy when:

- `status` is `Error`, `Uninstalled`, or `New`, **or**
- `deleted` or `uninstalled` is truthy

Post-uninstall verification (with `--verify`) treats a repo as *still present* if
the repo row is still active (not `deleted` and not `uninstalled`) after
re-enumerating.

## Why uninstall instead of repair

Galaxy 25.0 has no `repair_repository_revision` endpoint (removed upstream —
verified against the running instance's `/openapi.json`). `install_repository_revision`
silently no-ops when any DB row exists for the target repo, so a fresh install
alone can't clear a ghost.

The remaining remedy is `DELETE /api/tool_shed_repositories/{id}` (`?remove_from_disk=true`),
which removes the DB row, files on disk, and conda envs for the repo. A
subsequent install then runs cleanly against the empty state.

## Env

Uses the same `.env` as the app — no new setup:

- `GALAXY_URL` — Galaxy base URL
- `GALAXY_API_KEY` — admin API key

## Run

```bash
# list only, no changes
uv run python scripts/ghost_installs/sweep.py --list

# uninstall unhealthy repos
uv run python scripts/ghost_installs/sweep.py --uninstall

# uninstall + reload toolbox + re-enumerate to confirm ghosts are gone
uv run python scripts/ghost_installs/sweep.py --uninstall --verify

# machine-readable summary
uv run python scripts/ghost_installs/sweep.py --uninstall --verify --json
```

Flags:

- `--concurrency N` — parallel uninstalls (default 3). Higher values can cause
  Galaxy to serialize or contend on repo mutations.

Exit code is non-zero if any uninstall or verification failed.

## Safety

- `remove_from_disk=true` removes files and conda envs. Shared dependencies may
  cascade off; if another healthy repo relied on them, they will be reinstalled
  on next need. That's an accepted cost of clearing the ghost.
- Running while user workflows are executing is generally safe, but a fresh
  install triggered mid-invocation can still race Galaxy's own install path.
  Prefer running when the instance is quiet.
- The script talks TLS with `verify=False` to match the app's runtime behavior
  on self-signed staging certs.
