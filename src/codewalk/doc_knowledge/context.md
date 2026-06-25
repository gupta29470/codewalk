# `src/codewalk/doc_knowledge/` — Documentation & Guidelines Indexing

This package indexes Markdown/text/PDF documentation and team coding guidelines into ChromaDB collections so review and ask endpoints can ground answers in them.

## Modules

| File | Role |
|------|------|
| `doc_parser.py` | `parse_doc()`, `parse_all_docs()`, `discover_docs()` — parses `.md`, `.txt`, `.pdf`, `.rst` files into plain text + metadata. |
| `doc_store.py` | `DocStore` — ChromaDB wrapper for docs with section-aware chunking and metadata (`doc_path`, `section`). |
| `prompts.py` | Prompt template for grounded doc Q&A. |

## Data flow

```
folder of .md/.txt/.pdf/.rst
    ↓
doc_parser.parse_all_docs() → parsed docs
    ↓
DocStore.index_docs() → chunks with metadata
    ↓
ChromaDB collection {collection_name}_docs
    ↓
DocStore.search() → results for /docs/ask or review context
```

## Connections

- Called by `pipeline.build_full_analysis()` when `docs_path` is provided.
- Called by API `/docs/index`, `/docs/search`, `/docs/ask`.
- Called by MCP `codewalk_index_docs`, `codewalk_search_docs`, `codewalk_ask_docs`.
- Guidelines for reviews are loaded via `review/utils.py` (`load_code_guidelines_text`) and `embeddings/vector_store.py`; the old `review/guidelines_loader.py` no longer exists.

## Recent fixes

- `pipeline.build_full_analysis()` now accepts a `collection_name` parameter; docs are stored in `{collection_name}_docs` so the doc collection always matches the active code collection (previously it used the repo folder name and could diverge).
