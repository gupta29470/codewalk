# `src/codewalk/analysis/` — Static Analysis

This package parses source files and derives dependency graphs, module structure, and reading order.

## Modules

| File | Role |
|------|------|
| `dependency_graph.py` | `build_dependency_graph()` — tree-sitter import extraction + language-specific resolution (Python, JS/TS, Dart, Java, Go, Rust, Ruby, PHP, C/C++, C#, Kotlin, Swift). |
| `module_detector.py` | `detect_modules()` — groups files into modules based on folder structure and import graph. |
| `code_parser.py` | `parse_file()` / `get_parser_for_language()` — tree-sitter parser registry and symbol extraction. |
| `reading_order.py` | `generate_reading_order()` — topological sort + LLM tagging of must-read/optional/skip files. |
| `blast_radius.py` | `calculate_full_blast_map()` — PageRank / centrality risk scoring per file. |
| `relevance_filter.py` | Filters module/symbol lists by query relevance. |
| `parsers/` | Language-specific parser helpers (Python, JS/TS, Go, etc.). |

## Data flow

```
scanned files (ingestion/)
    ↓
dependency_graph.py → deps {"graph": {...}, "stats": {...}}
    ↓
module_detector.py → modules_result {"modules": {...}, "module_graph": {...}}
    ↓
graph/graph_store.py, query/, generation/, rag/
```

## Connections

- `dependency_graph.py` is called by `pipeline.py`, `api/state.py`, `query/` helpers, and the MCP server.
- `module_detector.py` output drives `generation/diagram_generator.py` and `query/` module lookups.
- `reading_order.py` is exposed via API/MCP reading-order tools.
- `blast_radius.py` feeds review and overview generation.

## Recent fixes

- `dependency_graph.py` now resolves Python relative imports (`from . import x`, `from .pkg import y`, `from ..other import z`) using the source file's directory.
- `dependency_graph.py` now captures every name in multi-name Python imports (`import os, sys, json`).
- `dependency_graph.py` Go import resolution now matches by directory-suffix depth instead of substring containment.
- `reading_order.py` strips markdown code fences without dropping legitimate file content.

