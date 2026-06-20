# Why DuckDB? (Not SQLite)

> A junior-engineer-friendly explanation of why Codewalk uses DuckDB instead of SQLite for its graph store.

---

## The One-Sentence Answer

**DuckDB reads only the columns your query needs. SQLite reads every column in every row, then throws away the ones it doesn't need.**

---

## What Each Database Is Built For

| | SQLite | DuckDB |
|---|---|---|
| **Design** | Row store (OLTP) | Column store (OLAP) |
| **Built for** | "Get me user #12345" (one row, all columns) | "Count orders per region" (many rows, few columns) |
| **Best at** | Point lookups, single-row inserts, CRUD apps | Scans, JOINs, GROUP BY, bulk inserts |
| **Used by** | Mobile apps, browsers, small web apps | Analytics, data science, Codewalk |
| **Setup** | `import sqlite3` | `pip install duckdb` |
| **File** | `.sqlite` | `.duckdb` |
| **Server needed?** | No | No |

Both are single-file, embedded, no-server databases. The SQL syntax is nearly identical. The difference is **how they store data on disk**.

---

## How Data Is Stored — The Core Difference

### Codewalk's `symbols` Table (7 columns, 105 rows in `fatih/color`)

| symbol_id | name | qualified_name | file_id | symbol_type | start_line | end_line |
|---|---|---|---|---|---|---|
| ab367af4.. | noColorIsSet | color/color.go:noColorIsSet | e7841368.. | function | 39 | 41 |
| c7bc74fb.. | stdoutIsTerminal | color/color.go:stdoutIsTerminal | e7841368.. | function | 45 | 50 |
| 79dc1af6.. | stdOut | color/color.go:stdOut | e7841368.. | function | 54 | 59 |
| 9dfa5a86.. | New | color/color.go:New | e7841368.. | function | 173 | 184 |
| 2cb7a82c.. | RGB | color/color.go:RGB | e7841368.. | function | 187 | 189 |

### SQLite: Stores data ROW by ROW

All 7 columns of a row are packed together, one after another on disk:

```
SQLite file on disk:

  Byte 0-80:    [ab367af4.., noColorIsSet, color/color.go:noColorIsSet, e7841368.., function, 39, 41]
  Byte 81-170:  [c7bc74fb.., stdoutIsTerminal, color/color.go:stdoutIsTerminal, e7841368.., function, 45, 50]
  Byte 171-250: [79dc1af6.., stdOut, color/color.go:stdOut, e7841368.., function, 54, 59]
  Byte 251-340: [9dfa5a86.., New, color/color.go:New, e7841368.., function, 173, 184]
  ...

  Each row = ALL 7 columns packed next to each other.
  You CANNOT read column 4 (file_id) without reading past columns 1, 2, 3 first.
```

### DuckDB: Stores data COLUMN by COLUMN

Each column is stored in its own separate block on disk:

```
DuckDB file on disk:

  Byte 0-500:      [ab367af4.., c7bc74fb.., 79dc1af6.., 9dfa5a86.., ...]     ← ALL symbol_ids
  Byte 501-800:    [noColorIsSet, stdoutIsTerminal, stdOut, New, RGB, ...]     ← ALL names
  Byte 801-2000:   [color/color.go:noColorIsSet, color/color.go:stdout..]     ← ALL qualified_names
  Byte 2001-2500:  [e7841368.., e7841368.., e7841368.., e7841368.., ...]      ← ALL file_ids
  Byte 2501-2800:  [function, function, function, function, ...]              ← ALL symbol_types
  Byte 2801-3000:  [39, 45, 54, 173, 187, ...]                               ← ALL start_lines
  Byte 3001-3200:  [41, 50, 59, 184, 189, ...]                               ← ALL end_lines

  Each column = separate location on disk.
  To read file_ids, jump directly to byte 2001. Bytes 0-2000 are NEVER touched.
```

**Key insight**: Position 0 in every column = row 1. Position 3 in every column = row 4 (`New`). DuckDB uses positions to reassemble rows when needed.

---

## Concrete Example: A Real Codewalk Query

### The Query

When you run `@codewalk explain New`, Codewalk needs to find all symbols in `color/color.go`:

