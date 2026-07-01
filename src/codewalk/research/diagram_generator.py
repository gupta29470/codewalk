"""Generate a focused file + function/class diagram for research reports.

Uses the DuckDB graph store (symbols, symbol_calls, imports, files) so the
diagram is grounded in the actual codebase rather than LLM-generated Mermaid.

Output is designed for ReactFlow subflows:
- file nodes are parent containers.
- class nodes are nested parent containers (child of file).
- function/method nodes are leaf nodes.
- contains/member_of relationships are implicit via parentId.
- imports/calls edges are explicit.
"""
from __future__ import annotations
from collections import deque

from src.codewalk.graph.graph_store import GraphStore, _stable_id

MAX_NODES = 120
MAX_FILES = 16
MAX_SYMBOLS_PER_FILE = 12

_CODE_EXTENSIONS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".go", ".rs", ".rb", ".php",
    ".c", ".cpp", ".h", ".hpp", ".cs", ".kt", ".swift", ".dart",
}


def _is_code_file(path: str) -> bool:
    """Keep source files; drop docs, configs, and lock files."""
    from pathlib import Path
    suffix = Path(path).suffix.lower()
    return suffix in _CODE_EXTENSIONS


def generate_research_diagram(
    sources: list[str],
    graph_store: GraphStore | None,
    max_nodes: int = MAX_NODES,
) -> dict | None:
    """Build a focused research diagram from real code data.

    Args:
        sources: File paths identified as relevant by the research step.
        graph_store: DuckDB graph store with symbols/imports/calls tables.
        max_nodes: Hard cap on total nodes to keep the diagram readable.

    Returns:
        {"nodes": [...], "edges": [...]} or None if graph_store is unavailable
        or no relevant data exists.
    """
    if not graph_store or not sources:
        return None

    # 1. Resolve source files that exist in the graph store.
    source_ids = [_stable_id(p) for p in set(sources)]
    placeholders = ", ".join(["?"] * len(source_ids))
    file_rows = graph_store.conn.execute(
        f"SELECT file_id, path, module FROM files WHERE file_id IN ({placeholders})",
        source_ids,
    ).fetchall()

    if not file_rows:
        return None

    seed_files = {
        row[0]: {"path": row[1], "module": row[2]}
        for row in file_rows
        if _is_code_file(row[1])
    }
    seed_ids = set(seed_files.keys())
    if not seed_ids:
        return None

    # 2. Expand one level through imports to show context, capped to MAX_FILES.
    neighbor_ids = _expand_import_neighbors(graph_store, seed_ids)
    neighbor_info = _load_file_info(graph_store, neighbor_ids)
    neighbor_ids = {
        fid for fid, info in neighbor_info.items()
        if _is_code_file(info["path"])
    }
    relevant_file_ids = seed_ids | neighbor_ids
    if len(relevant_file_ids) > MAX_FILES:
        remaining = MAX_FILES - len(seed_ids)
        sorted_neighbors = sorted(
            neighbor_ids,
            key=lambda fid: neighbor_info.get(fid, {}).get("path", fid),
        )
        relevant_file_ids = seed_ids | set(sorted_neighbors[:max(0, remaining)])

    # 3. Load symbols for relevant files.
    symbols = _load_symbols(graph_store, relevant_file_ids)
    if not symbols:
        nodes, edges = _build_file_only_diagram(seed_files, neighbor_ids, graph_store)
        if not nodes:
            return None
        return {"nodes": nodes, "edges": edges}

    # 4. Prioritize and cap symbols.
    selected_symbols, selected_symbol_ids = _select_symbols(
        symbols, seed_ids, max_nodes=max_nodes - len(relevant_file_ids)
    )

    # 5. Load cross-symbol call edges.
    call_edges = _load_symbol_calls(graph_store, selected_symbol_ids)

    # 6. Build unified nodes and edges with parentId hierarchy.
    nodes, edges = _build_unified_graph(
        seed_files,
        relevant_file_ids,
        selected_symbols,
        selected_symbol_ids,
        call_edges,
        graph_store,
    )

    if not nodes:
        return None

    # 7. Compute a coarse left-to-right level for each node (frontend can override).
    nodes = _compute_levels(nodes, edges)

    return {"nodes": nodes, "edges": edges}


