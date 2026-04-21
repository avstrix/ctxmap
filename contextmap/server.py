"""
MCP server for contextmap.
Exposes structural + semantic graph tools to Claude Code and other AI assistants.
Start with: contextmap serve
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    from fastmcp import FastMCP
    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False


def create_server(repo_root: Path, db_path: Path):
    if not MCP_AVAILABLE:
        raise ImportError("fastmcp not installed: pip install fastmcp")

    from .store import GraphStore
    from .builder import build, update
    from .analysis import analyze, render_report, detect_changes

    mcp = FastMCP("contextmap")
    store = GraphStore(db_path)

    # ─── Build / Update ──────────────────────────────────────

    @mcp.tool()
    def build_graph(force: bool = False) -> str:
        """Build or rebuild the knowledge graph for the current repo."""
        stats = build(repo_root, store, force=force)
        report = render_report(store, repo_root / "contextmap-out")
        return json.dumps({"stats": stats, "report_written": True})

    @mcp.tool()
    def update_graph() -> str:
        """Incrementally update graph — only re-parses changed files. Fast."""
        stats = update(repo_root, store)
        return json.dumps(stats)

    # ─── Context / Review ────────────────────────────────────

    @mcp.tool()
    def get_minimal_context() -> str:
        """
        Ultra-compact graph summary (~100 tokens).
        Call this first before any code review or architecture question.
        """
        s = store.stats()
        gods = store.god_nodes(top_n=3)
        god_names = [g.get("label", g["id"]) for g in gods]
        return json.dumps({
            "nodes": s.nodes,
            "edges": s.edges,
            "files": s.files,
            "top_hubs": god_names,
            "semantic": not s.structural_only,
            "tip": "Use get_blast_radius for change impact, query_graph for exploration.",
        })

    @mcp.tool()
    def get_blast_radius(changed_files: list[str], depth: int = 3) -> str:
        """
        Given a list of changed file paths, return every affected function/class/test.
        Use before code review to understand change scope.
        """
        result = detect_changes(store, changed_files)
        return json.dumps(result)

    @mcp.tool()
    def get_review_context(changed_files: list[str]) -> str:
        """
        Full review context: blast radius + god nodes + surprising connections.
        Token-optimised — returns only what's relevant to the changed files.
        """
        blast = detect_changes(store, changed_files)
        gods = store.god_nodes(top_n=5)
        # Filter to gods touched by the change
        affected_ids: set[str] = set()
        for c in blast["changes"]:
            affected_ids.update(c["affected_nodes"])

        touched_gods = [g for g in gods if g["id"] in affected_ids]

        return json.dumps({
            "blast_radius": blast,
            "touched_hubs": touched_gods,
            "surprises": store.surprising_connections(top_n=3),
        })

    # ─── Exploration ─────────────────────────────────────────

    @mcp.tool()
    def query_graph(question: str, budget: int = 1000) -> str:
        """
        Search the graph by keyword and return matching nodes + their neighbors.
        budget: max tokens of output (approximate).
        """
        results = store.search(question, limit=15)
        enriched = []
        token_count = 0

        for node in results:
            neighbors = store.get_neighbors(node["id"])
            callers = store.get_callers(node["id"])
            entry = {
                "node": {k: v for k, v in node.items() if k not in ("metadata",)},
                "called_by": [{"id": c["id"], "label": c.get("label", c["id"])} for c in callers[:5]],
                "calls": [{"id": n["id"], "label": n.get("label", n["id"]), "relation": n.get("relation")} for n in neighbors[:5]],
            }
            enriched.append(entry)
            token_count += len(json.dumps(entry)) // 4  # rough token estimate
            if token_count > budget:
                break

        return json.dumps({"results": enriched, "total_matches": len(results)})

    @mcp.tool()
    def get_node(node_id: str) -> str:
        """Get full details of a specific node by ID."""
        node = store.get_node(node_id)
        if not node:
            return json.dumps({"error": f"Node not found: {node_id}"})
        neighbors = store.get_neighbors(node_id)
        callers = store.get_callers(node_id)
        return json.dumps({
            "node": node,
            "neighbors": neighbors[:20],
            "callers": callers[:20],
        })

    @mcp.tool()
    def get_path(source_id: str, target_id: str) -> str:
        """Find shortest path between two nodes in the graph."""
        path = store.shortest_path(source_id, target_id)
        if not path:
            return json.dumps({"error": "No path found", "source": source_id, "target": target_id})

        # Hydrate with node data
        nodes = [store.get_node(nid) or {"id": nid} for nid in path]
        return json.dumps({"path": nodes, "length": len(path)})

    @mcp.tool()
    def get_god_nodes(top_n: int = 10) -> str:
        """Return the most-connected nodes — structural hubs and conceptual centers."""
        gods = store.god_nodes(top_n=top_n)
        return json.dumps({"god_nodes": gods})

    @mcp.tool()
    def get_surprising_connections(top_n: int = 10) -> str:
        """
        Return unexpected cross-file or cross-community connections.
        High score = more surprising. INFERRED edges that span files rank highest.
        """
        surprises = store.surprising_connections(top_n=top_n)
        return json.dumps({"surprising_connections": surprises})

    @mcp.tool()
    def get_architecture_overview() -> str:
        """
        High-level architectural summary: hub nodes, node kinds, isolated components.
        Good starting point for onboarding or architecture review.
        """
        from .analysis import analyze
        data = analyze(store)
        return json.dumps({
            "architecture": data["architecture"],
            "stats": data["stats"],
            "suggested_questions": data["suggested_questions"],
        })

    @mcp.tool()
    def get_graph_stats() -> str:
        """Return graph size, health, and semantic layer status."""
        s = store.stats()
        return json.dumps({
            "nodes": s.nodes,
            "edges": s.edges,
            "files": s.files,
            "semantic_built": not s.structural_only,
        })

    # ─── Semantic ─────────────────────────────────────────────

    @mcp.tool()
    async def run_semantic(path: str | None = None) -> str:
        """
        Run LLM semantic extraction on docs/images/PDFs.
        If path given, runs on that file only. Otherwise runs on whole repo.
        Requires ANTHROPIC_API_KEY and pip install contextmap[semantic].
        """
        from .semantic import extract_semantic, run_semantic_pass

        if path:
            p = Path(path)
            nodes, edges = await extract_semantic(p, store)
            if nodes:
                store.upsert_nodes(nodes)
            if edges:
                store.upsert_edges(edges)
            return json.dumps({"nodes": len(nodes), "edges": len(edges)})
        else:
            stats = await run_semantic_pass(repo_root, store)
            render_report(store, repo_root / "contextmap-out")
            return json.dumps(stats)

    # ─── Export ──────────────────────────────────────────────

    @mcp.tool()
    def export_graph(format: str = "json") -> str:
        """
        Export the graph. format: json | graphml
        Writes to contextmap-out/graph.{format}
        """
        out_dir = repo_root / "contextmap-out"
        out_dir.mkdir(exist_ok=True)

        if format == "json":
            out = out_dir / "graph.json"
            store.export_json(out)
        elif format == "graphml":
            out = out_dir / "graph.graphml"
            store.export_graphml(out)
        else:
            return json.dumps({"error": f"Unknown format: {format}"})

        return json.dumps({"exported": str(out)})

    # ─── MCP Prompts ─────────────────────────────────────────

    @mcp.prompt()
    def review_changes(changed_files: str) -> str:
        """Code review workflow for a set of changed files."""
        files = [f.strip() for f in changed_files.split(",")]
        return f"""You are reviewing code changes. Follow this workflow:

