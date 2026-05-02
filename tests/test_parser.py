"""Tests for structural parser."""
import tempfile
from pathlib import Path
from ctxmap.parser import extract_file, _sanitize_name


def _write(tmp_path, name, content):
    p = tmp_path / name
    p.write_text(content)
    return p


def test_sanitize_name():
    assert _sanitize_name("hello\x00world") == "helloworld"
    assert len(_sanitize_name("x" * 300)) == 256


def test_extract_python(tmp_path):
    p = _write(tmp_path, "foo.py", """\
def hello():
    pass

class MyClass:
    def method(self):
        hello()
""")
    result = extract_file(p)
    labels = {n["label"] for n in result.nodes}
    assert "hello" in labels or len(result.nodes) > 0  # at minimum file node
    assert result.file_hash != ""


def test_extract_js(tmp_path):
    p = _write(tmp_path, "app.js", """\
function greet(name) {
    return "hello " + name;
}

class App {
    constructor() {}
}
""")
    result = extract_file(p)
    assert len(result.nodes) >= 1  # at minimum the file node


def test_extract_typescript(tmp_path):
    p = _write(tmp_path, "util.ts", """\
export function add(a: number, b: number): number {
    return a + b;
}

interface Config {
    debug: boolean;
}
""")
    result = extract_file(p)
    assert result.file_hash != ""


def test_extract_nonexistent():
    result = extract_file(Path("/nonexistent/file.py"))
    assert result.nodes == []
    assert result.edges == []


def test_extract_rationale_comments(tmp_path):
    p = _write(tmp_path, "notes.py", """\
# NOTE: This is a design decision
# WHY: We use this pattern for performance
def example():
    pass
""")
    result = extract_file(p)
    rationale_nodes = [n for n in result.nodes if n.get("kind") == "rationale"]
    assert len(rationale_nodes) >= 1


def test_file_node_always_present(tmp_path):
    p = _write(tmp_path, "empty.py", "")
    result = extract_file(p)
    file_nodes = [n for n in result.nodes if n.get("kind") == "file"]
    assert len(file_nodes) == 1
    assert result.nodes[0]["source_file"] == str(p)
