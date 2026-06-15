# Knowledge Graph — Backend & Frontend Handoff

**What this doc is for:** everything after `knowledge-graph.json` exists — how to verify it, what the frontend must build, and what else (ops, API, MCP, standalone). Backend export is done; UI is next.

> **Inspiration:** [Understand-Anything dashboard](https://github.com/Egonex-AI/Understand-Anything) (`knowledge-graph.json` + React Flow). Codewalk uses the **same top-level schema**; no LLM required for the graph itself.

---

## 1. You have the JSON — verify first

### Local (after `POST /analyze` or MCP analyze)

```bash
ls -la {repo_path}/.codewalk/knowledge-graph.json
ls -la {repo_path}/.codewalk/manifest.json
```

`manifest.json` should include:

```json
{
  "has_knowledge_graph": true,
  "knowledge_graph_version": "1.0.0"
}
```

### Cloud (after server index + laptop pull)

```bash
# Server
ls -la /var/codewalk/indexes/owner/repo/knowledge-graph.json

# Laptop (target repo root)
ls -la .codewalk/knowledge-graph.json
```

Cloud tarball (`GET /indexes/{owner}/{repo}`) packs the whole `.codewalk/` folder — JSON is included automatically when present.

### API (local API with loaded index)

```bash
curl -s http://localhost:8000/knowledge-graph | python3 -m json.tool | head -30
```

404 → index loaded but JSON missing (old index or fast-path load only) → re-analyze once.

### Quick sanity on the file

```bash
python3 -c "
import json, sys
g=json.load(open('.codewalk/knowledge-graph.json'))
print('nodes', len(g['nodes']), 'edges', len(g['edges']), 'layers', len(g['layers']))
print('types', {n['type'] for n in g['nodes']})
print('edge types', {e['type'] for e in g['edges']})
"
```

---

## 2. Backend (done — reference only)

| Item | Location |
|------|----------|
| Export on index | `build_full_analysis()` → `knowledge_graph_export.py` |
| Output path | `{repo_path}/.codewalk/knowledge-graph.json` |
| API | `GET /knowledge-graph` (local query; blocked on cloud-only server) |
| Tests | `python -m unittest tests.test_knowledge_graph_export -v` |

**When JSON is regenerated:** every path that calls `build_full_analysis()` (analyze, refresh, MCP local embed, cloud webhook, CLI). **Not** on `index_mode: auto` fast-path load-only.

**Indexes from before this feature:** one re-index required (`prepare --index` on server or `POST /analyze` with `reindex`/`full`).

---

## 3. JSON schema (v1.0.0) — frontend must parse

### Top level

```json
{
  "version": "1.0.0",
  "project": { ... },
  "nodes": [ ... ],
  "edges": [ ... ],
  "layers": [ ... ],
  "stats": { ... }
}
```

### `project`

| Field | Use in UI |
|-------|-----------|
| `name` | Page title / header |
| `repoPath` | Subtitle (optional) |
| `languages` | Badge chips |
| `frameworks` | Badge chips (`tech_detect`, e.g. `python`) |
| `description` | Empty today — hide or placeholder |
| `analyzedAt` | “Last indexed” timestamp |
| `gitCommitHash` | Short SHA badge |
| `branch` | Branch badge |

### `nodes[]`

| `type` | `id` prefix | Required fields | UI |
|--------|-------------|-----------------|-----|
| `file` | `file:` | `name`, `filePath`, `language`, `module`, `complexity` | Box node, file icon |
| `function` | `function:` | `name`, `filePath`, `qualifiedName`, `lineRange` | Rounded node |
| `class` | `class:` | same | Different color/shape |
| `method` | `method:` | same | Same as function |
| `module` | `module:` | `name`, `filePaths[]` | Cluster / group header |

**Empty `summary` / minimal `tags`:** show `name` + `filePath` (basename). Do **not** require LLM text.

**Optional `metrics`:** `sizeBytes`, `importCount`, `importerCount`, `startLine`/`endLine` — detail panel only.

### `edges[]`

| `type` | Meaning | Suggested weight/color |
|--------|---------|------------------------|
| `imports` | file → file | Solid, primary |
| `calls` | symbol → symbol | Solid, accent (+ `line` in metadata) |
| `exports` | file → symbol | Thin |
| `related` | symbol ↔ symbol (same file, layout) | Dashed, low opacity |
| `module_dep` | module → module | Thick, between clusters |
| `contains` | module → file | Dotted |

All edges: `source`, `target`, `type`, `direction` (`forward`), `weight`.

### `layers[]`

| Field | Use |
|-------|-----|
| `id` | `layer:moduleName` |
| `name` | Filter tab / legend |
| `description` | Tooltip |
| `nodeIds` | Show/hide subgraph |

One layer per detected module (structural, not LLM-named).

### `stats`

Counts for header/footer: `nodeCount`, `edgeCount`, `files`, `symbols`, `modules`, etc.

---

## 4. Frontend — detailed build plan

Target: new page in `frontend/` (Next.js), UA-style interactive graph. **Demo always works**; real data when index exists.

### 4.1 Repo layout (add these)

```
frontend/
  public/
    demo/
      knowledge-graph.json     # Static demo (copy from UA or trim codewalk export)
  src/
    app/
      knowledge-graph/
        page.tsx               # Main graph page
    lib/
      knowledge-graph.ts       # Types + loader + React Flow helpers
    components/
      knowledge-graph/
        GraphCanvas.tsx        # React Flow wrapper
        NodeDetailPanel.tsx    # Selected node sidebar
        LayerFilter.tsx        # Toggle layers
        GraphSearch.tsx        # Filter nodes by name/path
```

### 4.2 Dependencies to add

```bash
cd frontend
npm install reactflow dagre
npm install -D @types/dagre   # if needed
```

(UA uses React Flow + Dagre layout — same stack.)

### 4.3 TypeScript types (`lib/knowledge-graph.ts`)

Mirror the JSON schema:

- `KnowledgeGraph`, `GraphNode`, `GraphEdge`, `GraphLayer`, `ProjectMeta`
- `GraphNodeType = 'file' | 'function' | 'class' | 'method' | 'module'`
- `GraphEdgeType = 'imports' | 'calls' | 'exports' | 'related' | 'module_dep' | 'contains'`

Add a type guard / validator (optional): check `version === '1.0.0'` and required keys before render.

### 4.4 Data loading strategy (required behavior)

```
on mount:
  1. If Codewalk API analyzed (existing analyze-context):
       try GET {API_BASE}/knowledge-graph
       if 200 → use response, set source = "index"
  2. Else if fetch failed / 404:
       fetch /demo/knowledge-graph.json
       set source = "demo"
  3. Show banner: "Demo data" vs "Live index: {project.name}"
```

**Do not** read `file://` paths from the browser — use API or `public/demo/` static file only.

**Cloud users:** run the local API, download the index with MCP (`codewalk_pull_index` / `codewalk_connect_repo`), then `GET /knowledge-graph`. Or copy `.codewalk/knowledge-graph.json` into `public/demo/` manually for offline dev.

### 4.5 React Flow conversion

1. **Nodes:** map each `nodes[]` entry → React Flow node  
   - `id` = graph `id` (unique)  
   - `data.label` = `name` or basename of `filePath`  
   - `data.nodeType` = `type`  
   - Custom node component per `type` (color/shape)

2. **Edges:** map each `edges[]` → React Flow edge  
   - `id` = `${source}-${target}-${type}`  
   - `animated` for `calls` optional  
   - `style` / `strokeDasharray` by `type`

3. **Layout:** run **Dagre** (directed) on filtered subgraph  
   - Filter by selected layer’s `nodeIds` for large repos  
   - Default view: **module layer** or **file-only** (hide symbols) for performance

### 4.6 UI features (MVP → polish)

| Feature | Priority | Notes |
|---------|----------|-------|
| Pan / zoom | MVP | React Flow default |
| Node click → detail panel | MVP | path, type, lineRange, metrics |
| Layer filter | MVP | Use `layers[].nodeIds` |
| Search by name/path | MVP | Filter visible nodes |
| Edge type legend | MVP | Toggle edge types on/off |
| “File only” mode | High | Drop symbol nodes for big repos |
| Open in editor link | Nice | `filePath:line` if you have IDE URL scheme |
| Minimap | Nice | React Flow `MiniMap` |
| Tours / LLM summaries | Later | Not in JSON today |

### 4.7 Wire into existing app

1. **`frontend/src/lib/api.ts`** — add:

   ```ts
   getKnowledgeGraph(): Promise<KnowledgeGraph>
   ```

2. **`Sidebar.tsx`** — add nav item:

   ```ts
   { href: "/knowledge-graph", label: "Knowledge Graph", icon: Share2, locked: true }
   ```

   `locked: true` until `analyze-context` has result (same as Architecture).

3. **`analyze-context`** — no change required if loader uses `GET /knowledge-graph` after analyze.

4. **Home page** — optional card: “Explore codebase graph” → `/knowledge-graph`.

### 4.8 Demo JSON

- Copy [UA sample](https://github.com/Egonex-AI/Understand-Anything/blob/main/understand-anything-plugin/packages/dashboard/public/knowledge-graph.json) into `frontend/public/demo/knowledge-graph.json` **or**
- Export a small codewalk repo and trim nodes to ~20 for fast dev.

Demo must match schema `1.0.0` so the same components render both sources.

### 4.9 Performance notes

| Repo size | Approach |
|-----------|----------|
| Small (&lt;100 files) | Render all nodes |
| Medium | Default: files + modules; expand file to show symbols |
| Large | Layer filter + file-only; limit symbols per file in view |

`stats.nodeCount` in header — warn if &gt; 500 nodes.

---

## 5. What else (beyond the React page)

### 5.1 Ops / index lifecycle

| Task | Who |
|------|-----|
| Deploy backend with export code | Server / CI |
| Re-index once | `./deploy/reset-repo.sh prepare owner/repo --index` |
| Laptop pull | MCP `codewalk_pull_index` or `codewalk_connect_repo` |
| Confirm manifest | `has_knowledge_graph: true` |

After every **incremental** index, JSON is **rewritten** (full export from DuckDB).

### 5.2 Local dev workflow

```bash
# Terminal 1 — start the API
.codewalk-env/bin/uvicorn src.codewalk.api.main:app --reload

# Then analyze the target repo (via the web UI or curl):
curl -X POST http://localhost:8000/analyze \
  -H "Content-Type: application/json" \
  -d '{"repo_path": "/path/to/target/repo", "index_mode": "auto"}'

# Terminal 2 — frontend
cd frontend && npm run dev
# Open http://localhost:3000/knowledge-graph
```

### 5.3 Optional later (not required for MVP)

| Item | Description |
|------|-------------|
| **MCP tool** `codewalk_get_knowledge_graph` | Return path or truncated JSON for agents |
| **Standalone static page** | `frontend/public/graph-standalone.html` — load JSON file via file input (no Next) |
| **Embed in README** | Screenshot + link to graph page |
| **LLM enrichment** | Fill `summary` / `tags` / layer titles (optional phase 2) |
| **Stale detection** | Compare `manifest.index_version` vs last loaded in UI |

### 5.4 What NOT to do

- Don’t call cloud `api.codewalk.xyz/knowledge-graph` — endpoint is **disabled** in cloud-only mode.
- Don’t parse node `id` prefix alone — use `type` field (`class:` vs `function:` both valid).
- Don’t require `summary` — empty is normal.

---

## 6. Frontend acceptance checklist

- [ ] `public/demo/knowledge-graph.json` loads when API returns 404
- [ ] After `POST /analyze`, live graph loads from `GET /knowledge-graph`
- [ ] Banner shows demo vs live source
- [ ] File / function / class nodes render differently
- [ ] Edge types visually distinct (`imports`, `calls`, `related`, …)
- [ ] Layer filter reduces visible nodes
- [ ] Search finds node by name or path
- [ ] Click node shows detail (path, lines, metrics)
- [ ] Sidebar link locked until analyzed
- [ ] Large graph: file-only or layer filter prevents browser freeze

---

## 7. Backend tests

```bash
.codewalk-env/bin/python -m unittest tests.test_knowledge_graph_export -v
```

Note: `tests/` is gitignored today — run locally or move tests under `src/` if you want them in CI.

---

## 8. Alignment checklist (index paths)

| Path | Generates `knowledge-graph.json`? |
|------|-----------------------------------|
| `build_full_analysis()` | Yes |
| API `POST /analyze` → `initialize()` | Yes |
| API `POST /refresh` / `rebuild_analysis_cache` | Yes |
| MCP `codewalk_analyze_codebase` (local embed) | Yes |
| Cloud webhook / `admin/index` | Yes |
| CLI index | Yes |
| Fast path `load_scoped_analysis` only | No — uses existing file if present |
| Legacy `worker/indexer.py` | Yes |

---

## 9. Related docs

- [SERVER_OPS.md](../deploy/SERVER_OPS.md) — re-index, permissions
- [README.md](../README.md) — API overview
- [frontend/README.md](../frontend/README.md) — Next.js app (update when graph page ships)
