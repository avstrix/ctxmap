# Architecture

contextmap is a Python library + CLI + optional MCP server.

## Core idea

`context.py` is the heart of the tool. Everything else (parser, store, builder) exists to feed it good data cheaply.

## Pipeline

```
collect_files()
  → extract_file()           # Tree-sitter AST, SHA-256 hash — no LLM
  → store.upsert()           # SQLite WAL
  → build_context()          # section-level diff → patch CONTEXT.md
      → _section_input_hash()    # has this section's inputs changed?
      → _build_*_section()       # only called if hash changed
      → _write_context()         # atomic write
```

## Module responsibilities

| Module | Entry point | What it does |
|---|---|---|
| `context.py` | `build_context(store, root)` | Generates/patches CONTEXT.md section by section |
| `parser.py` | `extract_file(path)` | Tree-sitter AST → nodes + edges |
| `store.py` | `GraphStore(db_path)` | SQLite r/w + lazy NetworkX for graph queries |
| `builder.py` | `build(root, store)` | Scans files, diffs hashes, calls extract_file |
| `semantic.py` | `extract_semantic(path, store)` | LLM extraction for docs/images, hash-cached |
| `analysis.py` | `analyze(store)` | God nodes, blast radius, surprising connections |
| `server.py` | `create_server(root, db)` | FastMCP with optional deep-query tools |
| `installer.py` | `install(root, platform)` | Writes CLAUDE.md section + git hook |
| `cli.py` | `main()` | Click CLI wiring |

## CONTEXT.md section update model

Each section tracks a hash of its source inputs in `.ctxmap/section_hashes.json`.

```
section          input hash sources
─────────────────────────────────────────────────────
project          file count + README.md hash
architecture     function counts per file + import edges
conventions      all function names (pattern detection)
hot_files        total node + edge count
recent           last 10 file hashes by update time
notes            never hashed — never rewritten
```

On `contextmap update`:
1. Compute current input hash for each section
2. Compare to stored hash
3. Only call the section builder if hash differs
4. Write CONTEXT.md atomically
5. Save new hashes

Typical commit changing 3 files: `recent` section rebuilds (~200 tokens). All others cached.

## Node schema

```python
{
    "id": "src/auth.py::LoginHandler",   # filepath::symbol
    "label": "LoginHandler",
    "kind": "class|function|file|rationale",
    "source_file": "src/auth.py",
    "source_location": "L42",
    "docstring": "...",
    "confidence": "EXTRACTED|INFERRED|AMBIGUOUS",
}
```

## Section builders

Each `_build_*_section(store, root) -> str` function is independent and testable.

- `_build_project_section` — reads stats + README first paragraph
- `_build_architecture_section` — module map from function counts + internal imports + classes
- `_build_conventions_section` — naming patterns from function names + rationale nodes
- `_build_hot_files_section` — top file nodes by graph degree (builtins filtered)
- `_build_recent_section` — `git log --since=7 days ago`, fallback to hash timestamps

## Security

- No `eval()`, `exec()`, `pickle`, `yaml.unsafe_load()`
- SQL: always parameterized queries (`?`)
- `_sanitize_name()` strips control chars, caps at 256 chars
- API keys only from environment variables

## Adding a language

1. Add tree-sitter grammar to `pyproject.toml`
2. Add to `EXTENSION_MAP` in `parser.py`
3. Add node types to `_FUNCTION_TYPES`, `_CLASS_TYPES`, `_IMPORT_TYPES`, `_CALL_TYPES`
4. Add fixture to `tests/fixtures/`, test to `tests/test_parser.py`

## Testing

```bash
pytest tests/ -q   # 49 tests, all pure unit tests
```
