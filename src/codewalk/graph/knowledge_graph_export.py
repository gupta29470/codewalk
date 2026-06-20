"""Export DuckDB graph analysis to knowledge-graph.json for the dashboard UI.

Schema version 1.0 — compatible with Understand-Anything-style consumers:
  nodes: file | function | class | method | module | concept
  edges: imports | calls | exports | extends | related | module_dep | contains
  layers: one per detected module (structural; no LLM required)
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import igraph as ig

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


def _compute_node_positions(graph: dict, canvas_size: float = 2000.0) -> None:
    """Pre-compute 2D positions for every node and store them as x/y.

    Layout is computed with igraph's Fruchterman-Reingold algorithm per
    connected component so disconnected subgraphs don't pile on top of each
    other. This lets the frontend skip expensive client-side layout.
    """
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    if not nodes:
        return

    id_to_node = {n["id"]: n for n in nodes}
    pairs = [
        (e["source"], e["target"])
        for e in edges
        if e["source"] in id_to_node and e["target"] in id_to_node
    ]
    if not pairs:
        # No edges: lay nodes out in a simple grid.
        cols = max(1, int(len(nodes) ** 0.5))
        spacing = canvas_size / cols
        for i, node in enumerate(nodes):
            node["x"] = round((i % cols) * spacing, 2)
            node["y"] = round((i // cols) * spacing, 2)
        return

    try:
        g = ig.Graph.TupleList(pairs, directed=True)
    except Exception:
        return

    names = g.vs["name"]
    clusters = g.connected_components()
    component_layouts: list[tuple[list[int], ig.Layout]] = []
    for comp in clusters:
        subg = g.subgraph(comp)
        niter = max(30, min(100, len(comp) // 20))
        try:
            lo = subg.layout("fruchterman_reingold", niter=niter)
        except Exception:
            continue
        component_layouts.append((comp, lo))

    if not component_layouts:
        return

    cols = max(1, int(len(component_layouts) ** 0.5))
    for comp_idx, (comp, lo) in enumerate(component_layouts):
        coords = lo.coords
        xs = [c[0] for c in coords]
        ys = [c[1] for c in coords]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        width = max_x - min_x or 1.0
        height = max_y - min_y or 1.0
        scale = canvas_size / max(width, height)
        col = comp_idx % cols
        row = comp_idx // cols
        offset_x = col * canvas_size
        offset_y = row * canvas_size
        for local_idx, v in enumerate(comp):
            name = names[v]
            node = id_to_node.get(name)
            if node:
                x, y = coords[local_idx]
                node["x"] = round(offset_x + (x - min_x) * scale, 2)
                node["y"] = round(offset_y + (y - min_y) * scale, 2)


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


def _file_centrality_metrics(store) -> dict[str, dict[str, float | int]]:
    """Compute PageRank, betweenness, and degree for each file from import edges."""
    edges = store.get_import_edges()
    if not edges:
        return {}
    try:
        graph = ig.Graph.TupleList(edges, directed=True)
    except Exception:
        return {}
    names = graph.vs["name"]
    pagerank = graph.pagerank()
    betweenness = graph.betweenness()
    indegree = graph.indegree()
    outdegree = graph.outdegree()
    return {
        name: {
            "pageRank": round(float(pagerank[i]), 6),
            "betweenness": round(float(betweenness[i]), 4),
            "inDegree": int(indegree[i]),
            "outDegree": int(outdegree[i]),
        }
        for i, name in enumerate(names)
    }


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


def _generate_heuristic_tour(graph: dict) -> list[dict]:
    """Generate a guided tour from the knowledge graph topology.

    Mirrors the heuristic in the Understand-Anything dashboard's
    `generateHeuristicTour`: topological walk, grouped by layers when present.
    """
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    layers = graph.get("layers", [])

    node_map = {n["id"]: n for n in nodes if "id" in n}
    concept_nodes = [n for n in nodes if n.get("type") == "concept"]
    code_nodes = [n for n in nodes if n.get("type") != "concept"]
    code_node_ids = {n["id"] for n in code_nodes}

    in_degree: dict[str, int] = {nid: 0 for nid in code_node_ids}
    adjacency: dict[str, list[str]] = {nid: [] for nid in code_node_ids}

    for edge in edges:
        src = edge.get("source")
        tgt = edge.get("target")
        if src in code_node_ids and tgt in code_node_ids:
            in_degree[tgt] = in_degree.get(tgt, 0) + 1
            adjacency[src].append(tgt)

    # Kahn's algorithm for a topological ordering of code nodes.
    queue = [nid for nid, deg in in_degree.items() if deg == 0]
    topo_order: list[str] = []
    while queue:
        current = queue.pop(0)
        topo_order.append(current)
        for neighbor in adjacency.get(current, []):
            new_degree = in_degree.get(neighbor, 1) - 1
            in_degree[neighbor] = new_degree
            if new_degree == 0:
                queue.append(neighbor)

    # Append any nodes missed due to cycles or isolation.
    reached = set(topo_order)
    for node in code_nodes:
        if node["id"] not in reached:
            topo_order.append(node["id"])

    steps: list[dict] = []

    if layers:
        node_to_layer: dict[str, str] = {}
        for layer in layers:
            for nid in layer.get("nodeIds", []):
                node_to_layer[nid] = layer["id"]

        layer_order: list[str] = []
        layer_nodes: dict[str, list[str]] = {}
        for nid in topo_order:
            layer_id = node_to_layer.get(nid)
            if layer_id:
                if layer_id not in layer_nodes:
                    layer_nodes[layer_id] = []
                    layer_order.append(layer_id)
                layer_nodes[layer_id].append(nid)

        layer_map = {layer["id"]: layer for layer in layers if "id" in layer}
        for layer_id in layer_order:
            layer = layer_map.get(layer_id)
            node_ids = layer_nodes.get(layer_id, [])
            if not layer or not node_ids:
                continue
            names = [node_map[nid]["name"] for nid in node_ids if nid in node_map]
            steps.append({
                "order": 0,
                "title": layer.get("name", layer_id),
                "description": (
                    f"{layer.get('description', '')}. Key files: {', '.join(names)}."
                ),
                "nodeIds": node_ids,
            })

        layered_node_ids = {nid for layer in layers for nid in layer.get("nodeIds", [])}
        unlayered = [nid for nid in topo_order if nid not in layered_node_ids]
        if unlayered:
            names = [node_map[nid]["name"] for nid in unlayered if nid in node_map]
            steps.append({
                "order": 0,
                "title": "Supporting Components",
                "description": f"Additional supporting files: {', '.join(names)}.",
                "nodeIds": unlayered,
            })
    else:
        for i in range(0, len(topo_order), 3):
            batch = topo_order[i : i + 3]
            summaries = [
                f"{node_map[nid].get('name', nid)} ({node_map[nid].get('summary', '')})"
                for nid in batch
                if nid in node_map
            ]
            step_number = i // 3 + 1
            steps.append({
                "order": 0,
                "title": f"Step {step_number}: Code Walkthrough",
                "description": f"Exploring: {'; '.join(summaries)}.",
                "nodeIds": batch,
            })

    if concept_nodes:
        concept_summaries = [
            f"{n.get('name', '')} ({n.get('summary', '')})" for n in concept_nodes
        ]
        steps.append({
            "order": 0,
            "title": "Key Concepts",
            "description": f"Important architectural concepts: {'; '.join(concept_summaries)}.",
            "nodeIds": [n["id"] for n in concept_nodes],
        })

    for i, step in enumerate(steps):
        step["order"] = i + 1

    return steps


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

    # Centrality metrics from the file-level import graph.
    file_centrality = _file_centrality_metrics(store)

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
        centrality = file_centrality.get(path, {})
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
                **centrality,
            },
        })
        node_ids.add(nid)

    # ── Symbol nodes (functions / classes / methods) ──────────────────
    symbol_rows = store.conn.execute(
        "SELECT s.symbol_id, s.name, s.qualified_name, s.symbol_type, "
        "s.start_line, s.end_line, s.parent_class, f.path, "
        "sm.kind, sm.http_method, sm.http_path, sm.event_name, sm.cli_command "
        "FROM symbols s JOIN files f ON s.file_id = f.file_id "
        "LEFT JOIN symbol_metadata sm ON s.symbol_id = sm.symbol_id "
        "ORDER BY f.path, s.start_line, s.symbol_id"
    ).fetchall()

    symbols_by_file: dict[str, list[str]] = {}
    symbol_id_to_nid: dict[str, str] = {}

    def _symbol_summary_from_meta(kind, http_method, http_path, event_name, cli_command) -> str:
        if http_method and http_path:
            return f"{http_method} {http_path}"
        if http_path:
            return f"route {http_path}"
        if cli_command:
            return f"CLI: {cli_command}"
        if event_name:
            return f"Event: {event_name}"
        if kind:
            return kind.replace("_", " ").title()
        return ""

    for (
        sid, name, qname, sym_type, start_line, end_line, parent_class, fpath,
        kind, http_method, http_path, event_name, cli_command
    ) in symbol_rows:
        node_type = sym_type if sym_type in ("class", "method") else "function"
        # Use the DB symbol_id as the node id suffix to avoid collisions when
        # the same qualified name appears multiple times (e.g. overloaded
        # methods or minified JS with identical names on the same line).
        nid = f"{node_type}:{sid}"
        symbol_id_to_nid[sid] = nid
        line_len = max(0, (end_line or start_line) - start_line)
        tags = [sym_type] if sym_type else []
        if kind:
            tags.append(kind)
        if parent_class:
            tags.append(f"in:{parent_class}")
        summary = _symbol_summary_from_meta(kind, http_method, http_path, event_name, cli_command)
        nodes.append({
            "id": nid,
            "type": node_type,
            "name": name,
            "filePath": fpath,
            "qualifiedName": qname,
            "lineRange": [start_line, end_line],
            "summary": summary,
            "tags": sorted(set(tags)),
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

    # ── Class hierarchy edges (class → parent class) ──────────────────
    for class_sid, parent_sid in store.conn.execute(
        "SELECT class_symbol_id, parent_symbol_id FROM class_hierarchy"
    ).fetchall():
        src = symbol_id_to_nid.get(class_sid)
        tgt = symbol_id_to_nid.get(parent_sid)
        if src and tgt and src in node_ids and tgt in node_ids:
            edges.append(_edge(src, tgt, "extends", weight=0.8))

    # ── Class member edges (class → method/function) ──────────────────
    for class_sid, member_sid in store.conn.execute(
        "SELECT class_symbol_id, member_symbol_id FROM class_members"
    ).fetchall():
        src = symbol_id_to_nid.get(class_sid)
        tgt = symbol_id_to_nid.get(member_sid)
        if src and tgt and src in node_ids and tgt in node_ids:
            edges.append(_edge(src, tgt, "contains", weight=0.85))

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
        SELECT sc.caller_symbol_id, sc.callee_symbol_id, sc.line
        FROM symbol_calls sc
        """
    ).fetchall()

    for caller_sid, callee_sid, line in call_rows:
        src = symbol_id_to_nid.get(caller_sid)
        tgt = symbol_id_to_nid.get(callee_sid)
        if src and tgt and src in node_ids and tgt in node_ids:
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

    graph = {
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
    graph["tour"] = _generate_heuristic_tour(graph)
    _compute_node_positions(graph)
    return graph


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