```sql
SELECT name, start_line, end_line
FROM symbols
WHERE file_id = 'e7841368..'
```

This query needs **3 columns** (file_id to filter, name + start_line + end_line to return) out of **7 total**.

### What SQLite Does

```
Step 1: Read row 1 from disk
        → loads ALL 7 values: [ab367af4.., noColorIsSet, color/color.go:noColorIsSet,
                                e7841368.., function, 39, 41]
        → check file_id (column 4): "e7841368.." = "e7841368.." ✅ match!
        → return name="noColorIsSet", start=39, end=41
        → DISCARD symbol_id, qualified_name, symbol_type (3 values wasted)

Step 2: Read row 2 from disk
        → loads ALL 7 values: [c7bc74fb.., stdoutIsTerminal, color/color.go:stdoutIs...,
                                e7841368.., function, 45, 50]
        → check file_id: match ✅
        → return name="stdoutIsTerminal", start=45, end=50
        → DISCARD 3 values again

Step 3: Read row 3... same thing
Step 4: Read row 4... same thing
...
Step 105: Read row 105... same thing

TOTAL VALUES LOADED FROM DISK:  105 rows × 7 columns = 735 values
TOTAL VALUES ACTUALLY USED:     105 × 1 (filter) + ~80 × 3 (return) = 345 values
WASTED:                         390 values (~53%)
```

### What DuckDB Does

```
Step 1: Read the file_id column (byte 2001-2500)
        → loads 105 file_ids: [e7841368.., e7841368.., e7841368.., ...]
        → filter: positions [0, 1, 2, 3, 4, ...79] match "e7841368.."
        (columns symbol_id, qualified_name, symbol_type are NEVER loaded — they're
         at bytes 0-2000 and 2501-2800, and DuckDB never seeks there)

Step 2: Read the name column at matched positions only
        → loads: [noColorIsSet, stdoutIsTerminal, stdOut, New, RGB, ...]

Step 3: Read the start_line column at matched positions only
        → loads: [39, 45, 54, 173, 187, ...]

Step 4: Read the end_line column at matched positions only
        → loads: [41, 50, 59, 184, 189, ...]

TOTAL VALUES LOADED FROM DISK:  105 (filter) + 80 × 3 (return) = 345 values
TOTAL VALUES ACTUALLY USED:     345 values
WASTED:                         0 values (0%)
```

### Side-by-Side

| | SQLite | DuckDB |
|---|---|---|
| Columns loaded from disk | 7 (all of them) | 4 (only file_id, name, start_line, end_line) |
| Columns needed by query | 4 | 4 |
| Columns wasted | 3 (symbol_id, qualified_name, symbol_type) | 0 |
| Values loaded (105 rows) | 735 | 345 |
| Waste | 53% | 0% |

---

## Actual Benchmark: SQLite vs DuckDB (Real Numbers)

We ran the **exact same 4 queries** on both databases, at 3 different data sizes.
Same data, same schema, same machine. Times in **microseconds (μs)** — lower is better.

### Small Repo: 200 files, 3,000 symbols, 9,000 calls

| Query | SQLite (μs) | DuckDB (μs) | Winner |
|---|---|---|---|
| `SELECT name FROM symbols` (1 of 7 cols) | 498 | 292 | **DuckDB 1.7x** |
| `WHERE file_id = X` (filter + 2 cols) | 79 | 82 | **Tie** |
| 3-table JOIN (`get_import_edges`) | 383 | 443 | **SQLite 1.2x** |
| JOIN + GROUP BY + ORDER (analytics) | 4,363 | 721 | **DuckDB 6x** |

At small scale, it's a toss-up. DuckDB wins analytics, SQLite wins simple JOINs.

### Medium Repo: 2,000 files, 30,000 symbols, 90,000 calls

| Query | SQLite (μs) | DuckDB (μs) | Winner |
|---|---|---|---|
| `SELECT name FROM symbols` (1 of 7 cols) | 5,602 | 3,097 | **DuckDB 1.8x** |
| `WHERE file_id = X` (filter + 2 cols) | 756 | 124 | **DuckDB 6x** |
| 3-table JOIN (`get_import_edges`) | 5,095 | 1,889 | **DuckDB 2.7x** |
| JOIN + GROUP BY + ORDER (analytics) | 66,355 | 4,287 | **DuckDB 15.5x** |

