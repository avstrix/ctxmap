"""Tests for analysis module."""
from pathlib import Path
import pytest
from contextmap.store import GraphStore
from contextmap.analysis import analyze, render_report, detect_changes


@pytest.fixture
def populated_store(tmp_path):
    store = GraphStore(tmp_path / "graph.db")
    store.upsert_nodes([
        {"id": "hub.py::hub_func", "label": "hub_func", "kind": "function", "source_file": "hub.py"},
        {"id": "a.py::func_a", "label": "func_a", "kind": "function", "source_file": "a.py"},
        {"id": "b.py::func_b", "label": "func_b", "kind": "function", "source_file": "b.py"},
        {"id": "c.py::func_c", "label": "func_c", "kind": "function", "source_file": "c.py"},
        {"id": "test_a.py::test_hub", "label": "test_hub", "kind": "function", "source_file": "test_a.py"},
    ])
    store.upsert_edges([
        {"source": "a.py::func_a", "target": "hub.py::hub_func", "relation": "calls", "confidence": "EXTRACTED"},
        {"source": "b.py::func_b", "target": "hub.py::hub_func", "relation": "calls", "confidence": "EXTRACTED"},
        {"source": "c.py::func_c", "target": "hub.py::hub_func", "relation": "calls", "confidence": "INFERRED"},
        {"source": "test_a.py::test_hub", "target": "hub.py::hub_func", "relation": "calls", "confidence": "EXTRACTED"},
    ])
    return store


def test_analyze_returns_structure(populated_store):
    data = analyze(populated_store)
    assert "god_nodes" in data
    assert "surprising_connections" in data
    assert "suggested_questions" in data
    assert "architecture" in data
    assert "stats" in data


def test_god_nodes_finds_hub(populated_store):
    data = analyze(populated_store)
    top = data["god_nodes"][0]
    assert top["label"] == "hub_func"


def test_suggested_questions_nonempty(populated_store):
    data = analyze(populated_store)
    assert len(data["suggested_questions"]) >= 1


def test_render_report(populated_store, tmp_path):
    out_dir = tmp_path / "out"
    report = render_report(populated_store, out_dir)
    assert "GRAPH_REPORT" in report or "contextmap" in report
    assert (out_dir / "GRAPH_REPORT.md").exists()


def test_detect_changes_blast_radius(populated_store):
    result = detect_changes(populated_store, ["hub.py"])
    # hub.py affects things that call hub_func
    assert len(result["changes"]) >= 1
    change = result["changes"][0]
    assert "risk_score" in change
    assert "affected_count" in change


def test_detect_changes_identifies_tests(populated_store):
    result = detect_changes(populated_store, ["hub.py"])
    change = next((c for c in result["changes"] if c["file"] == "hub.py"), None)
    assert change is not None
    assert change["has_test_coverage"] is True  # test_hub calls hub_func


def test_surprising_connections_cross_file(populated_store):
    # Add an INFERRED cross-file edge
    populated_store.upsert_edges([{
        "source": "a.py::func_a",
        "target": "c.py::func_c",
        "relation": "semantically_similar_to",
        "confidence": "INFERRED",
        "confidence_score": 0.7,
    }])
    surprises = populated_store.surprising_connections(top_n=5)
    assert len(surprises) >= 1
    assert surprises[0]["score"] > 0
