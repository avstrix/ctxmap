"""Tests for CONTEXT.md generation."""
import json
from pathlib import Path
import pytest
from contextmap.store import GraphStore
from contextmap.context import (
    build_context, token_estimate,
    _read_sections, _write_context,
    _section_input_hash, SECTION_MARKERS,
    MANUAL_MARKER_START,
)


@pytest.fixture
def populated_store(tmp_path):
    store = GraphStore(tmp_path / "graph.db")
    store.upsert_nodes([
        {"id": "src/auth.py", "label": "auth.py", "kind": "file", "source_file": "src/auth.py"},
        {"id": "src/auth.py::login", "label": "login", "kind": "function", "source_file": "src/auth.py", "source_location": "L10"},
        {"id": "src/auth.py::logout", "label": "logout", "kind": "function", "source_file": "src/auth.py", "source_location": "L25"},
        {"id": "src/db.py", "label": "db.py", "kind": "file", "source_file": "src/db.py"},
        {"id": "src/db.py::UserRepo", "label": "UserRepo", "kind": "class", "source_file": "src/db.py", "source_location": "L5"},
        {"id": "src/db.py::get_user", "label": "get_user", "kind": "function", "source_file": "src/db.py"},
        {"id": "tests/test_auth.py::test_login", "label": "test_login", "kind": "function", "source_file": "tests/test_auth.py"},
        {"id": "src/auth.py::rationale1", "label": "NOTE: uses JWT for stateless auth", "kind": "rationale", "source_file": "src/auth.py"},
    ])
    store.upsert_edges([
        {"source": "src/auth.py", "target": ".db", "relation": "imports"},
        {"source": "src/auth.py::login", "target": "src/db.py::get_user", "relation": "calls"},
    ])
    store.set_file_hash("src/auth.py", "abc123")
    store.set_file_hash("src/db.py", "def456")
    return store


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "README.md").write_text("# myapp\nA test application for auth and users.")
    (tmp_path / ".ctxmap").mkdir()
    return tmp_path


def test_build_context_creates_file(populated_store, repo):
    stats = build_context(populated_store, repo)
    assert (repo / "CONTEXT.md").exists()
    assert stats["project"] == "updated"
    assert stats["architecture"] == "updated"
    assert stats["notes"] in ("created", "kept")


def test_context_contains_all_sections(populated_store, repo):
    build_context(populated_store, repo)
    text = (repo / "CONTEXT.md").read_text()
    for section, (start, end) in SECTION_MARKERS.items():
        assert start in text, f"Missing section marker: {start}"
        assert end in text, f"Missing section end marker: {end}"


def test_context_token_estimate(populated_store, repo):
    build_context(populated_store, repo)
    tokens = token_estimate(repo)
    assert tokens > 50
    assert tokens < 3000  # should always be compact


def test_incremental_nothing_changes(populated_store, repo):
    build_context(populated_store, repo, force=True)
    stats2 = build_context(populated_store, repo)
    # All managed sections should be cached
    assert stats2["project"] == "cached"
    assert stats2["architecture"] == "cached"
    assert stats2["notes"] == "kept"


def test_incremental_architecture_updates_on_new_file(populated_store, repo):
    build_context(populated_store, repo, force=True)
    # Add a new file node
    populated_store.upsert_nodes([
        {"id": "src/payments.py", "label": "payments.py", "kind": "file", "source_file": "src/payments.py"},
        {"id": "src/payments.py::charge", "label": "charge", "kind": "function", "source_file": "src/payments.py"},
    ])
    stats2 = build_context(populated_store, repo)
    assert stats2["architecture"] == "updated"
    assert stats2["hot_files"] == "updated"
    # recent unaffected (no hash changes)
    assert stats2["recent"] == "cached"


def test_notes_section_never_overwritten(populated_store, repo):
    build_context(populated_store, repo)
    # Manually edit notes section
    text = (repo / "CONTEXT.md").read_text()
    text = text.replace(
        "<!-- Add your own notes here.",
        "My custom note that should never be lost."
    )
    (repo / "CONTEXT.md").write_text(text)
    # Run update again
    build_context(populated_store, repo)
    new_text = (repo / "CONTEXT.md").read_text()
    assert "My custom note that should never be lost." in new_text


def test_force_rewrites_all_sections(populated_store, repo):
    build_context(populated_store, repo)
    stats = build_context(populated_store, repo, force=True)
    assert stats["project"] == "updated"
    assert stats["architecture"] == "updated"
    assert stats["conventions"] == "updated"


def test_architecture_section_has_module_map(populated_store, repo):
    build_context(populated_store, repo)
    sections = _read_sections(repo / "CONTEXT.md")
    arch = sections.get("architecture", "")
    assert "auth.py" in arch
    assert "db.py" in arch


def test_architecture_shows_internal_deps(populated_store, repo):
    build_context(populated_store, repo)
    sections = _read_sections(repo / "CONTEXT.md")
    arch = sections.get("architecture", "")
    # auth.py imports .db so should show dependency
    assert "db" in arch


def test_architecture_shows_classes(populated_store, repo):
    build_context(populated_store, repo)
    sections = _read_sections(repo / "CONTEXT.md")
    arch = sections.get("architecture", "")
    assert "UserRepo" in arch


def test_conventions_detects_test_pattern(populated_store, repo):
    build_context(populated_store, repo)
    sections = _read_sections(repo / "CONTEXT.md")
    conv = sections.get("conventions", "")
    assert "test" in conv.lower()


def test_conventions_shows_rationale(populated_store, repo):
    build_context(populated_store, repo)
    sections = _read_sections(repo / "CONTEXT.md")
    conv = sections.get("conventions", "")
    assert "JWT" in conv or "stateless" in conv


def test_hot_files_excludes_tests(populated_store, repo):
    build_context(populated_store, repo)
    sections = _read_sections(repo / "CONTEXT.md")
    hot = sections.get("hot_files", "")
    assert "test_auth" not in hot


def test_project_reads_readme(populated_store, repo):
    build_context(populated_store, repo)
    sections = _read_sections(repo / "CONTEXT.md")
    proj = sections.get("project", "")
    assert "myapp" in proj or "test application" in proj


def test_section_hashes_saved(populated_store, repo):
    build_context(populated_store, repo)
    meta = repo / ".ctxmap" / "section_hashes.json"
    assert meta.exists()
    hashes = json.loads(meta.read_text())
    assert "project" in hashes
    assert "architecture" in hashes


def test_read_write_roundtrip(tmp_path):
    sections = {
        "project": "test project content",
        "architecture": "module map here",
        "conventions": "patterns here",
        "hot_files": "important files",
        "recent": "recent changes",
        "notes": "my manual note",
    }
    path = tmp_path / "CONTEXT.md"
    _write_context(path, sections, "myrepo")
    recovered = _read_sections(path)
    for key, val in sections.items():
        assert recovered[key].strip() == val.strip(), f"Section '{key}' roundtrip failed"


def test_token_estimate_zero_when_no_file(tmp_path):
    assert token_estimate(tmp_path) == 0
