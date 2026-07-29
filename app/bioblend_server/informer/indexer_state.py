"""Persistent last-indexed timestamps for BackgroundIndexer collections.

Prevents the "re-scrape everything on every container restart" behavior
that happens when there's no memory of prior indexing runs.

Persistence relies on the enclosing directory being bind-mounted from
the host. See docker-compose.yml volume `./state:/app/state`. Without
the mount the file still works inside the container filesystem, but it
gets wiped on rebuild — degrading gracefully to the old always-reindex
behavior instead of failing loudly.
"""
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile


DEFAULT_STATE_PATH = os.environ.get(
    "INDEXER_STATE_PATH", "/app/state/last_indexed.json"
)


class IndexerState:
    """Reads/writes a JSON map of {collection_name: ISO8601 timestamp}."""

    def __init__(self, path: str = DEFAULT_STATE_PATH):
        self.path = Path(path)
        self.log = logging.getLogger(self.__class__.__name__)
        self._ensure_dir()

    def _ensure_dir(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            self.log.warning(
                f"Could not create indexer state directory {self.path.parent}: {e}"
            )

    def _read(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        try:
            with self.path.open("r") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError) as e:
            self.log.warning(f"Failed to read indexer state at {self.path}: {e}")
            return {}

    def _write(self, data: dict[str, str]):
        try:
            with NamedTemporaryFile(
                "w",
                dir=self.path.parent,
                delete=False,
                prefix=".tmp-",
                suffix=".json",
            ) as tmp:
                json.dump(data, tmp, indent=2, sort_keys=True)
                tmp_path = Path(tmp.name)
            tmp_path.replace(self.path)
        except OSError as e:
            self.log.warning(f"Failed to write indexer state at {self.path}: {e}")

    def get_last_indexed(self, collection: str) -> datetime | None:
        raw = self._read().get(collection)
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            self.log.warning(f"Malformed timestamp for {collection!r}: {raw!r}")
            return None

    def mark_indexed(self, collection: str):
        data = self._read()
        data[collection] = datetime.now(timezone.utc).isoformat()
        self._write(data)

    def is_fresh(self, collection: str, lifespan_seconds: int) -> bool:
        ts = self.get_last_indexed(collection)
        if ts is None:
            return False
        age = (datetime.now(timezone.utc) - ts).total_seconds()
        return age < lifespan_seconds