def generate_research_graph_context(
    sources: list[str],
    graph_store: GraphStore | None,
    max_files: int = 12,
    max_symbols_per_file: int = 8,
) -> str:
    """Build a grounded text summary of the relevant architecture for the LLM.

    Returns an empty string if no graph data is available.
    """
    if not graph_store or not sources:
        return ""

    source_ids = [_stable_id(p) for p in set(sources)]
    placeholders = ", ".join(["?"] * len(source_ids))
    file_rows = graph_store.conn.execute(
        f"SELECT file_id, path, module FROM files WHERE file_id IN ({placeholders})",
        source_ids,
    ).fetchall()

    seed_files = {
        row[0]: {"path": row[1], "module": row[2]}
        for row in file_rows
        if _is_code_file(row[1])
    }
    seed_ids = set(seed_files.keys())
    if not seed_ids:
        return ""

    neighbor_ids = _expand_import_neighbors(graph_store, seed_ids)
    neighbor_info = _load_file_info(graph_store, neighbor_ids)
    neighbor_ids = {
        fid for fid, info in neighbor_info.items()
        if _is_code_file(info["path"])
    }
    relevant_file_ids = seed_ids | neighbor_ids
    if len(relevant_file_ids) > max_files:
        remaining = max_files - len(seed_ids)
        sorted_neighbors = sorted(
            neighbor_ids,
            key=lambda fid: neighbor_info.get(fid, {}).get("path", fid),
        )
        relevant_file_ids = seed_ids | set(sorted_neighbors[:max(0, remaining)])

    file_info = _load_file_info(graph_store, relevant_file_ids)
    symbols = _load_symbols(graph_store, relevant_file_ids)
    selected_symbols, selected_symbol_ids = _select_symbols(
        symbols, seed_ids, max_nodes=max_files * max_symbols_per_file - len(relevant_file_ids)
    )
    call_edges = _load_symbol_calls(graph_store, selected_symbol_ids)
    import_edges = _load_import_edges(graph_store, relevant_file_ids)

    lines: list[str] = []
    lines.append("## Focused Code Graph")
    lines.append("")

    # Files and their symbols.
    for fid in sorted(relevant_file_ids, key=lambda f: file_info.get(f, {}).get("path", f)):
        info = file_info.get(fid)
        if not info:
            continue
        path = info["path"]
        marker = " (seed)" if fid in seed_ids else ""
        lines.append(f"### File: {path}{marker}")

        file_symbols = [s for s in selected_symbols.values() if s["file_id"] == fid]
        file_symbols.sort(key=lambda s: (s["symbol_type"] != "class", s["start_line"]))

        if file_symbols:
            lines.append("Symbols:")
            for sym in file_symbols:
                kind = sym["symbol_type"]
                parent = f" (method of {sym['parent_class']})" if sym.get("parent_class") else ""
                lines.append(f"  - {kind}: {sym['name']}{parent} (lines {sym['start_line']}-{sym['end_line']})")
        lines.append("")

    # Import relationships.
    if import_edges:
        lines.append("### File Import Relationships")
        for edge in sorted(import_edges, key=lambda e: (e["source"], e["target"])):
            src = file_info.get(edge["source"], {}).get("path", edge["source"])
            tgt = file_info.get(edge["target"], {}).get("path", edge["target"])
            lines.append(f"- {src} imports {tgt}")
        lines.append("")

    # Symbol call relationships.
    if call_edges:
        lines.append("### Symbol Call Relationships")
        for edge in sorted(call_edges, key=lambda e: (e["source"], e["target"])):
            src_sym = selected_symbols.get(edge["source"])
            tgt_sym = selected_symbols.get(edge["target"])
            if not src_sym or not tgt_sym:
                continue
            src_file = file_info.get(src_sym["file_id"], {}).get("path", src_sym["file_id"])
            tgt_file = file_info.get(tgt_sym["file_id"], {}).get("path", tgt_sym["file_id"])
            lines.append(f"- {src_sym['name']} in {src_file} calls {tgt_sym['name']} in {tgt_file}")
        lines.append("")

    return "\n".join(lines)


def _load_file_info(graph_store: GraphStore, file_ids: set[str]) -> dict[str, dict]:
    """Load path/module info for the given file IDs."""
    if not file_ids:
        return {}
    placeholders = ", ".join(["?"] * len(file_ids))
    rows = graph_store.conn.execute(
        f"SELECT file_id, path, module FROM files WHERE file_id IN ({placeholders})",
        list(file_ids),
    ).fetchall()
    return {row[0]: {"path": row[1], "module": row[2]} for row in rows}


def _expand_import_neighbors(graph_store: GraphStore, seed_ids: set[str]) -> set[str]:
    """Return file IDs one import hop away from seed files."""
    if not seed_ids:
        return set()

    placeholders = ", ".join(["?"] * len(seed_ids))
    query = f"""
        SELECT DISTINCT i.target_file_id
        FROM imports i
        WHERE i.source_file_id IN ({placeholders})
          AND i.target_file_id NOT IN ({placeholders})
    """
    params = list(seed_ids) + list(seed_ids)
    rows = graph_store.conn.execute(query, params).fetchall()
    return {row[0] for row in rows}


