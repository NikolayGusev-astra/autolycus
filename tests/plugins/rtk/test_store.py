"""Tests for plugins/rtk/store.py — disk persistence layer."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from plugins.rtk import store


@pytest.fixture
def cache_dir() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        yield tmp


# ---------------------------------------------------------------------------
# save / load basic
# ---------------------------------------------------------------------------


class TestSaveLoad:
    def test_save_and_load(self, cache_dir: str):
        data = "Hello, world! " * 100
        pid = store.save(data, cache_dir=cache_dir)
        assert isinstance(pid, str) and len(pid) > 10
        loaded = store.load(pid, cache_dir=cache_dir)
        assert loaded == data

    def test_load_nonexistent(self, cache_dir: str):
        data = store.load("no-such-uuid", cache_dir=cache_dir)
        assert data is None

    def test_uuid_format(self, cache_dir: str):
        pid = store.save("test", cache_dir=cache_dir)
        # UUID v4 format: 8-4-4-4-12 hex chars
        parts = pid.split("-")
        assert len(parts) == 5
        assert all(len(p) in (4, 8, 12) for p in parts)

    def test_persist_id_is_unique(self, cache_dir: str):
        pids = set()
        for _ in range(100):
            pid = store.save("x" * 1000, cache_dir=cache_dir)
            pids.add(pid)
        assert len(pids) == 100

    def test_multiple_saves_same_session(self, cache_dir: str):
        pids = [store.save(f"data-{i}", cache_dir=cache_dir) for i in range(5)]
        for i, pid in enumerate(pids):
            assert store.load(pid, cache_dir=cache_dir) == f"data-{i}"


# ---------------------------------------------------------------------------
# Edge cases: empty, unicode, binary-unsafe strings
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_string(self, cache_dir: str):
        pid = store.save("", cache_dir=cache_dir)
        loaded = store.load(pid, cache_dir=cache_dir)
        assert loaded == ""

    def test_unicode(self, cache_dir: str):
        data = "Привет мир 🌍 π ≈ 3.14\n日本語\n한국어"
        pid = store.save(data, cache_dir=cache_dir)
        assert store.load(pid, cache_dir=cache_dir) == data

    def test_newlines_and_tabs(self, cache_dir: str):
        data = "line1\nline2\n\tindented\nlast"
        pid = store.save(data, cache_dir=cache_dir)
        assert store.load(pid, cache_dir=cache_dir) == data

    def test_very_long_string(self, cache_dir: str):
        data = "A" * 100_000
        pid = store.save(data, cache_dir=cache_dir)
        loaded = store.load(pid, cache_dir=cache_dir)
        assert len(loaded) == 100_000
        assert loaded == data


# ---------------------------------------------------------------------------
# resolve_path
# ---------------------------------------------------------------------------


class TestResolvePath:
    def test_resolve_found(self, cache_dir: str):
        pid = store.save("test data", cache_dir=cache_dir)
        path = store.resolve_path(pid, cache_dir=cache_dir)
        assert path is not None
        assert path.endswith(f"{pid}.txt")
        assert os.path.exists(path)

    def test_resolve_not_found(self, cache_dir: str):
        path = store.resolve_path("no-such-uuid", cache_dir=cache_dir)
        assert path is None


# ---------------------------------------------------------------------------
# cleanup
# ---------------------------------------------------------------------------


class TestCleanup:
    def test_removes_old_files(self, cache_dir: str):
        # Save a file, then manually set its mtime to 31 days ago
        pid = store.save("old data", cache_dir=cache_dir)
        path = Path(cache_dir) / f"{pid}.txt"
        old_mtime = 100_000_000  # ~1973, definitely old
        os.utime(path, (old_mtime, old_mtime))
        removed = store.cleanup(max_age_days=1, cache_dir=cache_dir)
        assert removed == 1
        assert not path.exists()

    def test_keeps_recent_files(self, cache_dir: str):
        pid = store.save("fresh data", cache_dir=cache_dir)
        removed = store.cleanup(max_age_days=30, cache_dir=cache_dir)
        assert removed == 0
        assert store.load(pid, cache_dir=cache_dir) == "fresh data"

    def test_empty_cache_dir(self, cache_dir: str):
        removed = store.cleanup(max_age_days=1, cache_dir=cache_dir)
        assert removed == 0

    def test_only_txt_files_in_dir(self, cache_dir: str):
        # Non-txt files should be ignored
        Path(cache_dir, "README.md").write_text("readme")
        store.save("data", cache_dir=cache_dir)
        removed = store.cleanup(max_age_days=0, cache_dir=cache_dir)
        # Only the .txt file gets cleaned, not README.md
        assert removed == 1
        assert Path(cache_dir, "README.md").exists()


# ---------------------------------------------------------------------------
# cache_dir creation
# ---------------------------------------------------------------------------


class TestCacheDirCreation:
    def test_nonexistent_dir_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            nonexistent = os.path.join(tmp, "new", "subdir", "cache")
            assert not os.path.exists(nonexistent)
            pid = store.save("test", cache_dir=nonexistent)
            assert os.path.exists(nonexistent)
            assert store.load(pid, cache_dir=nonexistent) == "test"

    def test_default_dir(self):
        # Ensure default dir ~/.autolycus/rtk-cache is created
        from pathlib import Path as P
        default = P("~/.autolycus/rtk-cache").expanduser()
        pid = store.save("default-test")
        assert default.exists()
        assert store.load(pid) == "default-test"
        # Cleanup
        (default / f"{pid}.txt").unlink()
