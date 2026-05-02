"""
contextmap CLI
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

console = Console()


def _get_store(repo_root: Path):
    from .store import GraphStore
    db_path = repo_root / ".contextmap-out" / "graph.db"
    return GraphStore(db_path)


@click.group()
@click.version_option()
def main():
    """contextmap — unified structural + semantic knowledge graph for AI coding assistants."""
    logging.basicConfig(level=logging.WARNING, format="%(message)s")


@main.command()
@click.option("--root", default=".", type=click.Path(), help="Repo root")
@click.option("--force", is_flag=True, help="Re-parse all files even if unchanged")
def build(root, force):
    """Build or rebuild the knowledge graph."""
    repo = Path(root).resolve()
    store = _get_store(repo)

    from .builder import build as do_build

    with console.status("[bold green]Building graph..."):
        stats = do_build(repo, store, force=force)

    console.print(f"[green]✓ Parsed {stats['parsed']} files, skipped {stats['skipped']}, {stats['errors']} errors ({stats['elapsed_ms']}ms)")

    from .context import build_context, token_estimate
    with console.status("[bold green]Generating CONTEXT.md..."):
        ctx_stats = build_context(store, repo, force=force)

    updated = [k for k, v in ctx_stats.items() if v == "updated"]
    cached = [k for k, v in ctx_stats.items() if v == "cached"]
    tokens = token_estimate(repo)
    console.print(f"[green]✓ CONTEXT.md ({tokens} tokens) — updated: {updated or 'none'}, cached: {cached or 'none'}")
    _print_stats(store)


@main.command()
@click.option("--root", default=".", type=click.Path(), help="Repo root")
def update(root):
    """Incrementally update — only re-parses changed files."""
    repo = Path(root).resolve()
    store = _get_store(repo)

    from .builder import update as do_update
    from .context import build_context, token_estimate

    with console.status("[bold green]Updating graph..."):
        stats = do_update(repo, store)

    console.print(f"[green]✓ Graph: {stats['parsed']} re-parsed, {stats['skipped']} skipped ({stats['elapsed_ms']}ms)")

    with console.status("[bold green]Patching CONTEXT.md..."):
        ctx_stats = build_context(store, repo)

    updated = [k for k, v in ctx_stats.items() if v == "updated"]
    tokens = token_estimate(repo)
    if updated:
        console.print(f"[green]✓ CONTEXT.md patched ({tokens} tokens) — sections updated: {updated}")
    else:
        console.print(f"[dim]✓ CONTEXT.md unchanged ({tokens} tokens) — all sections cached[/dim]")


@main.command()
@click.option("--root", default=".", type=click.Path())
def status(root):
    """Show graph statistics."""
    repo = Path(root).resolve()
    store = _get_store(repo)
    _print_stats(store)


@main.command()
@click.argument("question")
@click.option("--root", default=".", type=click.Path())
@click.option("--budget", default=2000, help="Approximate token budget for output")
def query(question, root, budget):
    """Search the graph by keyword or question."""
    repo = Path(root).resolve()
    store = _get_store(repo)
    results = store.search(question, limit=15)

    if not results:
        console.print("[yellow]No results found.")
        return

    table = Table(title=f"Results for: {question}")
    table.add_column("ID", style="dim", max_width=50)
    table.add_column("Label")
    table.add_column("Kind")
    table.add_column("File")
    table.add_column("Loc")

    for r in results[:20]:
        table.add_row(
            r["id"].split("::")[-1],
            r.get("label", ""),
            r.get("kind", ""),
            Path(r.get("source_file", "")).name,
            r.get("source_location", ""),
        )

    console.print(table)


@main.command()
@click.argument("node_id")
@click.option("--root", default=".", type=click.Path())
def explain(node_id, root):
    """Explain a node and its connections."""
    repo = Path(root).resolve()
    store = _get_store(repo)

    # Try fuzzy match first
    matches = store.search(node_id, limit=5)
    if not matches:
        console.print(f"[yellow]Node not found: {node_id}")
        return

    node = matches[0]
    neighbors = store.get_neighbors(node["id"])
    callers = store.get_callers(node["id"])

    console.print(f"\n[bold]{node.get('label', node['id'])}[/bold] ({node.get('kind', '?')})")
    console.print(f"File: {node.get('source_file', '')} {node.get('source_location', '')}")
    if node.get("docstring"):
        console.print(f"[dim]{node['docstring'][:200]}[/dim]")

    if callers:
        console.print("\n[cyan]Called by:[/cyan]")
        for c in callers[:8]:
            console.print(f"  • {c.get('label', c['id'])}")

    if neighbors:
        console.print("\n[cyan]Connects to:[/cyan]")
        for n in neighbors[:8]:
            console.print(f"  • {n.get('label', n['id'])} [{n.get('relation', '')}]")


@main.command()
@click.argument("source")
@click.argument("target")
@click.option("--root", default=".", type=click.Path())
def path(source, target, root):
    """Find shortest path between two nodes."""
    repo = Path(root).resolve()
    store = _get_store(repo)

    src_matches = store.search(source, limit=1)
    tgt_matches = store.search(target, limit=1)

    if not src_matches:
        console.print(f"[yellow]Not found: {source}")
        return
    if not tgt_matches:
        console.print(f"[yellow]Not found: {target}")
        return

    p = store.shortest_path(src_matches[0]["id"], tgt_matches[0]["id"])
    if not p:
        console.print("[yellow]No path found between those nodes.")
        return

    console.print(f"\nPath ({len(p)} hops):")
    for i, nid in enumerate(p):
        node = store.get_node(nid) or {"id": nid, "label": nid.split("::")[-1], "kind": "?"}
        prefix = "  → " if i > 0 else "  "
        console.print(f"{prefix}[bold]{node.get('label', nid)}[/bold] ({node.get('kind', '?')}) [{Path(node.get('source_file', '')).name}]")


@main.command()
@click.option("--root", default=".", type=click.Path())
@click.option("--force", is_flag=True, help="Rewrite all sections even if unchanged")
def context(root, force):
    """Regenerate CONTEXT.md (or patch only changed sections)."""
    repo = Path(root).resolve()
    store = _get_store(repo)
    from .context import build_context, token_estimate
    with console.status("Updating CONTEXT.md..."):
        stats = build_context(store, repo, force=force)
    updated = [k for k, v in stats.items() if v == "updated"]
    cached = [k for k, v in stats.items() if v == "cached"]
    tokens = token_estimate(repo)
    console.print(f"[green]✓ CONTEXT.md ({tokens} tokens)")
    console.print(f"  updated: {updated or 'none'}")
    console.print(f"  cached:  {cached or 'none'}")
    console.print("  kept:    notes (manual section never touched)")


@main.command()
@click.option("--root", default=".", type=click.Path())
def report(root):
    """Re-generate contextmap-out/GRAPH_REPORT.md (legacy)."""
    repo = Path(root).resolve()
    store = _get_store(repo)
    from .analysis import render_report
    render_report(store, repo / "contextmap-out")
    console.print("[green]✓ contextmap-out/GRAPH_REPORT.md updated")


@main.command()
@click.option("--root", default=".", type=click.Path())
@click.argument("path_arg", metavar="PATH", default=None, required=False)
def semantic(root, path_arg):
    """Run LLM semantic extraction (requires ANTHROPIC_API_KEY)."""
    import os
    if not os.environ.get("ANTHROPIC_API_KEY"):
        console.print("[red]ANTHROPIC_API_KEY not set. Export it and retry.")
        sys.exit(1)

    repo = Path(root).resolve()
    store = _get_store(repo)

    from .semantic import run_semantic_pass, extract_semantic
    from .analysis import render_report

    async def _run():
        if path_arg:
            p = Path(path_arg)
            with console.status(f"Extracting {p.name}..."):
                nodes, edges = await extract_semantic(p, store)
            if nodes:
                store.upsert_nodes(nodes)
            if edges:
                store.upsert_edges(edges)
            console.print(f"[green]✓ {len(nodes)} nodes, {len(edges)} edges extracted from {p.name}")
        else:
            with console.status("Running semantic pass on all docs/images..."):
                stats = await run_semantic_pass(repo, store)
            console.print(f"[green]✓ Processed {stats['processed']}, cached {stats['cached']}, errors {stats['errors']}")
            render_report(store, repo / "contextmap-out")

    asyncio.run(_run())


@main.command()
@click.option("--root", default=".", type=click.Path())
def watch(root):
    """Watch for file changes and auto-update the graph."""
    repo = Path(root).resolve()
    store = _get_store(repo)
    from .builder import watch as do_watch

    def on_change(path):
        console.print(f"[dim]Updated: {path.name}[/dim]")

    observer = do_watch(repo, store, callback=on_change)
    if not observer:
        return
    console.print(f"[green]Watching {repo} — Ctrl+C to stop")
    try:
        import time
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


@main.command()
@click.option("--root", default=".", type=click.Path())
@click.option("--format", "fmt", default="json", type=click.Choice(["json", "graphml"]))
def export(root, fmt):
    """Export graph to JSON or GraphML."""
    repo = Path(root).resolve()
    store = _get_store(repo)
    out_dir = repo / "contextmap-out"
    out_dir.mkdir(exist_ok=True)

    if fmt == "json":
        out = out_dir / "graph.json"
        store.export_json(out)
    else:
        out = out_dir / "graph.graphml"
        store.export_graphml(out)

    console.print(f"[green]✓ Exported to {out}")


@main.command()
@click.option("--root", default=".", type=click.Path())
@click.option("--platform", default=None, type=click.Choice(list({
    "claude-code", "cursor", "codex", "opencode", "gemini", "copilot", "aider", "windsurf", "continue"
})))
def install(root, platform):
    """Install contextmap for your AI coding tool (auto-detects if no platform given)."""
    from .installer import install as do_install
    repo = Path(root).resolve()
    installed = do_install(repo, platform)
    for p in installed:
        console.print(f"[green]✓ {p} configured")
    console.print("\nNext: run [bold]contextmap build[/bold] to build the graph.")


@main.command()
@click.option("--root", default=".", type=click.Path())
def uninstall(root):
    """Remove contextmap configuration files."""
    from .installer import uninstall as do_uninstall
    repo = Path(root).resolve()
    removed = do_uninstall(repo)
    for r in removed:
        console.print(f"[yellow]Removed: {r}")


@main.command()
@click.option("--root", default=".", type=click.Path())
def serve(root):
    """Start the MCP server (stdio transport) for use with Claude Code etc."""
    repo = Path(root).resolve()
    db_path = repo / ".contextmap-out" / "graph.db"
    from .server import run_server
    run_server(repo, db_path)


def _print_stats(store):
    s = store.stats()
    console.print(f"\n[bold]Graph:[/bold] {s.nodes} nodes · {s.edges} edges · {s.files} files")
    console.print(f"[bold]Semantic layer:[/bold] {'[green]built[/green]' if not s.structural_only else '[yellow]not built[/yellow] (run: contextmap semantic)'}")

    gods = store.god_nodes(top_n=5)
    if gods:
        names = ", ".join(
            g.get("label", "") or str(g.get("id", ""))[:30]
            for g in gods[:5]
            if g.get("source_file")  # skip builtins
        )
        if names:
            console.print(f"[bold]Top hubs:[/bold] {names}")
