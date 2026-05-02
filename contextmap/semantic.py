"""
Semantic extraction via LLM (Anthropic Claude).
Only runs on non-code files (docs, PDFs, images) or when explicitly requested.
Results are cached by SHA-256 — never re-runs unchanged files.
Confidence tags: EXTRACTED (explicit in source) | INFERRED (reasonable deduction) | AMBIGUOUS (uncertain)
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
from pathlib import Path

from .store import GraphStore

logger = logging.getLogger(__name__)

SEMANTIC_EXTENSIONS = {".md", ".txt", ".rst", ".pdf", ".png", ".jpg", ".jpeg", ".webp", ".gif"}

EXTRACT_PROMPT = """\
You are extracting a knowledge graph from a document.

Return ONLY a JSON object with this exact schema — no preamble, no markdown fences:
{
  "nodes": [
    {"id": "unique_slug", "label": "Human name", "kind": "concept|decision|component|person|tool", "description": "1-2 sentences"}
  ],
  "edges": [
    {"source": "slug_a", "target": "slug_b", "relation": "calls|uses|imports|implements|related_to|decided_by|rationale_for|semantically_similar_to", "confidence": "EXTRACTED|INFERRED|AMBIGUOUS", "confidence_score": 0.0-1.0}
  ]
}

Rules:
- EXTRACTED: relationship explicitly stated in the source
- INFERRED: reasonable deduction, set confidence_score 0.5-0.9
- AMBIGUOUS: uncertain, set confidence_score 0.1-0.5
- Extract design rationale from NOTE/WHY/IMPORTANT comments
- Maximum 30 nodes and 50 edges per file
- IDs must be snake_case slugs, unique within this file

Document to extract from:
"""


async def extract_semantic(
    path: Path,
    store: GraphStore,
    file_id: str | None = None,
) -> tuple[list[dict], list[dict]]:
    """
    Extract semantic nodes/edges from a doc/image/PDF.
    Uses cache — returns immediately if file unchanged.
    """
    try:
        import anthropic
    except ImportError:
        logger.warning("anthropic not installed: pip install contextmap[semantic]")
        return [], []

    if not path.exists():
        return [], []

    src = path.read_bytes()
    file_hash = hashlib.sha256(src).hexdigest()
    fid = file_id or str(path)

    # Check cache
    cached = store.get_semantic_cache(file_hash)
    if cached:
        logger.debug(f"Semantic cache hit: {path.name}")
        return cached

    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
    suffix = path.suffix.lower()

    # Build message content
    content = []

    if suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
        media_map = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif"}
        b64 = base64.standard_b64encode(src).decode()
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": media_map.get(suffix, "image/png"), "data": b64},
        })
        content.append({"type": "text", "text": EXTRACT_PROMPT + f"\n[Image file: {path.name}]\nExtract all visible concepts, components, and relationships."})

    elif suffix == ".pdf":
        b64 = base64.standard_b64encode(src).decode()
        content.append({
            "type": "document",
            "source": {"type": "base64", "media_type": "application/pdf", "data": b64},
        })
        content.append({"type": "text", "text": EXTRACT_PROMPT})

    else:
        text = src.decode("utf-8", errors="replace")[:12000]
        content.append({"type": "text", "text": EXTRACT_PROMPT + text})

    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2000,
            messages=[{"role": "user", "content": content}],
        )
        raw = response.content[0].text.strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.warning(f"JSON parse error for {path}: {e}")
        return [], []
    except Exception as e:
        logger.warning(f"LLM extraction error for {path}: {e}")
        return [], []

    nodes = data.get("nodes", [])
    edges = data.get("edges", [])

    # Prefix IDs with file path to avoid collisions
    id_map = {}
    for node in nodes:
        orig_id = node.get("id", "")
        new_id = f"{fid}::{orig_id}"
        id_map[orig_id] = new_id
        node["id"] = new_id
        node["source_file"] = str(path)
        if "confidence" not in node:
            node["confidence"] = "EXTRACTED"

    for edge in edges:
        edge["source"] = id_map.get(edge.get("source", ""), edge.get("source", ""))
        edge["target"] = id_map.get(edge.get("target", ""), edge.get("target", ""))
        if "confidence" not in edge:
            edge["confidence"] = "INFERRED"
        if "confidence_score" not in edge:
            edge["confidence_score"] = 0.8

    # Save to cache
    store.save_semantic_cache(file_hash, nodes, edges)
    logger.info(f"Semantic extraction: {path.name} → {len(nodes)} nodes, {len(edges)} edges")
    return nodes, edges


async def run_semantic_pass(root: Path, store: GraphStore) -> dict:
    """
    Run semantic extraction on all doc/image files under root.
    Only processes files not already in semantic cache.
    """
    from .builder import collect_files

    files = collect_files(root, SEMANTIC_EXTENSIONS)
    stats = {"processed": 0, "cached": 0, "errors": 0}

    for path in files:
        try:
            src = path.read_bytes()
            file_hash = hashlib.sha256(src).hexdigest()

            if store.get_semantic_cache(file_hash):
                stats["cached"] += 1
                continue

            nodes, edges = await extract_semantic(path, store)
            if nodes:
                store.upsert_nodes(nodes)
            if edges:
                store.upsert_edges(edges)
            stats["processed"] += 1

        except Exception as e:
            logger.warning(f"Semantic pass error for {path}: {e}")
            stats["errors"] += 1

    return stats


async def transcribe_video(path: Path, store: GraphStore) -> tuple[list[dict], list[dict]]:
    """
    Transcribe video/audio with faster-whisper, then run semantic extraction on transcript.
    Requires: pip install contextmap[video]
    """
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        logger.error("faster-whisper not installed: pip install contextmap[video]")
        return [], []

    cache_dir = path.parent / ".contextmap-out" / "transcripts"
    cache_dir.mkdir(parents=True, exist_ok=True)
    transcript_path = cache_dir / f"{path.stem}.txt"

    if not transcript_path.exists():
        model = WhisperModel("base", compute_type="int8")
        segments, _ = model.transcribe(str(path))
        transcript = "\n".join(s.text for s in segments)
        transcript_path.write_text(transcript)
        logger.info(f"Transcribed: {path.name}")

    return await extract_semantic(transcript_path, store, file_id=str(path))