def _load_symbols(graph_store: GraphStore, file_ids: set[str]) -> dict[str, dict]:
    """Load symbols for the given file IDs keyed by symbol_id."""
    if not file_ids:
        return {}

    placeholders = ", ".join(["?"] * len(file_ids))
    rows = graph_store.conn.execute(
        f"""
        SELECT symbol_id, name, qualified_name, file_id, symbol_type, start_line, end_line, parent_class
        FROM symbols
        WHERE file_id IN ({placeholders})
        ORDER BY start_line
        """,
        list(file_ids),
    ).fetchall()

    symbols = {}
    for row in rows:
        symbols[row[0]] = {
            "symbol_id": row[0],
            "name": row[1],
            "qualified_name": row[2],
            "file_id": row[3],
            "symbol_type": row[4],
            "start_line": row[5],
            "end_line": row[6],
            "parent_class": row[7],
        }
    return symbols


def _select_symbols(
    symbols: dict[str, dict],
    seed_file_ids: set[str],
    max_nodes: int,
) -> tuple[dict[str, dict], set[str]]:
    """Prioritize symbols from seed files, then cap total count."""
    def is_noisy(sym: dict) -> bool:
        name = sym["name"]
        return name == "__init__" or name.startswith("__") or name.startswith("_")

    eligible = [s for s in symbols.values() if not is_noisy(s)]

    seed_symbols = [s for s in eligible if s["file_id"] in seed_file_ids]
    neighbor_symbols = [s for s in eligible if s["file_id"] not in seed_file_ids]

    def sort_key(sym: dict):
        is_method = bool(sym.get("parent_class"))
        is_class = sym["symbol_type"] == "class"
        return (is_method, not is_class, sym["name"])

    seed_symbols.sort(key=sort_key)
    neighbor_symbols.sort(key=sort_key)

    selected = seed_symbols + neighbor_symbols

    per_file_count: dict[str, int] = {}
    filtered = []
    for sym in selected:
        fid = sym["file_id"]
        per_file_count[fid] = per_file_count.get(fid, 0) + 1
        if per_file_count[fid] > MAX_SYMBOLS_PER_FILE:
            continue
        filtered.append(sym)
        if len(filtered) >= max_nodes:
            break

    selected_dict = {s["symbol_id"]: s for s in filtered}
    return selected_dict, set(selected_dict.keys())


def _load_symbol_calls(graph_store: GraphStore, symbol_ids: set[str]) -> list[dict]:
    """Load call edges where both caller and callee are selected symbols."""
    if not symbol_ids:
        return []

    placeholders = ", ".join(["?"] * len(symbol_ids))
    query = f"""
        SELECT caller_symbol_id, callee_symbol_id
        FROM symbol_calls
        WHERE caller_symbol_id IN ({placeholders})
          AND callee_symbol_id IN ({placeholders})
    """
    params = list(symbol_ids) + list(symbol_ids)
    rows = graph_store.conn.execute(query, params).fetchall()

    edges = []
    seen = set()
    for caller_id, callee_id in rows:
        key = (caller_id, callee_id)
        if key in seen:
            continue
        seen.add(key)
        edges.append({"source": caller_id, "target": callee_id, "type": "calls"})
    return edges