At 2,000 files, DuckDB wins every query. The analytical query is **15x faster**.

### Large Repo: 20,000 files, 300,000 symbols, 900,000 calls

| Query | SQLite (μs) | DuckDB (μs) | Winner |
|---|---|---|---|
| `SELECT name FROM symbols` (1 of 7 cols) | 60,093 | 33,280 | **DuckDB 1.8x** |
| `WHERE file_id = X` (filter + 2 cols) | 7,595 | 253 | **DuckDB 30x** |
| 3-table JOIN (`get_import_edges`) | 66,761 | 18,134 | **DuckDB 3.7x** |
| JOIN + GROUP BY + ORDER (analytics) | 1,096,088 | 20,488 | **DuckDB 53.5x** |

At 20,000 files (real production monorepo), the analytical query takes **1.1 seconds** in SQLite vs **20ms** in DuckDB. That's **53x faster**.

### What Codewalk Actually Indexes (for reference)

| Repo | Files | Symbols | Calls |
|---|---|---|---|
| `fatih/color` (small Go lib) | 9 | 105 | 348 |
| `tj/commander.js` (medium JS lib) | 198 | 277 | 478 |
| Production monorepo (target) | 5,000-20,000+ | 50,000-300,000+ | 150,000-900,000+ |

### The Pattern

```
                SQLite faster ←──────────→ DuckDB faster

  200 files:    ████░░░░░░    (mixed — some queries SQLite wins)
  2,000 files:  ░░████████    (DuckDB wins everything, 2-15x)
  20,000 files: ░░████████████████████  (DuckDB wins everything, 2-53x)

  The gap WIDENS with more data. DuckDB's columnar engine has startup overhead,
  but once data is large enough, it processes columns much faster than SQLite
  processes rows.
```

### Honest Admission: At Codewalk's Current Scale

With `commander.js` (277 symbols), **SQLite is actually faster** — DuckDB's columnar engine has fixed overhead per query (~80μs) that dominates at tiny data sizes:

| Query | SQLite (μs) | DuckDB (μs) | Winner |
|---|---|---|---|
| All queries at 277 symbols | 12-163 | 78-479 | **SQLite wins** |

But both finish in **under 0.5ms** — imperceptible to a human. The choice is about **which direction scales better** as users index larger repos. DuckDB scales to 300,000 symbols without breaking a sweat. SQLite starts struggling at 30,000+.

### Why the Gap Exists

The benchmark proves the theory from the previous section:

- **Q1 (`SELECT name` — 1 of 7 columns)**: DuckDB reads 1 column. SQLite reads 7. Gap = 1.8x at all sizes.
- **Q2 (`WHERE file_id = X` — filter scan)**: DuckDB scans 1 column to filter. SQLite scans entire rows. Gap grows from 1x → 6x → **30x** as rows increase.
- **Q4 (JOIN + GROUP BY)**: DuckDB's vectorized engine processes batches of values. SQLite processes row-at-a-time. Gap grows from 6x → 15x → **53x**.

**The wider the table and the more rows, the bigger DuckDB's advantage.**

---

## Where DuckDB Is Used in Codewalk (4 Scenarios)

### Scenario 1: Search — Graph Expansion

**When**: User searches `@codewalk how does authentication work?` and ChromaDB returns weak results.

**What happens**: Codewalk looks up neighboring files (imports + importers) via DuckDB, then searches within those neighbors for better results.

```python
# graph_expansion.py — called during corrective RAG
for fp in source_files:
    for imported in graph_store.get_imports(fp):       # ← DuckDB query
        neighbor_files.add(imported)
    for importer in graph_store.get_importers(fp):     # ← DuckDB query
        neighbor_files.add(importer)
```

**DuckDB query that runs**:
```sql
-- "What files does auth/login.py import?"
SELECT f.path FROM imports i
JOIN files f ON i.target_file_id = f.file_id
WHERE i.source_file_id = 'a3f8c1...'
→ ["auth/session.py", "models/user.py"]

-- "What files import auth/login.py?"
SELECT f.path FROM imports i
JOIN files f ON i.source_file_id = f.file_id
WHERE i.target_file_id = 'a3f8c1...'
→ ["routes/api.py", "middleware/auth_check.py"]
```

