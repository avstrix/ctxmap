"""
Platform installer for contextmap.
Auto-detects which AI coding tools are installed and writes correct config for each.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


PLATFORMS = {
    "claude-code": "Claude Code",
    "cursor": "Cursor",
    "codex": "OpenAI Codex",
    "opencode": "OpenCode",
    "gemini": "Gemini CLI",
    "copilot": "GitHub Copilot CLI",
    "aider": "Aider",
    "windsurf": "Windsurf",
    "continue": "Continue",
}

SKILL_TEXT = """\
# contextmap skill

contextmap is a unified knowledge graph tool. It combines structural AST analysis
(fast, deterministic) with optional LLM semantic extraction.

## Trigger

When the user types `/contextmap`, invoke this skill.

## Commands

- `/contextmap build` — full build of the current directory
- `/contextmap update` — incremental update (only changed files)
- `/contextmap semantic` — run LLM semantic extraction on docs/images
- `/contextmap query <question>` — search the graph
- `/contextmap explain <symbol>` — explain a node and its connections
- `/contextmap path <A> <B>` — shortest path between two nodes
- `/contextmap report` — re-generate GRAPH_REPORT.md
- `/contextmap watch` — auto-update on file changes

## Workflow

1. On first use in a repo: `/contextmap build`
2. After changes: `/contextmap update`
3. For deeper understanding: `/contextmap semantic` (requires ANTHROPIC_API_KEY)
4. Then query: `/contextmap query "auth flow"` or `/contextmap explain MyClass`

## Always-on

If contextmap-out/GRAPH_REPORT.md exists, read it before answering architecture
or code review questions — it gives you god nodes, surprising connections, and
suggested questions.
"""

CLAUDE_MD_SECTION = """\

## contextmap context

contextmap maintains `CONTEXT.md` in this repo — a compressed map of the codebase
updated automatically on every git commit.

**Always read `CONTEXT.md` at the start of every session before doing anything.**

It contains: project overview, module architecture, coding conventions,
hot files to understand first, and recent changes.

For deep queries: MCP tools `get_blast_radius`, `query_graph`, `get_god_nodes` are available.
Run `contextmap serve` to start the MCP server.
"""

MCP_CONFIG = {
    "mcpServers": {
        "contextmap": {
            "type": "stdio",
            "command": "contextmap",
            "args": ["serve"],
        }
    }
}


def _contextmap_command() -> str:
    """Find the installed contextmap executable path."""
    cmd = shutil.which("contextmap")
    if cmd:
        return cmd
    # Try uvx
    uvx = shutil.which("uvx")
    if uvx:
        return f"uvx contextmap"
    return sys.executable + " -m contextmap"


def install_claude_code(repo_root: Path):
    """Write .mcp.json + CLAUDE.md section + PreToolUse hook."""
    # .mcp.json
    mcp_path = repo_root / ".mcp.json"
    config = {}
    if mcp_path.exists():
        try:
            config = json.loads(mcp_path.read_text())
        except json.JSONDecodeError:
            pass
    config.setdefault("mcpServers", {})["contextmap"] = {
        "type": "stdio",
        "command": _contextmap_command(),
        "args": ["serve"],
    }
    mcp_path.write_text(json.dumps(config, indent=2))

    # CLAUDE.md
    claude_md = repo_root / "CLAUDE.md"
    existing = claude_md.read_text() if claude_md.exists() else ""
    if "contextmap" not in existing:
        claude_md.write_text(existing + CLAUDE_MD_SECTION)

    # Skill file
    skill_dir = Path.home() / ".claude" / "skills" / "contextmap"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(SKILL_TEXT)

    print("✓ Claude Code: .mcp.json, CLAUDE.md, skill installed")


def install_cursor(repo_root: Path):
    rules_dir = repo_root / ".cursor" / "rules"
    rules_dir.mkdir(parents=True, exist_ok=True)
    rule = f"---\nalwaysApply: true\n---\n{CLAUDE_MD_SECTION}"
    (rules_dir / "contextmap.mdc").write_text(rule)
    print("✓ Cursor: .cursor/rules/contextmap.mdc installed")


def install_codex(repo_root: Path):
    agents_md = repo_root / "AGENTS.md"
    existing = agents_md.read_text() if agents_md.exists() else ""
    if "contextmap" not in existing:
        agents_md.write_text(existing + CLAUDE_MD_SECTION)

    skill_dir = Path.home() / ".codex" / "skills" / "contextmap"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(SKILL_TEXT)
    print("✓ Codex: AGENTS.md, skill installed")


def install_generic_agents_md(repo_root: Path, platform: str):
    """Fallback for platforms that use AGENTS.md."""
    agents_md = repo_root / "AGENTS.md"
    existing = agents_md.read_text() if agents_md.exists() else ""
    if "contextmap" not in existing:
        agents_md.write_text(existing + CLAUDE_MD_SECTION)
    print(f"✓ {PLATFORMS.get(platform, platform)}: AGENTS.md updated")


def install(repo_root: Path, platform: str | None = None):
    """
    Auto-detect or target a specific platform and install contextmap config.
    """
    installed = []

    if platform == "claude-code" or (platform is None and _has_claude_code()):
        install_claude_code(repo_root)
        installed.append("Claude Code")

    if platform == "cursor" or (platform is None and _has_cursor(repo_root)):
        install_cursor(repo_root)
        installed.append("Cursor")

    if platform == "codex" or (platform is None and _has_codex()):
        install_codex(repo_root)
        installed.append("Codex")

    if platform in ("opencode", "gemini", "copilot", "aider", "windsurf", "continue") or (
        platform is None and _has_agents_md_platform()
    ):
        p = platform or "generic"
        install_generic_agents_md(repo_root, p)
        installed.append(PLATFORMS.get(p, p))

    if not installed:
        # Fallback: install Claude Code config since that's most common
        install_claude_code(repo_root)
        installed.append("Claude Code (default)")

    return installed


def _has_claude_code() -> bool:
    return bool(shutil.which("claude")) or (Path.home() / ".claude").exists()


def _has_cursor(repo_root: Path) -> bool:
    return (repo_root / ".cursor").exists() or bool(shutil.which("cursor"))


def _has_codex() -> bool:
    return bool(shutil.which("codex")) or (Path.home() / ".codex").exists()


def _has_agents_md_platform() -> bool:
    return any(shutil.which(cmd) for cmd in ("aider", "opencode", "gh"))


def uninstall(repo_root: Path, platform: str | None = None):
    """Remove contextmap configuration."""
    removed = []

    mcp_path = repo_root / ".mcp.json"
    if mcp_path.exists():
        try:
            config = json.loads(mcp_path.read_text())
            config.get("mcpServers", {}).pop("contextmap", None)
            mcp_path.write_text(json.dumps(config, indent=2))
            removed.append(".mcp.json entry")
        except Exception:
            pass

    for md_file in [repo_root / "CLAUDE.md", repo_root / "AGENTS.md"]:
        if md_file.exists():
            text = md_file.read_text()
            if "contextmap" in text:
                # Remove the contextmap section
                lines = text.splitlines()
                clean = []
                skip = False
                for line in lines:
                    if "## contextmap" in line:
                        skip = True
                    elif skip and line.startswith("## "):
                        skip = False
                    if not skip:
                        clean.append(line)
                md_file.write_text("\n".join(clean))
                removed.append(str(md_file.name))

    skill_dir = Path.home() / ".claude" / "skills" / "contextmap"
    if skill_dir.exists():
        shutil.rmtree(skill_dir)
        removed.append("skill file")

    return removed
