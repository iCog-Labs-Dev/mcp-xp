"""Enumerate installed tool-shed repositories, flag ghost/broken ones, repair.

Reuses env vars and the repair endpoint the runtime installer uses (see
app/GX_integration/workflows/worklfow_installer.py::_ensure_repository_installable).
Standalone — no FastAPI process required.

Env (from .env at repo root):
    GALAXY_URL       Galaxy base URL
    GALAXY_API_KEY   Admin API key

Usage:
    uv run python scripts/ghost_installs/sweep.py --list
    uv run python scripts/ghost_installs/sweep.py --repair
    uv run python scripts/ghost_installs/sweep.py --repair --verify
    uv run python scripts/ghost_installs/sweep.py --repair --verify --json
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from typing import Any

import httpx
from bioblend.galaxy import GalaxyInstance
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger("ghost_sweep")

UNHEALTHY_STATUSES = {"Error", "Uninstalled", "New"}
# Galaxy's repair route requires the repo id in the path:
#   POST /api/tool_shed_repositories/{id}/repair_repository_revision
# See https://galaxyproject.org/toolshed/api/
REPAIR_ENDPOINT_TEMPLATE = "/api/tool_shed_repositories/{repo_id}/repair_repository_revision"


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


async def repair(
    client: httpx.AsyncClient,
    galaxy_url: str,
    admin_key: str,
    repo: dict[str, Any],
) -> bool:
    if not repo.get("id"):
        log.warning("cannot repair %s: repo has no 'id' field", repo_id(repo))
        return False
    payload = {
        "tool_shed_url": f"https://{repo['tool_shed']}",
        "name": repo["name"],
        "owner": repo["owner"],
        "changeset_revision": repo["changeset_revision"],
    }
    endpoint = REPAIR_ENDPOINT_TEMPLATE.format(repo_id=repo["id"])
    try:
        resp = await client.post(
            f"{galaxy_url}{endpoint}",
            headers={"x-api-key": admin_key},
            json=payload,
        )
        resp.raise_for_status()
        log.info("repair accepted: %s", repo_id(repo))
        return True
    except Exception as e:
        log.warning("repair failed for %s: %s", repo_id(repo), e)
        return False


async def reload_toolbox(gi: GalaxyInstance) -> None:
    await asyncio.to_thread(gi.config.reload_toolbox)


async def verify_repo_tools(gi: GalaxyInstance, repo: dict[str, Any]) -> bool:
    """Best-effort verification: fetch repo detail; every listed tool must resolve.

    A same-name/different-revision case is still counted as failure — matches the
    runtime installer's post-install check.
    """
    try:
        detail = await asyncio.to_thread(gi.toolShed.show_repository, repo["id"])
    except Exception as e:
        log.debug("could not show_repository(%s): %s", repo["id"], e)
        return False

    tool_ids = detail.get("tool_ids") or []
    if not tool_ids:
        return detail.get("status") == "Installed"

    for tid in tool_ids:
        try:
            tool = await asyncio.to_thread(gi.tools.show_tool, tid)
        except Exception:
            return False
        if not tool:
            return False
        tool_repo = tool.get("tool_shed_repository") or {}
        if tool_repo.get("changeset_revision") != repo.get("changeset_revision"):
            return False
    return True


async def run() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--list", action="store_true", help="list repos and exit")
    parser.add_argument("--repair", action="store_true", help="attempt repair on unhealthy repos")
    parser.add_argument("--verify", action="store_true", help="post-repair: reload toolbox and verify tools")
    parser.add_argument("--concurrency", type=int, default=3, help="parallel repairs (default: 3)")
    parser.add_argument("--verify-wait", type=int, default=60, help="max seconds to wait for tools after repair (default: 60)")
    parser.add_argument("--json", dest="as_json", action="store_true", help="emit JSON summary to stdout")
    args = parser.parse_args()

    if not args.list and not args.repair:
        parser.error("choose --list or --repair (or both)")

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
        "repaired": [],
        "repair_failed": [],
        "verified": [],
        "verify_failed": [],
    }

    if not args.repair:
        if args.as_json:
            print(json.dumps(summary, indent=2))
        return 0

    sem = asyncio.Semaphore(args.concurrency)

    async def repair_one(client: httpx.AsyncClient, r: dict[str, Any]) -> None:
        async with sem:
            ok = await repair(client, galaxy_url, admin_key, r)
            (summary["repaired"] if ok else summary["repair_failed"]).append(repo_id(r))

    async with httpx.AsyncClient(timeout=60.0, verify=False) as client:
        await asyncio.gather(*[repair_one(client, r) for r in buckets["unhealthy"]])

    if args.verify and summary["repaired"]:
        log.info("reloading toolbox before verification")
        await reload_toolbox(gi)
        await asyncio.sleep(5)

        by_id = {repo_id(r): r for r in buckets["unhealthy"]}
        pending = set(summary["repaired"])
        deadline = time.monotonic() + args.verify_wait

        while pending and time.monotonic() < deadline:
            for rid in list(pending):
                if await verify_repo_tools(gi, by_id[rid]):
                    summary["verified"].append(rid)
                    pending.remove(rid)
            if pending:
                await asyncio.sleep(10)
                await reload_toolbox(gi)

        summary["verify_failed"] = sorted(pending)

    log.info(
        "done — repaired=%d repair_failed=%d verified=%d verify_failed=%d",
        len(summary["repaired"]),
        len(summary["repair_failed"]),
        len(summary["verified"]),
        len(summary["verify_failed"]),
    )

    if args.as_json:
        print(json.dumps(summary, indent=2))

    exit_code = 0 if not summary["repair_failed"] and not summary["verify_failed"] else 1
    return exit_code


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