**Without DuckDB**: Graph expansion can't happen. Stuck with weak ChromaDB results. Answer quality drops.

---

### Scenario 2: Explain Function — Show Callers and Callees

**When**: User asks `@codewalk explain New`

**What happens**: After finding the function via ChromaDB, Codewalk queries DuckDB for who calls it and what it calls.

```python
# query.py — called by codewalk_explain_function
callers = graph_store.get_callers_of_symbol(qualified_name)  # ← DuckDB
callees = graph_store.get_callees_of_symbol(qualified_name)  # ← DuckDB
```

**DuckDB query that runs**:
```sql
-- "Who calls New()?"
SELECT cs.name, cf.path, sc.line
FROM symbol_calls sc
JOIN symbols cs ON sc.caller_symbol_id = cs.symbol_id
JOIN files cf ON cs.file_id = cf.file_id
WHERE sc.callee_symbol_id = '9dfa5a86..'

→ caller       file              line
  RGB          color/color.go    188
  BgRGB        color/color.go    193
  Set          color/color.go    213
```

**What the user sees**:
```
New() creates a Color object.
Called by:
  - RGB() at color/color.go:188
  - BgRGB() at color/color.go:193
  - Set() at color/color.go:213
Calls:
  - noColorIsSet() at line 178
  - boolPtr() at line 179
  - Add() at line 182
```

**Without DuckDB**: The LLM only sees the function body. Can't tell you who calls it or what it calls.

---

### Scenario 3: Code Review — Find Broken Callers

**When**: User runs `@codewalk review my changes` and the diff shows `New()`'s signature changed.

**What happens**: Codewalk finds which symbols were changed, then queries DuckDB for their callers to warn about breaking changes.

**Applying fixes (MCP):** Review is agent-driven — your IDE agent calls `codewalk_review_diff` / `codewalk_reflect_review`. Each proposed fix goes through `codewalk_approve_action`; you approve or reject in your **host's UI** (Cursor cards, chat, etc.). Only after approval does the agent call `codewalk_apply_fix` with the returned `approval_token`. See [README.md](../README.md) § “Review & approve fixes”.

```python
# reviewer.py — called by codewalk_review_diff
symbols = graph_store.get_symbols_in_file(diff_file.file_path)  # ← DuckDB
for symbol in symbols:
    if changed_lines & symbol_line_range:
        callers = graph_store.get_callers_of_symbol(symbol["qualified_name"])  # ← DuckDB
```

**DuckDB queries that run**:
```sql
-- "What symbols are in color/color.go?"
SELECT symbol_id, name, qualified_name, symbol_type, start_line, end_line
FROM symbols WHERE file_id = 'e7841368..'
ORDER BY start_line

→ noColorIsSet (39-41), stdoutIsTerminal (45-50), ..., New (173-184), ...

-- "Who calls New()?" (New is in the changed line range)
→ RGB, BgRGB, Set (same as Scenario 2)
```

**What the review includes**:
```
⚠️ You changed New()'s signature but 3 callers still use the old signature:
  - RGB() at color/color.go:188
  - BgRGB() at color/color.go:193
  - Set() at color/color.go:213
  These will break.
```

**Without DuckDB**: LLM reviews only the diff. Doesn't know 3 other functions call `New()`. Misses the breaking change.

---

### Scenario 4: Module Info — Coupling Score

**When**: User asks `@codewalk what's in the lib module?`

**What happens**: Codewalk counts cross-module import edges to measure how tightly coupled the module is.

```python
# query.py — called by codewalk_get_module_info
for file in info["files"]:
    imports = graph_store.get_imports(file)      # ← DuckDB
    importers = graph_store.get_importers(file)  # ← DuckDB
    outgoing += sum(1 for imp in imports if imp not in module_files)
    incoming += sum(1 for imp in importers if imp not in module_files)
```

**Output**:
```
Module: lib (6 files)
Coupling: 0 outgoing, 5 incoming cross-module edges
→ lib is a dependency of other modules but doesn't depend on them — good design.
```

**Without DuckDB**: You get the file list but no coupling information. Can't tell if the module has clean boundaries.

---

## Why Not SQLite? Honest Comparison

