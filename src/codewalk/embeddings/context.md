# `src/codewalk/embeddings/` — Chunking, Embedding & Vector Storage

This package transforms raw source files into searchable vector chunks stored in ChromaDB.

## Modules

| File | Role |
|------|------|
| `chunker.py` | `chunk_file()` — tree-sitter AST chunking (parent/child chunks) with a text-splitter fallback; `file_hash()`, `read_file_content()`. |
| `embedder.py` | `embed_chunks()` — runs the sentence-transformer model (default `jinaai/jina-code-embeddings-1.5b`). Parent chunks get zero embeddings. |
| `vector_store.py` | `VectorStore` — ChromaDB wrapper with parent/child collections, `search_with_parents()`, file-based deletion, hash tracking. |

## Data flow

```
file_info (from ingestion/)
    ↓
chunk_file() → list of chunks
    ↓
embed_chunks() → chunks with embedding vectors
    ↓
VectorStore.add_parent_child_chunks() → ChromaDB
```

## Parent/child storage model

- **Parent chunks** = whole symbols/functions/classes (context-only, fetched by ID).
- **Child chunks** = small searchable pieces with `parent_chunk_id` metadata.
- **Leftover chunks** = files that could not be parsed; stored as both parent and child.

## Connections

- Used by `pipeline.py` for full/incremental indexing.
- Used by `rag/chain.py` for retrieval and graph expansion.
- Used by `api/state.py` and `mcp/server.py` to load existing indexes.
- Doc/guideline stores in `doc_knowledge/` and `review/` reuse the same ChromaDB directory.