1. Call get_minimal_context() to orient yourself.
2. Call get_review_context({json.dumps(files)}) to get blast radius and risk scores.
3. For any high-risk files (risk_score > 0.7), call get_node() on key affected symbols.
4. Check if god nodes are in the blast radius — changes to hubs need extra scrutiny.
5. Report: what changed, what's affected, what tests are missing, what risks exist.

Changed files: {changed_files}"""

    @mcp.prompt()
    def onboard_developer() -> str:
        """Codebase onboarding workflow."""
        return """You are onboarding a developer to this codebase. Follow this workflow:

1. Call get_architecture_overview() for the high-level structure.
2. Call get_god_nodes() to understand the core concepts.
3. Call get_surprising_connections() to flag non-obvious coupling.
4. Call query_graph("entry point") or query_graph("main") to find where things start.
5. Read contextmap-out/GRAPH_REPORT.md if it exists.

Produce: a plain-language guide covering what the codebase does, how it's organized,
the key files to understand first, and any architectural surprises."""

    @mcp.prompt()
    def debug_issue(symptom: str) -> str:
        """Debugging workflow starting from a symptom."""
        return f"""You are debugging an issue. Symptom: {symptom}

1. Call query_graph("{symptom}") to find related nodes.
2. For each match, call get_node() to see its callers and dependencies.
3. Use get_path() to trace the call chain from entry point to the failing code.
4. Check get_surprising_connections() — unexpected coupling often causes bugs.
5. Report: likely root cause, call chain, suggested fix, tests to add."""

    return mcp


def run_server(repo_root: Path, db_path: Path):
    server = create_server(repo_root, db_path)
    server.run(transport="stdio")