### Where DuckDB Wins (Codewalk's actual workload)

| Operation | DuckDB | SQLite | Why |
|---|---|---|---|
| `populate_from_analysis()` — bulk INSERT 200 files + 277 symbols + 478 calls | Batched columnar insert | Row-at-a-time even with `executemany` | DuckDB: 5-10x faster for bulk writes |
| `get_import_edges()` — 3-table JOIN, fetch all rows | Reads 2 columns per table | Reads all 4-7 columns per table | DuckDB: skips unused columns |
| `get_symbols_in_file()` — filter + sort | Reads file_id + name columns | Reads all 7 columns | DuckDB: 3.5x less I/O |
| `SELECT name, COUNT(*) GROUP BY type` | Columnar aggregation | Row-at-a-time count | DuckDB: vectorized batch processing |

### Where SQLite Wins (NOT Codewalk's workload)

| Operation | SQLite | DuckDB | Why |
|---|---|---|---|
| `SELECT * FROM files WHERE file_id = 'abc'` — single row lookup | All columns already together, one disk seek | Must read from 4 separate column locations | SQLite: row is pre-assembled |
| Concurrent writes from multiple processes | Mature WAL mode + file locking | Single-writer design | SQLite: battle-tested concurrency |
| `INSERT INTO users VALUES (...)` — one row at a time | Row goes in one spot on disk | Value must be appended to 4+ separate columns | SQLite: fewer disk writes |
| Ecosystem | Every language, every tool supports it | Newer — fewer GUI tools available | SQLite: universal support |

### Why Those SQLite Advantages Don't Apply to Codewalk

1. **Single row lookup** — Codewalk never does `SELECT * FROM files WHERE file_id = X` and needs all columns. It always needs 1-3 columns out of 4-7.
2. **Concurrent writes** — Codewalk is single-process. Only one MCP server or API server writes at a time.
3. **One-at-a-time inserts** — Codewalk always bulk inserts (200 files at once, 277 symbols at once). Never inserts one row.

---

## The Real Data (from actual indexed repos)

### `fatih/color` (Go library)

```
TABLE COUNTS:
  files             9 rows
  imports           0 rows
  symbols           105 rows
  symbol_metadata   0 rows
  class_hierarchy   0 rows
  class_members     0 rows
  symbol_calls      348 rows
  chunks            0 rows
  modules           3 rows
  module_deps       0 rows
```

### `tj/commander.js` (JS CLI framework)

```
TABLE COUNTS:
  files             198 rows
  imports           17 rows
  symbols           277 rows
  symbol_metadata   0 rows
  class_hierarchy   0 rows
  class_members     0 rows
  symbol_calls      478 rows
  chunks            0 rows
  modules           7 rows
  module_deps       3 rows
```

Sample `symbol_calls` data from `color`:
```
caller                      → callee                      line
─────────────────────────── → ─────────────────────────── ────
color/color.go:New          → color/color.go:noColorIsSet  178
color/color.go:New          → color/color.go:boolPtr       179
color/color.go:New          → color/color.go:Add           182
color/color.go:RGB          → color/color.go:New           188
color/color.go:BgRGB        → color/color.go:New           193
color/color.go:Set          → color/color.go:New           213
```

This reads: "The function `New()` at line 178 calls `noColorIsSet()`. The function `RGB()` at line 188 calls `New()`."

Sample `imports` data from `commander.js`:
```
source                    → target
───────────────────────── → ───────────────
tests/program.test.js     → index.js
lib/command.js            → lib/argument.js
lib/command.js            → lib/option.js
lib/command.js            → lib/error.js
index.js                  → lib/command.js
lib/command.js            → lib/help.js
```

Sample `module_deps` data:
```
root  → lib       (index.js imports lib/command.js)
tests → lib       (tests import lib files)
tests → root      (tests import index.js)
```

---

## DuckDB Schema (10 Tables)

