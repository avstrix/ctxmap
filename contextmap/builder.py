"""
Incremental graph builder.
- Full build: scan all files, parse, store
- Incremental update: SHA-256 diff, re-parse only changed files
- Watch mode: fsevents-based continuous update
"""
from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path

from .parser import extract_file
from .store import GraphStore

logger = logging.getLogger(__name__)

# Files we index
CODE_EXTENSIONS = {
    ".py", ".js", ".jsx", ".ts", ".tsx",
    ".go", ".rs", ".java", ".rb", ".cs",
    ".kt", ".scala", ".php", ".swift",
    ".lua", ".zig", ".ex", ".exs",
    ".vue", ".svelte",
}

DOC_EXTENSIONS = {
    ".md", ".txt", ".rst", ".mdx",
}

ALL_EXTENSIONS = CODE_EXTENSIONS | DOC_EXTENSIONS

IGNORED_DIRS = {
    "node_modules", ".git", "__pycache__", ".venv", "venv",
    "dist", "build", ".next", ".nuxt", "coverage",
    ".contextmap-out", "contextmap-out",
    "vendor", "target", "bin", "obj",
}


def _should_ignore(path: Path) -> bool:
    return any(part in IGNORED_DIRS for part in path.parts)


def collect_files(root: Path, extensions: set[str] | None = None) -> list[Path]:
    """Collect all indexable files under root."""
    exts = extensions or ALL_EXTENSIONS
    files = []
    ignore_file = root / ".contextmapignore"
    extra_ignores: set[str] = set()

    if ignore_file.exists():
        for line in ignore_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                extra_ignores.add(line.strip("/"))

    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if _should_ignore(path):
            continue
        if any(part in extra_ignores for part in path.relative_to(root).parts):
            continue
        if path.suffix.lower() in exts:
            files.append(path)

    return sorted(files)


def build(root: Path, store: GraphStore, force: bool = False) -> dict:
    """
    Full build: parse all files and store structural graph.
    Returns stats dict.
    """
    start = time.perf_counter()
    files = collect_files(root)
    logger.info(f"Found {len(files)} files to index")

    stats = {"parsed": 0, "skipped": 0, "errors": 0, "files": len(files)}

    for path in files:
        try:
            current_hash = _file_hash(path)
            stored_hash = store.get_file_hash(str(path))

            if not force and stored_hash == current_hash:
                stats["skipped"] += 1
                continue

            # Remove stale nodes for this file before re-parsing
            if stored_hash:
                store.delete_file_nodes(str(path))

            result = extract_file(path)
            if result.nodes:
                store.upsert_nodes(result.nodes)
            if result.edges:
                store.upsert_edges(result.edges)

            store.set_file_hash(str(path), current_hash)
            stats["parsed"] += 1

        except Exception as e:
            logger.warning(f"Error parsing {path}: {e}")
            stats["errors"] += 1

    elapsed = time.perf_counter() - start
    stats["elapsed_ms"] = round(elapsed * 1000)
    logger.info(f"Build complete: {stats}")
    return stats


def update(root: Path, store: GraphStore) -> dict:
    """
    Incremental update: only re-parse files whose hash changed.
    Fast path — typically <2s on large repos.
    """
    return build(root, store, force=False)


def _file_hash(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def watch(root: Path, store: GraphStore, callback=None):
    """
    Watch for file changes and incrementally update.
    Requires watchdog.
    """
    try:
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler
    except ImportError:
        logger.error("watchdog not installed: pip install watchdog")
        return

    class Handler(FileSystemEventHandler):
        def on_modified(self, event):
            if event.is_directory:
                return
            path = Path(event.src_path)
            if path.suffix.lower() in ALL_EXTENSIONS and not _should_ignore(path):
                _handle_change(path, store)
                if callback:
                    callback(path)

        def on_created(self, event):
            self.on_modified(event)

        def on_deleted(self, event):
            path = Path(event.src_path)
            if not _should_ignore(path):
                store.delete_file_nodes(str(path))
                if callback:
                    callback(path)

    def _handle_change(path: Path, store: GraphStore):
        try:
            current_hash = _file_hash(path)
            if not current_hash:
                return
            stored_hash = store.get_file_hash(str(path))
            if stored_hash == current_hash:
                return
            store.delete_file_nodes(str(path))
            result = extract_file(path)
            if result.nodes:
                store.upsert_nodes(result.nodes)
            if result.edges:
                store.upsert_edges(result.edges)
            store.set_file_hash(str(path), current_hash)
            logger.info(f"Updated: {path.name}")
        except Exception as e:
            logger.warning(f"Watch update error for {path}: {e}")

    observer = Observer()
    observer.schedule(Handler(), str(root), recursive=True)
    observer.start()
    logger.info(f"Watching {root} for changes...")
    return observer
