"""
Structural extraction via Tree-sitter AST.
No LLM. Deterministic. Fast.
Produces nodes (functions, classes, imports) and edges (calls, imports, inheritance).
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import tree_sitter_python as tspython
    import tree_sitter_javascript as tsjavascript
    import tree_sitter_typescript as tstypescript
    import tree_sitter_go as tsgo
    import tree_sitter_rust as tsrust
    import tree_sitter_java as tsjava
    from tree_sitter import Language, Parser
    TREE_SITTER_AVAILABLE = True
except ImportError:
    TREE_SITTER_AVAILABLE = False

# Map file extension → (language_module, language_name)
EXTENSION_MAP: dict[str, tuple[Any, str]] = {}

if TREE_SITTER_AVAILABLE:
    try:
        EXTENSION_MAP = {
            ".py":   (tspython, "python"),
            ".js":   (tsjavascript, "javascript"),
            ".jsx":  (tsjavascript, "javascript"),
            ".ts":   (tstypescript.language_typescript(), "typescript"),
            ".tsx":  (tstypescript.language_tsx(), "tsx"),
            ".go":   (tsgo, "go"),
            ".rs":   (tsrust, "rust"),
            ".java": (tsjava, "java"),
        }
    except Exception:
        pass

# Node types per language for functions, classes, imports, calls
_FUNCTION_TYPES = {
    "python":     {"function_definition", "async_function_definition"},
    "javascript": {"function_declaration", "arrow_function", "method_definition", "function_expression"},
    "typescript": {"function_declaration", "arrow_function", "method_definition", "function_expression"},
    "tsx":        {"function_declaration", "arrow_function", "method_definition", "function_expression"},
    "go":         {"function_declaration", "method_declaration"},
    "rust":       {"function_item"},
    "java":       {"method_declaration", "constructor_declaration"},
}

_CLASS_TYPES = {
    "python":     {"class_definition"},
    "javascript": {"class_declaration", "class_expression"},
    "typescript": {"class_declaration"},
    "tsx":        {"class_declaration"},
    "go":         {"type_spec"},
    "rust":       {"struct_item", "impl_item", "trait_item"},
    "java":       {"class_declaration", "interface_declaration"},
}

_IMPORT_TYPES = {
    "python":     {"import_statement", "import_from_statement"},
    "javascript": {"import_statement", "call_expression"},
    "typescript": {"import_statement"},
    "tsx":        {"import_statement"},
    "go":         {"import_declaration", "import_spec"},
    "rust":       {"use_declaration"},
    "java":       {"import_declaration"},
}

_CALL_TYPES = {
    "python":     {"call"},
    "javascript": {"call_expression"},
    "typescript": {"call_expression"},
    "tsx":        {"call_expression"},
    "go":         {"call_expression"},
    "rust":       {"call_expression", "macro_invocation"},
    "java":       {"method_invocation"},
}

_NAME_FIELDS = {"name", "identifier"}


@dataclass
class ExtractionResult:
    nodes: list[dict] = field(default_factory=list)
    edges: list[dict] = field(default_factory=list)
    file_hash: str = ""
    language: str = ""


def _sanitize_name(name: str) -> str:
    """Strip control chars, cap length, basic safety."""
    name = re.sub(r"[\x00-\x1f\x7f]", "", name)
    return name[:256]


def _node_text(node, src: bytes) -> str:
    return src[node.start_byte:node.end_byte].decode("utf-8", errors="replace").strip()


def _get_name(node, src: bytes) -> str:
    """Extract identifier name from an AST node."""
    for child in node.children:
        if child.type in _NAME_FIELDS or child.type == "identifier":
            return _sanitize_name(_node_text(child, src))
    # fallback: first word of node text
    text = _node_text(node, src)
    return _sanitize_name(text.split("(")[0].split("{")[0].strip()[:64])


def _get_docstring(node, src: bytes) -> str:
    """Extract docstring/leading comment for rationale."""
    for child in node.children:
        if child.type in ("block", "statement_block", "body"):
            for grandchild in child.children:
                if grandchild.type in ("expression_statement", "string"):
                    text = _node_text(grandchild, src)
                    if text.startswith(('"""', "'''", '"', "'")):
                        return text.strip("\"' \n")[:512]
    return ""


def _extract_rationale_comments(src: bytes) -> list[str]:
    """Pull NOTE/IMPORTANT/WHY/HACK inline comments."""
    text = src.decode("utf-8", errors="replace")
    pattern = re.compile(r"#\s*(NOTE|IMPORTANT|WHY|HACK|TODO)[:\s]+(.+)", re.IGNORECASE)
    return [f"{m.group(1)}: {m.group(2).strip()}" for m in pattern.finditer(text)]


