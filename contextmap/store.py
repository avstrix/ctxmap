"""
Unified graph store.
- SQLite: structural layer (fast reads/writes, incremental diffs)
- NetworkX: semantic layer (in-memory, loaded on demand)
Both layers share node IDs: filepath::symbol_name
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Generator

import networkx as nx


SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    kind TEXT NOT NULL,
    source_file TEXT,
    source_location TEXT,
    docstring TEXT,
    confidence TEXT DEFAULT 'EXTRACTED',
    metadata TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    target TEXT NOT NULL,
    relation TEXT NOT NULL,
    confidence TEXT DEFAULT 'EXTRACTED',
    confidence_score REAL DEFAULT 1.0,
    label TEXT DEFAULT '',
    UNIQUE(source, target, relation)
);

CREATE TABLE IF NOT EXISTS file_hashes (
    path TEXT PRIMARY KEY,
    hash TEXT NOT NULL,
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS semantic_cache (
    file_hash TEXT PRIMARY KEY,
    nodes_json TEXT NOT NULL,
    edges_json TEXT NOT NULL,
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source);
CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target);
CREATE INDEX IF NOT EXISTS idx_nodes_file  ON nodes(source_file);
CREATE INDEX IF NOT EXISTS idx_nodes_kind  ON nodes(kind);
"""


@dataclass
class GraphStats:
    nodes: int
    edges: int
    files: int
    languages: list[str]
    structural_only: bool  # True if semantic layer not yet built


