# ctxmap skill

ctxmap builds a structural + semantic knowledge graph of your codebase.

## Trigger

When the user types `/ctxmap`, invoke this skill.

## Commands

- `/ctxmap build` — full structural build (no API key needed)
- `/ctxmap update` — incremental update (only changed files, fast)
- `/ctxmap semantic` — LLM semantic extraction (requires ANTHROPIC_API_KEY)
- `/ctxmap query <question>` — search the graph
- `/ctxmap explain <symbol>` — explain a node and its connections
- `/ctxmap path <A> <B>` — shortest path between two nodes
- `/ctxmap status` — graph stats
- `/ctxmap report` — regenerate GRAPH_REPORT.md
- `/ctxmap watch` — auto-update on file changes

## Always-on behavior

If `ctxmap-out/GRAPH_REPORT.md` exists in the repo:
1. Read it before answering any architecture, code review, or onboarding question
2. It contains: god nodes (top hubs), surprising connections, suggested questions

## MCP tools available

- `get_minimal_context` — call this first on any code task
- `get_blast_radius(files)` — change impact analysis for code review
- `get_review_context(files)` — full review: blast radius + hubs + surprises
- `query_graph(question)` — keyword search + neighbors
- `get_node(id)` — full node details with callers and callees
- `get_god_nodes()` — most-connected architectural hubs
- `get_architecture_overview()` — high-level structural summary

## Workflow examples

**Code review:**
1. `get_minimal_context()`
2. `get_review_context(["changed_file.py"])`
3. For high-risk nodes: `get_node(id)`

**Onboarding:**
1. `get_architecture_overview()`
2. `get_god_nodes()`
3. `query_graph("entry point")`

**Debugging:**
1. `query_graph("symptom or error")`
2. `get_path(entry_point, failing_node)`
3. `get_surprising_connections()` for unexpected coupling