def _build_unified_graph(
    seed_files: dict[str, dict],
    relevant_file_ids: set[str],
    selected_symbols: dict[str, dict],
    selected_symbol_ids: set[str],
    call_edges: list[dict],
    graph_store: GraphStore,
) -> tuple[list[dict], list[dict]]:
    """Build unified nodes with parentId hierarchy and only explicit edges."""
    file_info = _load_file_info(graph_store, relevant_file_ids)

    nodes: list[dict] = []
    node_ids: set[str] = set()
    edges: list[dict] = []

    # File nodes (parents).
    for fid in relevant_file_ids:
        info = file_info.get(fid)
        if not info:
            continue
        path = info["path"]
        nodes.append({
            "id": fid,
            "type": "file",
            "name": path.split("/")[-1],
            "full_path": path,
            "module": info.get("module"),
            "is_seed": fid in seed_files,
        })
        node_ids.add(fid)

    # Class nodes (children of files, parents of methods).
    class_nodes = []
    for sym_id, sym in selected_symbols.items():
        if sym["symbol_type"] != "class":
            continue
        fid = sym["file_id"]
        if fid not in node_ids:
            continue
        class_nodes.append({
            "id": sym_id,
            "type": "class",
            "name": sym["name"],
            "full_path": file_info.get(fid, {}).get("path", ""),
            "qualified_name": sym["qualified_name"],
            "start_line": sym["start_line"],
            "end_line": sym["end_line"],
            "parentId": fid,
            "file_id": fid,
        })
        node_ids.add(sym_id)

    nodes.extend(class_nodes)
    class_ids = {n["id"] for n in class_nodes}

    # Function / method nodes.
    for sym_id, sym in selected_symbols.items():
        if sym["symbol_type"] == "class":
            continue
        fid = sym["file_id"]
        if fid not in node_ids:
            continue

        parent_class = sym.get("parent_class")
        parent_id = None
        if parent_class:
            # Find the selected class node with this name in the same file.
            for cid in class_ids:
                cls = selected_symbols.get(cid)
                if cls and cls["file_id"] == fid and cls["name"] == parent_class:
                    parent_id = cid
                    break

        node_type = "method" if parent_id else "function"
        nodes.append({
            "id": sym_id,
            "type": node_type,
            "name": sym["name"],
            "full_path": file_info.get(fid, {}).get("path", ""),
            "qualified_name": sym["qualified_name"],
            "start_line": sym["start_line"],
            "end_line": sym["end_line"],
            "parentId": parent_id or fid,
            "file_id": fid,
        })
        node_ids.add(sym_id)

    # File import edges.
    import_edges = _load_import_edges(graph_store, relevant_file_ids)
    for edge in import_edges:
        if edge["source"] in node_ids and edge["target"] in node_ids:
            edges.append(edge)

    # Symbol call edges.
    for edge in call_edges:
        if edge["source"] in node_ids and edge["target"] in node_ids:
            edges.append(edge)

    return nodes, edges


def _load_import_edges(graph_store: GraphStore, file_ids: set[str]) -> list[dict]:
    """Load file import edges limited to the selected file set."""
    if not file_ids:
        return []

    placeholders = ", ".join(["?"] * len(file_ids))
    query = f"""
        SELECT source_file_id, target_file_id
        FROM imports
        WHERE source_file_id IN ({placeholders})
          AND target_file_id IN ({placeholders})
    """
    params = list(file_ids) + list(file_ids)
    rows = graph_store.conn.execute(query, params).fetchall()

    edges = []
    seen = set()
    for source_id, target_id in rows:
        key = (source_id, target_id)
        if key in seen:
            continue
        seen.add(key)
        edges.append({"source": source_id, "target": target_id, "type": "imports"})
    return edges


def _build_file_only_diagram(
    seed_files: dict[str, dict],
    neighbor_ids: set[str],
    graph_store: GraphStore,
) -> tuple[list[dict], list[dict]]:
    """Fallback when no symbols are available: file nodes + import edges."""
    relevant = set(seed_files.keys()) | neighbor_ids
    file_info = _load_file_info(graph_store, relevant)

    nodes = []
    node_ids = set()
    for fid, info in file_info.items():
        nodes.append({
            "id": fid,
            "type": "file",
            "name": info["path"].split("/")[-1],
            "full_path": info["path"],
            "module": info.get("module"),
            "is_seed": fid in seed_files,
        })
        node_ids.add(fid)

    edges = _load_import_edges(graph_store, node_ids)
    return nodes, edges


def _compute_levels(nodes: list[dict], edges: list[dict]) -> list[dict]:
    """Assign a coarse topological level to each file (and inherit to children)."""
    file_nodes = [n for n in nodes if n["type"] == "file"]
    file_ids = {n["id"] for n in file_nodes}

    file_edges = [
        (e["source"], e["target"])
        for e in edges
        if e["type"] == "imports" and e["source"] in file_ids and e["target"] in file_ids
    ]

    outgoing: dict[str, list[str]] = {fid: [] for fid in file_ids}
    in_degree: dict[str, int] = {fid: 0 for fid in file_ids}

    for source, target in file_edges:
        outgoing[source].append(target)
        in_degree[target] += 1

    queue = deque([fid for fid in file_ids if in_degree[fid] == 0])
    level: dict[str, int] = {fid: 0 for fid in file_ids}
    processed: set[str] = set()

    while queue:
        source = queue.popleft()
        processed.add(source)
        for target in outgoing[source]:
            level[target] = max(level[target], level[source] + 1)
            in_degree[target] -= 1
            if in_degree[target] == 0:
                queue.append(target)

    for fid in file_ids:
        if fid not in processed:
            pred_level = -1
            for source, target in file_edges:
                if target == fid:
                    pred_level = max(pred_level, level[source])
            level[fid] = max(level[fid], pred_level + 1)

    for node in nodes:
        fid = node["id"] if node["type"] == "file" else node.get("file_id")
        node["level"] = level.get(fid, 0)

    return nodes
