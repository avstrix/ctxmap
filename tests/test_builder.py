"""Tests for incremental builder."""
import time
from pathlib import Path
import pytest
from ctxmap.builder import build, update, collect_files
from ctxmap.store import GraphStore


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("def main(): pass\n")
    (tmp_path / "src" / "util.py").write_text("def helper(): main()\n")
    (tmp_path / "README.md").write_text("# My Project\nThis is a test project.")
    return tmp_path


@pytest.fixture
def store(tmp_path):
    return GraphStore(tmp_path / "graph.db")


def test_build_indexes_files(repo, store):
    stats = build(repo, store)
    assert stats["parsed"] >= 2  # at least main.py and util.py
    s = store.stats()
    assert s.nodes > 0
    assert s.files > 0


def test_incremental_skips_unchanged(repo, store):
    build(repo, store)
    stats2 = build(repo, store)
    assert stats2["skipped"] >= stats2["parsed"]  # most should be skipped


def test_incremental_reparses_changed(repo, store):
    build(repo, store)
    # Modify a file
    (repo / "src" / "main.py").write_text("def main(): pass\ndef new_func(): pass\n")
    stats2 = update(repo, store)
    assert stats2["parsed"] >= 1


def test_collect_files_ignores_node_modules(repo):
    nm = repo / "node_modules" / "lib"
    nm.mkdir(parents=True)
    (nm / "index.js").write_text("module.exports = {};")
    files = collect_files(repo)
    assert not any("node_modules" in str(f) for f in files)


def test_collect_files_respects_contextmapignore(repo):
    (repo / ".contextmapignore").write_text("src/\n")
    files = collect_files(repo)
    assert not any("src" in str(f) for f in files)


def test_build_force(repo, store):
    build(repo, store)
    stats2 = build(repo, store, force=True)
    assert stats2["parsed"] >= 2  # all re-parsed


def test_build_handles_broken_file(repo, store):
    (repo / "src" / "broken.py").write_bytes(b"\xff\xfe invalid utf8 \x00")
    stats = build(repo, store)
    # Should not raise, may have errors counted
    assert "errors" in stats
