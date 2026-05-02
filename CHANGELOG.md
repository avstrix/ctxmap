# Changelog

## 0.2.0 — 2026-04-21

### New
- `context.py` — section-level diff update model for CONTEXT.md
- `CONTEXT.md` replaces `GRAPH_REPORT.md` as primary output
- `ctxmap context` CLI command
- Per-section hash tracking in `.ctxmap/section_hashes.json`
- `## Notes` locked section — never overwritten by ctxmap
- 17 new tests for context generation (49 total)

### Changed
- `ctxmap build` and `ctxmap update` now generate CONTEXT.md automatically
- CLAUDE.md installer now references CONTEXT.md instead of GRAPH_REPORT.md
- `_print_stats` filters builtins from hub display

### Fixed
- God nodes no longer polluted by Python builtins (Path, get, str)
- Duplicate entries in architecture section
- Repo name blank when path resolved from relative

## 0.1.0 — 2026-04-21

Initial release. Structural extraction, SQLite store, incremental build, MCP server, CLI.
