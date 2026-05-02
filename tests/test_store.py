"""Tests for GraphStore."""
import tempfile
from pathlib import Path
import pytest
from ctxmap.store import GraphStore


@pytest.fixture
def store(tmp_path):
    return GraphStore(tmp_path / "graph.db")


def test_upsert_and_get_node(store):
    store.upsert_nodes([{"id": "a::func", "label": "func", "kind": "function", "source_file": "a.py"}])
    node = store.get_node("a::func")
    assert node is not None
    assert node["label"] == "func"


def test_upsert_idempotent(store):
    node = {"id": "a::func", "label": "func", "kind": "function", "source_file": "a.py"}
    store.upsert_nodes([node, node])
    store.upsert_nodes([node])
    assert store.get_node("a::func") is not None


def test_upsert_edges(store):
    store.upsert_nodes([
        {"id": "a::foo", "label": "foo", "kind": "function", "source_file": "a.py"},
        {"id": "b::bar", "label": "bar", "kind": "function", "source_file": "b.py"},
    ])
    store.upsert_edges([{"source": "a::foo", "target": "b::bar", "relation": "calls"}])
    neighbors = store.get_neighbors("a::foo")
    assert any(n["id"] == "b::bar" for n in neighbors)


def test_blast_radius(store):
    store.upsert_nodes([
        {"id": "a.py", "label": "a.py", "kind": "file", "source_file": "a.py"},
        {"id": "a.py::func", "label": "func", "kind": "function", "source_file": "a.py"},
        {"id": "b.py::caller", "label": "caller", "kind": "function", "source_file": "b.py"},
    ])
    store.upsert_edges([{"source": "b.py::caller", "target": "a.py::func", "relation": "calls"}])
    blast = store.blast_radius(["a.py"])
    affected = blast["a.py"]
    assert "a.py" in affected or "a.py::func" in affected


def test_stats(store):
    store.upsert_nodes([{"id": "x", "label": "x", "kind": "function", "source_file": "x.py"}])
    s = store.stats()
    assert s.nodes >= 1


def test_file_hash(store):
    store.set_file_hash("a.py", "abc123")
    assert store.get_file_hash("a.py") == "abc123"
    assert store.get_file_hash("missing.py") is None


def test_delete_file_nodes(store):
    store.upsert_nodes([
        {"id": "a.py", "label": "a.py", "kind": "file", "source_file": "a.py"},
        {"id": "a.py::func", "label": "func", "kind": "function", "source_file": "a.py"},
    ])
    store.delete_file_nodes("a.py")
    assert store.get_node("a.py::func") is None


def test_search(store):
    store.upsert_nodes([
        {"id": "a::authenticate", "label": "authenticate", "kind": "function", "source_file": "a.py"},
        {"id": "b::logout", "label": "logout", "kind": "function", "source_file": "b.py"},
    ])
    results = store.search("auth")
    assert any(r["label"] == "authenticate" for r in results)


def test_god_nodes(store):
    # Node with many connections should rank highest
    store.upsert_nodes([
        {"id": "hub", "label": "hub", "kind": "function", "source_file": "hub.py"},
        {"id": "a", "label": "a", "kind": "function", "source_file": "a.py"},
        {"id": "b", "label": "b", "kind": "function", "source_file": "b.py"},
        {"id": "c", "label": "c", "kind": "function", "source_file": "c.py"},
    ])
    store.upsert_edges([
        {"source": "a", "target": "hub", "relation": "calls"},
        {"source": "b", "target": "hub", "relation": "calls"},
        {"source": "c", "target": "hub", "relation": "calls"},
    ])
    gods = store.god_nodes(top_n=1)
    assert gods[0]["id"] == "hub"


def test_export_json(store, tmp_path):
    store.upsert_nodes([{"id": "a", "label": "a", "kind": "function", "source_file": "a.py"}])
    out = tmp_path / "graph.json"
    store.export_json(out)
    import json
    data = json.loads(out.read_text())
    assert "nodes" in data
    assert "edges" in data


def test_semantic_cache(store):
    nodes = [{"id": "x::concept", "label": "concept", "kind": "concept", "source_file": "doc.md"}]
    edges = [{"source": "x::concept", "target": "y::other", "relation": "related_to", "confidence": "INFERRED"}]
    store.save_semantic_cache("abc123", nodes, edges)
    cached = store.get_semantic_cache("abc123")
    assert cached is not None
    assert cached[0][0]["label"] == "concept"
