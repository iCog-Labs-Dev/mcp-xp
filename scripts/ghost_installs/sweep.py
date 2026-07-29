"""Enumerate installed tool-shed repositories, flag ghost/broken ones, uninstall.

Reuses env vars and the same uninstall endpoint the runtime installer uses (see
app/GX_integration/workflows/worklfow_installer.py::_ensure_repository_installable).
Standalone — no FastAPI process required.

Galaxy 25.0 has no `repair_repository_revision` endpoint (removed upstream —
confirmed against the running instance's /openapi.json). The correct remedy
for a stuck ghost row is DELETE + fresh install. This script performs the
DELETE; a subsequent workflow submission will trigger the fresh install via
the runtime installer.

Env (from .env at repo root):
    GALAXY_URL       Galaxy base URL
    GALAXY_API_KEY   Admin API key

Usage:
    uv run python scripts/ghost_installs/sweep.py --list
    uv run python scripts/ghost_installs/sweep.py --uninstall
    uv run python scripts/ghost_installs/sweep.py --uninstall --verify
    uv run python scripts/ghost_installs/sweep.py --uninstall --verify --json
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from typing import Any

import httpx
from bioblend.galaxy import GalaxyInstance
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger("ghost_sweep")

UNHEALTHY_STATUSES = {"Error", "Uninstalled", "New"}
# DELETE /api/tool_shed_repositories/{id}  (remove_from_disk=true)
# See lib/galaxy/webapps/galaxy/api/tool_shed_repositories.py::uninstall_repository
UNINSTALL_ENDPOINT_TEMPLATE = "/api/tool_shed_repositories/{repo_id}"


def env_or_die(name: str) -> str:
    v = os.getenv(name)
    if not v:
        sys.exit(f"error: {name} is not set (check .env)")
    return v


def repo_id(r: dict[str, Any]) -> str:
    return f"{r.get('owner')}/{r.get('name')}@{r.get('changeset_revision')}"


def classify(repo: dict[str, Any]) -> str:
    if repo.get("deleted") or repo.get("uninstalled"):
        return "unhealthy"
    status = repo.get("status", "")
    if status in UNHEALTHY_STATUSES:
        return "unhealthy"
    if status == "Installed":
        return "healthy"
    return "unknown"


async def enumerate_repos(gi: GalaxyInstance) -> list[dict[str, Any]]:
    log.info("enumerating installed tool-shed repositories via gi.toolShed")
    return await asyncio.to_thread(gi.toolShed.get_repositories)


async def uninstall(
    client: httpx.AsyncClient,
    galaxy_url: str,
    admin_key: str,
    repo: dict[str, Any],
) -> bool:
    if not repo.get("id"):
        log.warning("cannot uninstall %s: repo has no 'id' field", repo_id(repo))
        return False
    endpoint = UNINSTALL_ENDPOINT_TEMPLATE.format(repo_id=repo["id"])
    try:
        resp = await client.delete(
            f"{galaxy_url}{endpoint}",
            headers={"x-api-key": admin_key},
            params={"remove_from_disk": "true"},
        )
        resp.raise_for_status()
        log.info("uninstall accepted: %s", repo_id(repo))
        return True
    except Exception as e:
        log.warning("uninstall failed for %s: %s", repo_id(repo), e)
        return False


async def reload_toolbox(gi: GalaxyInstance) -> None:
    await asyncio.to_thread(gi.config.reload_toolbox)


async def run() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--list", action="store_true", help="list repos and exit")
    parser.add_argument("--uninstall", action="store_true", help="DELETE unhealthy repos to clear stale DB rows")
    parser.add_argument("--verify", action="store_true", help="post-uninstall: re-enumerate and confirm targeted repos are gone")
    parser.add_argument("--concurrency", type=int, default=3, help="parallel uninstalls (default: 3)")
    parser.add_argument("--json", dest="as_json", action="store_true", help="emit JSON summary to stdout")
    args = parser.parse_args()

    if not args.list and not args.uninstall:
        parser.error("choose --list or --uninstall (or both)")

    galaxy_url = env_or_die("GALAXY_URL").rstrip("/")
    admin_key = env_or_die("GALAXY_API_KEY")

    gi = GalaxyInstance(url=galaxy_url, key=admin_key)

    repos = await enumerate_repos(gi)
    buckets: dict[str, list[dict[str, Any]]] = {"healthy": [], "unhealthy": [], "unknown": []}
    for r in repos:
        buckets[classify(r)].append(r)

    log.info(
        "found %d repos — healthy=%d unhealthy=%d unknown=%d",
        len(repos),
        len(buckets["healthy"]),
        len(buckets["unhealthy"]),
        len(buckets["unknown"]),
    )

    for r in buckets["unhealthy"]:
        log.info(
            "  unhealthy: %s status=%s deleted=%s uninstalled=%s",
            repo_id(r),
            r.get("status"),
            r.get("deleted"),
            r.get("uninstalled"),
        )
    for r in buckets["unknown"]:
        log.info("  unknown : %s status=%s", repo_id(r), r.get("status"))

    summary: dict[str, Any] = {
        "total": len(repos),
        "healthy": len(buckets["healthy"]),
        "unhealthy": len(buckets["unhealthy"]),
        "unknown": len(buckets["unknown"]),
        "unhealthy_repos": [repo_id(r) for r in buckets["unhealthy"]],
        "uninstalled": [],
        "uninstall_failed": [],
        "still_present": [],
    }

    if not args.uninstall:
        if args.as_json:
            print(json.dumps(summary, indent=2))
        return 0

    sem = asyncio.Semaphore(args.concurrency)

    async def uninstall_one(client: httpx.AsyncClient, r: dict[str, Any]) -> None:
        async with sem:
            ok = await uninstall(client, galaxy_url, admin_key, r)
            (summary["uninstalled"] if ok else summary["uninstall_failed"]).append(repo_id(r))

    async with httpx.AsyncClient(timeout=60.0, verify=False) as client:
        await asyncio.gather(*[uninstall_one(client, r) for r in buckets["unhealthy"]])

    if args.verify and summary["uninstalled"]:
        log.info("reloading toolbox before verification")
        await reload_toolbox(gi)
        await asyncio.sleep(2)

        post_repos = await enumerate_repos(gi)
        # A repo is considered "gone" if it either disappears from the list, or its
        # deleted/uninstalled flags are now true.
        still_active_ids = {
            r.get("id")
            for r in post_repos
            if r.get("id") and not (r.get("deleted") or r.get("uninstalled"))
        }
        targeted_by_id = {r["id"]: r for r in buckets["unhealthy"] if r.get("id")}
        for repo_uid, r in targeted_by_id.items():
            if repo_uid in still_active_ids:
                summary["still_present"].append(repo_id(r))

    log.info(
        "done — uninstalled=%d uninstall_failed=%d still_present=%d",
        len(summary["uninstalled"]),
        len(summary["uninstall_failed"]),
        len(summary["still_present"]),
    )

    if args.as_json:
        print(json.dumps(summary, indent=2))

    exit_code = 0 if not summary["uninstall_failed"] and not summary["still_present"] else 1
    return exit_code


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
