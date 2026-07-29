import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.bioblend_server.informer.indexer_state import IndexerState


@pytest.fixture
def state_path(tmp_path: Path) -> Path:
    return tmp_path / "last_indexed.json"


@pytest.fixture
def state(state_path: Path) -> IndexerState:
    return IndexerState(path=str(state_path))


class TestIndexerState:
    def test_missing_file_returns_none(self, state):
        assert state.get_last_indexed("generic_galaxy_tool") is None

    def test_missing_file_is_never_fresh(self, state):
        assert state.is_fresh("generic_galaxy_tool", lifespan_seconds=60) is False

    def test_mark_and_read_roundtrip(self, state):
        state.mark_indexed("generic_galaxy_tool")
        ts = state.get_last_indexed("generic_galaxy_tool")
        assert ts is not None
        assert ts.tzinfo is not None  # timezone-aware

    def test_fresh_within_lifespan(self, state):
        state.mark_indexed("generic_galaxy_tool")
        assert state.is_fresh("generic_galaxy_tool", lifespan_seconds=60) is True

    def test_stale_past_lifespan(self, state, state_path):
        old_ts = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
        state_path.write_text(json.dumps({"generic_galaxy_tool": old_ts}))
        assert state.is_fresh("generic_galaxy_tool", lifespan_seconds=7 * 86400) is False

    def test_two_collections_tracked_independently(self, state):
        state.mark_indexed("generic_galaxy_tool")
        assert state.get_last_indexed("generic_galaxy_workflow") is None
        state.mark_indexed("generic_galaxy_workflow")
        assert state.get_last_indexed("generic_galaxy_workflow") is not None
        assert state.get_last_indexed("generic_galaxy_tool") is not None

    def test_malformed_json_treated_as_empty(self, state, state_path, caplog):
        state_path.write_text("this is not json")
        with caplog.at_level(logging.WARNING):
            assert state.get_last_indexed("generic_galaxy_tool") is None
        assert "Failed to read indexer state" in caplog.text

    def test_malformed_timestamp_treated_as_absent(self, state, state_path, caplog):
        state_path.write_text(json.dumps({"generic_galaxy_tool": "not-a-timestamp"}))
        with caplog.at_level(logging.WARNING):
            assert state.get_last_indexed("generic_galaxy_tool") is None
        assert "Malformed timestamp" in caplog.text

    def test_non_dict_json_treated_as_empty(self, state, state_path):
        state_path.write_text(json.dumps(["not", "a", "dict"]))
        assert state.get_last_indexed("generic_galaxy_tool") is None

    def test_write_is_atomic(self, state, state_path):
        state.mark_indexed("generic_galaxy_tool")
        # After a write, no leftover .tmp-* files hang around next to the state file
        siblings = list(state_path.parent.glob(".tmp-*"))
        assert siblings == []