def extract_file(path: Path) -> ExtractionResult:
    """
    Parse a single file and return its nodes + edges.
    Falls back to regex extraction if tree-sitter grammar is unavailable.
    """
    result = ExtractionResult()

    if not path.exists() or not path.is_file():
        return result

    try:
        src = path.read_bytes()
    except (OSError, PermissionError):
        return result

    result.file_hash = hashlib.sha256(src).hexdigest()
    ext = path.suffix.lower()

    lang_entry = EXTENSION_MAP.get(ext)
    if lang_entry is None or not TREE_SITTER_AVAILABLE:
        return _regex_fallback(path, src, result)

    lang_module, lang_name = lang_entry
    result.language = lang_name

    try:
        if hasattr(lang_module, "language"):
            lang = Language(lang_module.language())
        elif isinstance(lang_module, Language):
            lang = lang_module
        else:
            lang = Language(lang_module)
        parser = Parser(lang)
        tree = parser.parse(src)
    except Exception:
        return _regex_fallback(path, src, result)

    file_id = str(path)
    fn_types = _FUNCTION_TYPES.get(lang_name, set())
    cls_types = _CLASS_TYPES.get(lang_name, set())
    imp_types = _IMPORT_TYPES.get(lang_name, set())
    call_types = _CALL_TYPES.get(lang_name, set())

    defined_names: set[str] = set()

    def walk(node, parent_id: str | None = None):
        node_id = None

        if node.type in fn_types:
            name = _get_name(node, src)
            if name:
                node_id = f"{file_id}::{name}"
                defined_names.add(name)
                doc = _get_docstring(node, src)
                result.nodes.append({
                    "id": node_id,
                    "label": name,
                    "kind": "function",
                    "source_file": str(path),
                    "source_location": f"L{node.start_point[0]+1}",
                    "docstring": doc,
                    "confidence": "EXTRACTED",
                })
                if parent_id:
                    result.edges.append({
                        "source": parent_id,
                        "target": node_id,
                        "relation": "contains",
                        "confidence": "EXTRACTED",
                    })

        elif node.type in cls_types:
            name = _get_name(node, src)
            if name:
                node_id = f"{file_id}::{name}"
                defined_names.add(name)
                result.nodes.append({
                    "id": node_id,
                    "label": name,
                    "kind": "class",
                    "source_file": str(path),
                    "source_location": f"L{node.start_point[0]+1}",
                    "confidence": "EXTRACTED",
                })

        elif node.type in imp_types:
            imp_text = _node_text(node, src)
            f"{file_id}::import::{imp_text[:64]}"
            target_module = _parse_import_target(node, src, lang_name)
            if target_module:
                result.edges.append({
                    "source": file_id,
                    "target": target_module,
                    "relation": "imports",
                    "confidence": "EXTRACTED",
                    "label": imp_text[:128],
                })

        elif node.type in call_types:
            callee = _get_call_name(node, src)
            if callee and parent_id:
                result.edges.append({
                    "source": parent_id,
                    "target": callee,
                    "relation": "calls",
                    "confidence": "INFERRED",
                    "confidence_score": 0.85,
                })

        next_parent = node_id or parent_id
        for child in node.children:
            walk(child, next_parent)

    # Add file-level node
    result.nodes.append({
        "id": file_id,
        "label": path.name,
        "kind": "file",
        "source_file": str(path),
        "confidence": "EXTRACTED",
    })

    walk(tree.root_node, file_id)

    # Extract rationale comments
    for comment in _extract_rationale_comments(src):
        rat_id = f"{file_id}::rationale::{hashlib.md5(comment.encode()).hexdigest()[:8]}"
        result.nodes.append({
            "id": rat_id,
            "label": comment[:80],
            "kind": "rationale",
            "source_file": str(path),
            "confidence": "EXTRACTED",
        })
        result.edges.append({
            "source": file_id,
            "target": rat_id,
            "relation": "rationale_for",
            "confidence": "EXTRACTED",
        })

    return result


def _get_call_name(node, src: bytes) -> str:
    """Extract the function name from a call expression."""
    for child in node.children:
        if child.type == "identifier":
            return _sanitize_name(_node_text(child, src))
        if child.type in ("member_expression", "attribute"):
            return _sanitize_name(_node_text(child, src).split(".")[-1])
    return ""


def _parse_import_target(node, src: bytes, lang: str) -> str:
    """Extract the imported module path as a string."""
    text = _node_text(node, src)
    if lang == "python":
        m = re.search(r"from\s+([\w.]+)\s+import|import\s+([\w.]+)", text)
        if m:
            return m.group(1) or m.group(2)
    elif lang in ("javascript", "typescript", "tsx"):
        m = re.search(r'from\s+["\']([^"\']+)["\']|require\(["\']([^"\']+)["\']\)', text)
        if m:
            return m.group(1) or m.group(2)
    elif lang == "go":
        m = re.search(r'"([^"]+)"', text)
        if m:
            return m.group(1)
    elif lang == "rust":
        m = re.search(r"use\s+([\w:]+)", text)
        if m:
            return m.group(1)
    return ""


def _regex_fallback(path: Path, src: bytes, result: ExtractionResult) -> ExtractionResult:
    """Basic regex extraction when tree-sitter grammar unavailable."""
    text = src.decode("utf-8", errors="replace")
    file_id = str(path)

    result.nodes.append({
        "id": file_id,
        "label": path.name,
        "kind": "file",
        "source_file": str(path),
        "confidence": "EXTRACTED",
    })

    # Functions
    for m in re.finditer(r"(?:def|function|func|fn)\s+(\w+)\s*\(", text):
        name = _sanitize_name(m.group(1))
        node_id = f"{file_id}::{name}"
        lineno = text[:m.start()].count("\n") + 1
        result.nodes.append({
            "id": node_id,
            "label": name,
            "kind": "function",
            "source_file": str(path),
            "source_location": f"L{lineno}",
            "confidence": "EXTRACTED",
        })

    # Classes
    for m in re.finditer(r"(?:class|struct|interface)\s+(\w+)", text):
        name = _sanitize_name(m.group(1))
        node_id = f"{file_id}::{name}"
        lineno = text[:m.start()].count("\n") + 1
        result.nodes.append({
            "id": node_id,
            "label": name,
            "kind": "class",
            "source_file": str(path),
            "source_location": f"L{lineno}",
            "confidence": "EXTRACTED",
        })

    return result
