"""Export DuckDB graph analysis to knowledge-graph.json for the dashboard UI.

Schema version 1.0 — compatible with Understand-Anything-style consumers:
  nodes: file | function | class | method | module
  edges: imports | calls | exports | related | module_dep | contains
  layers: one per detected module (structural; no LLM required)
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.codewalk.log import log as _log

KNOWLEDGE_GRAPH_VERSION = "1.0.0"
KNOWLEDGE_GRAPH_FILENAME = "knowledge-graph.json"
# Cap same-file symbol pairs for related edges (layout hint, not semantics)
_RELATED_MAX_SYMBOLS_PER_FILE = 12
_RELATED_EDGE_WEIGHT = 0.6


def _node_id_file(file_path: str) -> str:
    return f"file:{file_path}"


def _node_id_symbol(qualified_name: str, symbol_type: str = "function") -> str:
    """UA-style id prefix: class: / method: / function: + qualified_name."""
    if symbol_type == "class":
        prefix = "class"
    elif symbol_type == "method":
        prefix = "method"
    else:
        prefix = "function"
    return f"{prefix}:{qualified_name}"


def _node_id_module(module_name: str) -> str:
    return f"module:{module_name}"


def _complexity_from_bytes(size_bytes: int) -> str:
    if size_bytes < 4_000:
        return "simple"
    if size_bytes < 20_000:
        return "moderate"
    return "complex"


def _edge(source: str, target: str, edge_type: str, weight: float = 0.8, **extra: Any) -> dict:
    row = {
        "source": source,
        "target": target,
        "type": edge_type,
        "direction": "forward",
        "weight": weight,
    }
    row.update(extra)
    return row


def _related_edges_for_file(symbol_ids: list[str]) -> list[dict]:
    """Same-file symbol pairs — UA-style layout hints (weight 0.6)."""
    if len(symbol_ids) < 2:
        return []
    limited = symbol_ids[:_RELATED_MAX_SYMBOLS_PER_FILE]
    edges: list[dict] = []
    for i in range(len(limited)):
        for j in range(i + 1, len(limited)):
            edges.append(_edge(
                limited[i], limited[j], "related", weight=_RELATED_EDGE_WEIGHT,
            ))
    return edges


def build_knowledge_graph(
    store,
    *,
    files: list[dict],
    modules_result: dict,
    repo_name: str = "",
    repo_path: str = "",
    languages: list[str] | None = None,
    tech_stack: list[str] | None = None,
    commit_sha: str = "",
    branch: str = "",
) -> dict:
    """Build knowledge-graph dict from an open GraphStore (before close)."""
    from src.codewalk.graph.graph_store import GraphStore

    if not isinstance(store, GraphStore):
        raise TypeError("store must be a GraphStore instance")

    file_meta = {f["file_path"]: f for f in files}
    lang_set = sorted({f.get("language", "unknown") for f in files if f.get("language")})
    languages = languages or lang_set

    nodes: list[dict] = []
    edges: list[dict] = []
    node_ids: set[str] = set()

    # ── File nodes ────────────────────────────────────────────────────
    for row in store.conn.execute(
        "SELECT path, module, language FROM files ORDER BY path"
    ).fetchall():
        path, module, language = row
        nid = _node_id_file(path)
        meta = file_meta.get(path, {})
        size = int(meta.get("size_bytes") or 0)
        importer_count = len(store.get_importers(path))
        import_count = len(store.get_imports(path))
        nodes.append({
            "id": nid,
            "type": "file",
            "name": os.path.basename(path),
            "filePath": path,
            "language": language or meta.get("language", "unknown"),
            "module": module or "",
            "summary": "",
            "tags": sorted(filter(None, [language, module])),
            "complexity": _complexity_from_bytes(size),
            "metrics": {
                "sizeBytes": size,
                "importCount": import_count,
                "importerCount": importer_count,
            },
        })
        node_ids.add(nid)

    # ── Symbol nodes (functions / classes / methods) ──────────────────
    symbol_rows = store.conn.execute(
        "SELECT s.symbol_id, s.name, s.qualified_name, s.symbol_type, "
        "s.start_line, s.end_line, f.path "
        "FROM symbols s JOIN files f ON s.file_id = f.file_id "
        "ORDER BY f.path, s.start_line"
    ).fetchall()

    symbols_by_file: dict[str, list[str]] = {}

    for sid, name, qname, sym_type, start_line, end_line, fpath in symbol_rows:
        node_type = sym_type if sym_type in ("class", "method") else "function"
        nid = _node_id_symbol(qname, node_type)
        line_len = max(0, (end_line or start_line) - start_line)
        nodes.append({
            "id": nid,
            "type": node_type,
            "name": name,
            "filePath": fpath,
            "qualifiedName": qname,
            "lineRange": [start_line, end_line],
            "summary": "",
            "tags": [sym_type] if sym_type else [],
            "complexity": _complexity_from_bytes(line_len * 40),
            "metrics": {
                "startLine": start_line,
                "endLine": end_line,
            },
        })
        node_ids.add(nid)
        symbols_by_file.setdefault(fpath, []).append(nid)
        file_nid = _node_id_file(fpath)
        if file_nid in node_ids:
            edges.append(_edge(file_nid, nid, "exports", weight=0.9))

    # ── Related edges (same-file symbols — UA layout) ─────────────────
    for fpath, sids in symbols_by_file.items():
        for rel in _related_edges_for_file(sids):
            if rel["source"] in node_ids and rel["target"] in node_ids:
                edges.append(rel)

    # ── Module nodes ──────────────────────────────────────────────────
    modules = modules_result.get("modules") or {}
    module_graph = modules_result.get("module_graph") or {}

    for module_name, info in sorted(modules.items()):
        nid = _node_id_module(module_name)
        file_paths = info.get("files") or []
        nodes.append({
            "id": nid,
            "type": "module",
            "name": module_name,
            "filePath": "",
            "summary": "",
            "tags": ["module"],
            "complexity": "moderate",
            "metrics": {
                "fileCount": len(file_paths),
            },
            "filePaths": file_paths,
        })
        node_ids.add(nid)
        for fp in file_paths:
            fid = _node_id_file(fp)
            if fid in node_ids:
                edges.append(_edge(nid, fid, "contains", weight=0.5))

    # ── Import edges (file → file) ────────────────────────────────────
    for source_path, target_path in store.get_import_edges():
        src, tgt = _node_id_file(source_path), _node_id_file(target_path)
        if src in node_ids and tgt in node_ids:
            edges.append(_edge(src, tgt, "imports", weight=1.0))

    # ── Call edges (symbol → symbol) ──────────────────────────────────
    call_rows = store.conn.execute(
        """
        SELECT cs.qualified_name, ct.qualified_name, sc.line
        FROM symbol_calls sc
        JOIN symbols cs ON sc.caller_symbol_id = cs.symbol_id
        JOIN symbols ct ON sc.callee_symbol_id = ct.symbol_id
        """
    ).fetchall()
    symbol_type_by_qname = {
        row[2]: (row[3] if row[3] in ("class", "method") else "function")
        for row in symbol_rows
    }

    for caller_q, callee_q, line in call_rows:
        src = _node_id_symbol(caller_q, symbol_type_by_qname.get(caller_q, "function"))
        tgt = _node_id_symbol(callee_q, symbol_type_by_qname.get(callee_q, "function"))
        if src in node_ids and tgt in node_ids:
            edges.append(_edge(src, tgt, "calls", weight=0.8, line=line))

    # ── Module dependency edges ───────────────────────────────────────
    for source_mod, target_mod in store.get_module_dep_edges():
        src, tgt = _node_id_module(source_mod), _node_id_module(target_mod)
        if src in node_ids and tgt in node_ids:
            edges.append(_edge(src, tgt, "module_dep", weight=0.7))

    # ── Layers (structural — one per module) ──────────────────────────
    layers = []
    for module_name, info in sorted(modules.items()):
        member_ids = [_node_id_file(fp) for fp in (info.get("files") or [])]
        member_ids = [i for i in member_ids if i in node_ids]
        member_ids.append(_node_id_module(module_name))
        layers.append({
            "id": f"layer:{module_name}",
            "name": module_name,
            "description": (
                f"Module group ({len(info.get('files') or [])} files) "
                "detected by codewalk module_detector"
            ),
            "nodeIds": member_ids,
        })

    stats = store._get_stats()
    graph_stats = store.conn.execute(
        "SELECT source, target FROM module_deps"
    ).fetchall()

    return {
        "version": KNOWLEDGE_GRAPH_VERSION,
        "project": {
            "name": repo_name or (Path(repo_path).name if repo_path else ""),
            "repoPath": repo_path,
            "languages": languages,
            "frameworks": tech_stack or [],
            "description": "",
            "analyzedAt": datetime.now(timezone.utc).isoformat(),
            "gitCommitHash": commit_sha,
            "branch": branch,
        },
        "nodes": nodes,
        "edges": edges,
        "layers": layers,
        "stats": {
            **stats,
            "nodeCount": len(nodes),
            "edgeCount": len(edges),
            "layerCount": len(layers),
            "moduleDepCount": len(graph_stats),
        },
    }


def validate_knowledge_graph(graph: dict) -> list[str]:
    """Return validation error strings; empty list means OK."""
    errors: list[str] = []
    required_top = ("version", "project", "nodes", "edges", "layers", "stats")
    for key in required_top:
        if key not in graph:
            errors.append(f"missing top-level key: {key}")

    if errors:
        return errors

    node_ids = set()
    for i, node in enumerate(graph["nodes"]):
        for field in ("id", "type", "name"):
            if field not in node:
                errors.append(f"node[{i}] missing {field}")
        if "id" in node:
            if node["id"] in node_ids:
                errors.append(f"duplicate node id: {node['id']}")
            node_ids.add(node["id"])

    for i, edge in enumerate(graph["edges"]):
        for field in ("source", "target", "type"):
            if field not in edge:
                errors.append(f"edge[{i}] missing {field}")
        if edge.get("source") not in node_ids:
            errors.append(f"edge[{i}] unknown source: {edge.get('source')}")
        if edge.get("target") not in node_ids:
            errors.append(f"edge[{i}] unknown target: {edge.get('target')}")

    return errors


def write_knowledge_graph(index_dir: str, graph: dict) -> str:
    """Write knowledge-graph.json under index_dir (.codewalk/). Returns path."""
    errors = validate_knowledge_graph(graph)
    if errors:
        raise ValueError(f"Invalid knowledge graph: {errors[:5]}")

    out = Path(index_dir) / KNOWLEDGE_GRAPH_FILENAME
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(graph, f, indent=2)
    _log(f"[knowledge-graph] Wrote {out} ({graph['stats']['nodeCount']} nodes, "
         f"{graph['stats']['edgeCount']} edges)")
    _patch_manifest(index_dir)
    return str(out)


def _patch_manifest(index_dir: str) -> None:
    """Add knowledge-graph fields to manifest.json when present."""
    manifest_path = Path(index_dir) / "manifest.json"
    if not manifest_path.is_file():
        return
    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)
    manifest["knowledge_graph_version"] = KNOWLEDGE_GRAPH_VERSION
    manifest["has_knowledge_graph"] = True
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)


def export_knowledge_graph_from_store(
    store,
    *,
    index_dir: str,
    files: list[dict],
    modules_result: dict,
    repo_path: str = "",
    repo_name: str = "",
    tech_stack: list[str] | None = None,
    commit_sha: str = "",
    branch: str = "",
) -> str:
    """Build, validate, and write knowledge-graph.json. Call before store.close()."""
    if not repo_name and repo_path:
        repo_name = Path(repo_path).name

    tech = tech_stack
    if tech is None and repo_path:
        from src.codewalk.ingestion.tech_detect import detect_tech_stack
        tech = detect_tech_stack(repo_path)

    graph = build_knowledge_graph(
        store,
        files=files,
        modules_result=modules_result,
        repo_name=repo_name,
        repo_path=repo_path,
        tech_stack=tech or [],
        commit_sha=commit_sha,
        branch=branch,
    )
    return write_knowledge_graph(index_dir, graph)


def load_knowledge_graph(index_dir: str) -> dict | None:
    """Load knowledge-graph.json if present."""
    path = Path(index_dir) / KNOWLEDGE_GRAPH_FILENAME
    if not path.is_file():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)