```sql
-- 1. Every source file in the repo
CREATE TABLE files (
    file_id VARCHAR PRIMARY KEY,   -- sha256(path)[:16]
    path VARCHAR UNIQUE,           -- "color/color.go"
    module VARCHAR,                -- "color"
    language VARCHAR               -- "go"
);

-- 2. File A imports File B
CREATE TABLE imports (
    source_file_id VARCHAR,        -- who imports
    target_file_id VARCHAR,        -- who gets imported
    PRIMARY KEY (source_file_id, target_file_id)
);

-- 3. Functions, classes, methods extracted by tree-sitter
CREATE TABLE symbols (
    symbol_id VARCHAR PRIMARY KEY, -- sha256(qualified_name + file + line)[:16]
    name VARCHAR,                  -- "New"
    qualified_name VARCHAR,        -- "color/color.go:New"
    file_id VARCHAR,               -- which file it's in
    symbol_type VARCHAR,           -- "function", "class", "method"
    start_line INTEGER,            -- 173
    end_line INTEGER,              -- 184
    parent_class VARCHAR           -- optional containing class
);

-- 4. Extra metadata for symbols (routes, events, CLI commands, etc.)
CREATE TABLE symbol_metadata (
    symbol_id VARCHAR PRIMARY KEY REFERENCES symbols(symbol_id),
    kind VARCHAR,
    http_method VARCHAR,
    http_path VARCHAR,
    event_name VARCHAR,
    cli_command VARCHAR
);

-- 5. Class inheritance edges
CREATE TABLE class_hierarchy (
    class_symbol_id VARCHAR REFERENCES symbols(symbol_id),
    parent_symbol_id VARCHAR REFERENCES symbols(symbol_id),
    PRIMARY KEY (class_symbol_id, parent_symbol_id)
);

-- 6. Class membership edges
CREATE TABLE class_members (
    class_symbol_id VARCHAR REFERENCES symbols(symbol_id),
    member_symbol_id VARCHAR REFERENCES symbols(symbol_id),
    PRIMARY KEY (class_symbol_id, member_symbol_id)
);

-- 7. Function A calls Function B at line N
CREATE TABLE symbol_calls (
    caller_symbol_id VARCHAR,
    callee_symbol_id VARCHAR,
    line INTEGER,                  -- line number of the call site
    PRIMARY KEY (caller_symbol_id, callee_symbol_id, line)
);

-- 8. Chunk metadata (bridge to ChromaDB embeddings)
CREATE TABLE chunks (
    chunk_id VARCHAR PRIMARY KEY,
    file_id VARCHAR,
    symbol_id VARCHAR,
    start_line INTEGER,
    end_line INTEGER,
    content_hash VARCHAR,
    embedding_id VARCHAR           -- links to ChromaDB
);

-- 9. Auto-detected module groupings
CREATE TABLE modules (
    name VARCHAR PRIMARY KEY,      -- "lib", "tests", "root"
    file_count INTEGER             -- 6
);

-- 10. Module A depends on Module B
CREATE TABLE module_deps (
    source VARCHAR,                -- "tests"
    target VARCHAR,                -- "lib"
    PRIMARY KEY (source, target)
);
```

### ID Strategy

All IDs are **deterministic hashes** — computable without touching the database:

```python
import hashlib

def _stable_id(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]

# file_id = _stable_id("color/color.go")           → "e7841368efc54c47"
# symbol_id = _stable_id("color/color.go:New", "color/color.go", "173") → "9dfa5a86a99261f"
```

Why hashes instead of auto-increment IDs:
- **Same ID everywhere**: DuckDB, ChromaDB metadata, and igraph vertex names all use the same hash
- **No round-trip**: Don't need `INSERT → fetch last_id → use it` — compute the ID before inserting
- **Deterministic**: Same file always gets the same ID, even if you re-index

---

## Summary

```
SQLite stores:   [id, name, qname, fid, type, line, end]  ← row 1 (all 7 together)
                 [id, name, qname, fid, type, line, end]  ← row 2 (all 7 together)

DuckDB stores:   [id, id, id, ...]          ← all IDs in one block
                 [name, name, name, ...]    ← all names in one block
                 [qname, qname, ...]        ← all qualified names in one block
                 [fid, fid, fid, ...]       ← all file_ids in one block
                 ...

Query: SELECT name WHERE file_id = X

SQLite:  reads ALL 7 columns per row → uses 2 → wastes 5
DuckDB:  reads ONLY 2 columns (file_id + name) → wastes 0
```

**Codewalk's queries are always "scan many rows, use few columns" — exactly what DuckDB is designed for.**