class GraphStore:
    """
    Thread-safe graph store with SQLite backend.
    NetworkX graph is built lazily when semantic queries are made.
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._nx_graph: nx.Graph | None = None
        self._nx_dirty = True
        self._init_db()

    def _init_db(self):
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _conn(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(
            self.db_path,
            check_same_thread=False,
            timeout=10,
        )
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ─── Write ────────────────────────────────────────────────

    def upsert_nodes(self, nodes: list[dict]):
        with self._lock:
            with self._conn() as conn:
                conn.executemany(
                    """INSERT INTO nodes (id, label, kind, source_file, source_location, docstring, confidence, metadata)
                       VALUES (:id, :label, :kind, :source_file, :source_location, :docstring, :confidence, :metadata)
                       ON CONFLICT(id) DO UPDATE SET
                           label=excluded.label, kind=excluded.kind,
                           source_file=excluded.source_file, source_location=excluded.source_location,
                           docstring=excluded.docstring, confidence=excluded.confidence,
                           metadata=excluded.metadata""",
                    [
                        {
                            "id": n["id"],
                            "label": n.get("label", n["id"]),
                            "kind": n.get("kind", "unknown"),
                            "source_file": n.get("source_file", ""),
                            "source_location": n.get("source_location", ""),
                            "docstring": n.get("docstring", ""),
                            "confidence": n.get("confidence", "EXTRACTED"),
                            "metadata": json.dumps(n.get("metadata", {})),
                        }
                        for n in nodes
                    ],
                )
        self._nx_dirty = True

    def upsert_edges(self, edges: list[dict]):
        with self._lock:
            with self._conn() as conn:
                conn.executemany(
                    """INSERT INTO edges (source, target, relation, confidence, confidence_score, label)
                       VALUES (:source, :target, :relation, :confidence, :confidence_score, :label)
                       ON CONFLICT(source, target, relation) DO UPDATE SET
                           confidence=excluded.confidence,
                           confidence_score=excluded.confidence_score,
                           label=excluded.label""",
                    [
                        {
                            "source": e["source"],
                            "target": e["target"],
                            "relation": e.get("relation", "related_to"),
                            "confidence": e.get("confidence", "EXTRACTED"),
                            "confidence_score": e.get("confidence_score", 1.0),
                            "label": e.get("label", ""),
                        }
                        for e in edges
                    ],
                )
        self._nx_dirty = True

    def set_file_hash(self, path: str, hash_: str):
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO file_hashes(path, hash) VALUES(?,?) ON CONFLICT(path) DO UPDATE SET hash=excluded.hash, updated_at=datetime('now')",
                (path, hash_),
            )

    def get_file_hash(self, path: str) -> str | None:
        with self._conn() as conn:
            row = conn.execute("SELECT hash FROM file_hashes WHERE path=?", (path,)).fetchone()
            return row["hash"] if row else None

    def delete_file_nodes(self, path: str):
        """Remove all nodes and edges for a deleted/changed file."""
        with self._lock:
            with self._conn() as conn:
                node_ids = [
                    r["id"]
                    for r in conn.execute("SELECT id FROM nodes WHERE source_file=?", (path,)).fetchall()
                ]
                if node_ids:
                    placeholders = ",".join("?" * len(node_ids))
                    conn.execute(f"DELETE FROM edges WHERE source IN ({placeholders}) OR target IN ({placeholders})", node_ids + node_ids)
                    conn.execute(f"DELETE FROM nodes WHERE id IN ({placeholders})", node_ids)
        self._nx_dirty = True

    def save_semantic_cache(self, file_hash: str, nodes: list[dict], edges: list[dict]):
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO semantic_cache(file_hash, nodes_json, edges_json) VALUES(?,?,?) ON CONFLICT(file_hash) DO UPDATE SET nodes_json=excluded.nodes_json, edges_json=excluded.edges_json, updated_at=datetime('now')",
                (file_hash, json.dumps(nodes), json.dumps(edges)),
            )

    def get_semantic_cache(self, file_hash: str) -> tuple[list[dict], list[dict]] | None:
        with self._conn() as conn:
            row = conn.execute("SELECT nodes_json, edges_json FROM semantic_cache WHERE file_hash=?", (file_hash,)).fetchone()
            if row:
                return json.loads(row["nodes_json"]), json.loads(row["edges_json"])
        return None

    # ─── Read ────────────────────────────────────────────────

    def get_node(self, node_id: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM nodes WHERE id=?", (node_id,)).fetchone()
            return dict(row) if row else None

    def get_neighbors(self, node_id: str, relation: str | None = None) -> list[dict]:
        with self._conn() as conn:
            if relation:
                rows = conn.execute(
                    "SELECT n.*, e.relation, e.confidence, e.confidence_score FROM nodes n JOIN edges e ON n.id=e.target WHERE e.source=? AND e.relation=?",
                    (node_id, relation),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT n.*, e.relation, e.confidence, e.confidence_score FROM nodes n JOIN edges e ON n.id=e.target WHERE e.source=?",
                    (node_id,),
                ).fetchall()
            return [dict(r) for r in rows]

    def get_callers(self, node_id: str) -> list[dict]:
        """Nodes that call this node."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT n.* FROM nodes n JOIN edges e ON n.id=e.source WHERE e.target=? AND e.relation='calls'",
                (node_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def blast_radius(self, changed_files: list[str], depth: int = 3) -> dict[str, set[str]]:
        """
        BFS from changed file nodes to find all transitively affected nodes.
        Returns {file_path: set of affected node IDs}.
        """
        affected: dict[str, set[str]] = {}

        with self._conn() as conn:
            for path in changed_files:
                visited: set[str] = set()
                queue = [path]

                # Start with direct file node + all nodes in that file
                file_nodes = [r["id"] for r in conn.execute("SELECT id FROM nodes WHERE source_file=?", (path,)).fetchall()]
                queue.extend(file_nodes)

                hops = 0
                while queue and hops < depth:
                    next_queue = []
                    for nid in queue:
                        if nid in visited:
                            continue
                        visited.add(nid)
                        # Find everything that calls or imports this
                        callers = [
                            r["source"]
                            for r in conn.execute(
                                "SELECT source FROM edges WHERE target=? AND relation IN ('calls','imports','uses')",
                                (nid,),
                            ).fetchall()
                        ]
                        next_queue.extend(c for c in callers if c not in visited)
                    queue = next_queue
                    hops += 1

                affected[path] = visited

        return affected

    def search(self, query: str, limit: int = 20) -> list[dict]:
        """Simple substring search on node labels."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM nodes WHERE label LIKE ? OR docstring LIKE ? LIMIT ?",
                (f"%{query}%", f"%{query}%", limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def stats(self) -> GraphStats:
        with self._conn() as conn:
            node_count = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
            edge_count = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
            file_count = conn.execute("SELECT COUNT(DISTINCT source_file) FROM nodes WHERE source_file != ''").fetchone()[0]
            has_semantic = conn.execute("SELECT COUNT(*) FROM semantic_cache").fetchone()[0] > 0

        return GraphStats(
            nodes=node_count,
            edges=edge_count,
            files=file_count,
            languages=[],
            structural_only=not has_semantic,
        )

    # ─── NetworkX (semantic layer) ───────────────────────────

    def nx_graph(self) -> nx.Graph:
        """Build or return cached NetworkX graph from SQLite data."""
        if not self._nx_dirty and self._nx_graph is not None:
            return self._nx_graph

        G = nx.Graph()
        with self._conn() as conn:
            for row in conn.execute("SELECT * FROM nodes").fetchall():
                G.add_node(row["id"], **dict(row))
            for row in conn.execute("SELECT * FROM edges").fetchall():
                G.add_edge(
                    row["source"],
                    row["target"],
                    relation=row["relation"],
                    confidence=row["confidence"],
                    confidence_score=row["confidence_score"],
                )

        self._nx_graph = G
        self._nx_dirty = False
        return G

    def god_nodes(self, top_n: int = 10) -> list[dict]:
        """Highest-degree nodes — structural hubs."""
        G = self.nx_graph()
        if G.number_of_nodes() == 0:
            return []
        by_degree = sorted(G.degree(), key=lambda x: x[1], reverse=True)[:top_n]
        result = []
        for node_id, degree in by_degree:
            node_data = dict(G.nodes[node_id])
            node_data["degree"] = degree
            result.append(node_data)
        return result

    def surprising_connections(self, top_n: int = 10) -> list[dict]:
        """
        Edges that connect structurally distant communities.
        Scored by: cross-file INFERRED edges rank highest.
        """
        G = self.nx_graph()
        if G.number_of_edges() == 0:
            return []

        surprises = []
        for u, v, data in G.edges(data=True):
            u_data = G.nodes.get(u, {})
            v_data = G.nodes.get(v, {})
            u_file = u_data.get("source_file", "")
            v_file = v_data.get("source_file", "")

            score = 0.0
            if u_file and v_file and u_file != v_file:
                score += 1.0
            if data.get("confidence") == "INFERRED":
                score += 0.5
            if score > 0:
                surprises.append({
                    "source": u,
                    "target": v,
                    "relation": data.get("relation"),
                    "score": score,
                    "source_file": u_file,
                    "target_file": v_file,
                })

        surprises.sort(key=lambda x: x["score"], reverse=True)
        return surprises[:top_n]

    def shortest_path(self, source_id: str, target_id: str) -> list[str]:
        G = self.nx_graph()
        try:
            return nx.shortest_path(G, source_id, target_id)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return []

    def export_json(self, out_path: Path):
        """Export full graph as JSON."""
        G = self.nx_graph()
        data = {
            "nodes": [{"id": n, **G.nodes[n]} for n in G.nodes()],
            "edges": [
                {"source": u, "target": v, **d}
                for u, v, d in G.edges(data=True)
            ],
        }
        out_path.write_text(json.dumps(data, indent=2, default=str))

    def export_graphml(self, out_path: Path):
        G = self.nx_graph()
        nx.write_graphml(G, str(out_path))

    def clear(self):
        with self._lock:
            with self._conn() as conn:
                conn.execute("DELETE FROM edges")
                conn.execute("DELETE FROM nodes")
                conn.execute("DELETE FROM file_hashes")
        self._nx_dirty = True
