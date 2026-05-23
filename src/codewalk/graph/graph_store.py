import logging
import hashlib
from pathlib import Path

import duckdb

logger = logging.getLogger("codewalk")

def _stable_id(*parts: str) -> str:
    """Deterministic hash ID from input parts. No DB lookup needed."""
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


class GraphStore:
    """Persistent graph storage backed by DuckDB.

    Stores file dependencies, function metadata, module groupings,
    and (later) function-level call edges. Lives at .codewalk/graph.duckdb
    inside each analyzed repo.

    Usage:
        store = GraphStore("/path/to/repo/.codewalk/graph.duckdb")
        store.populate_from_analysis(files, deps, module_results)
        # Data persists across restarts — no rebuild needed.
    """
    def __init__(self, db_path: str = ".codewalk/graph.duckdb"):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = duckdb.connect(db_path)
        self._create_tables()

    def _create_tables(self):
        """Create all 7 graph tables. IF NOT EXISTS = safe to call every startup."""

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS files (
                file_id VARCHAR PRIMARY KEY,
                path VARCHAR UNIQUE,
                module VARCHAR,
                language VARCHAR
            );
            
            CREATE TABLE IF NOT EXISTS imports (
                source_file_id VARCHAR REFERENCES files(file_id),
                target_file_id VARCHAR REFERENCES files(file_id),
                PRIMARY KEY (source_file_id, target_file_id)
            );
                          
            CREATE TABLE IF NOT EXISTS symbols (
                symbol_id VARCHAR PRIMARY KEY,
                name VARCHAR,
                qualified_name VARCHAR,
                file_id VARCHAR REFERENCES files(file_id),
                symbol_type VARCHAR,
                start_line INTEGER,
                end_line INTEGER
            );
                          
            CREATE TABLE IF NOT EXISTS symbol_calls (
                caller_symbol_id VARCHAR REFERENCES symbols(symbol_id),
                callee_symbol_id VARCHAR REFERENCES symbols(symbol_id),
                line INTEGER,
                PRIMARY KEY (caller_symbol_id, callee_symbol_id, line)
            );
                          
            CREATE TABLE IF NOT EXISTS chunks (
                chunk_id VARCHAR PRIMARY KEY,
                file_id VARCHAR REFERENCES files(file_id),
                symbol_id VARCHAR,
                start_line INTEGER,
                end_line INTEGER,
                content_hash VARCHAR,
                embedding_id VARCHAR
            );
                          
            CREATE TABLE IF NOT EXISTS modules (
                name VARCHAR PRIMARY KEY,
                file_count INTEGER
            );
                          
            CREATE TABLE IF NOT EXISTS module_deps (
                source VARCHAR,
                target VARCHAR,
                PRIMARY KEY (source, target)
            );
        """)

    def populate_from_analysis(
            self,
            files: list[dict],
            deps: dict,
            module_results: dict,
    ):
        """Populate all tables from existing analysis data.

        Args:
            files: From scan_directory() — [{"file_path", "language", ...}]
            deps: From build_dependency_graph() — {"graph": {"a.py": ["b.py"]}}
            module_results: From detect_modules() — {"modules": {...}, "module_graph": {...}}
        """
        # Clear in reverse FK order: children before parents
        self.conn.execute("DELETE FROM symbol_calls")
        self.conn.execute("DELETE FROM symbols")
        self.conn.execute("DELETE FROM imports")
        self.conn.execute("DELETE FROM module_deps")
        self.conn.execute("DELETE FROM modules")
        self.conn.execute("DELETE FROM files")

        self._populate_files(files, module_results)
        self._populate_imports(deps)
        self._populate_symbols(files)
        self._populate_symbol_calls(files)
        self._populate_modules(module_results)
        stats = self._get_stats()

        logger.info(
            f"[GraphStore] Populated: {stats['files']} files, "
            f"{stats['imports']} imports, {stats['symbols']} symbols, "
            f"{stats['modules']} modules"
        )

    def _populate_files(self, files: list[dict], module_results: dict):
        """Insert file records with deterministic hash IDs."""
        file_to_module = {}
        for module_name, module_info in module_results.get("modules", {}).items():
            for file_path in module_info.get("files", []):
                file_to_module[file_path] = module_name

        self.conn.executemany(
            "INSERT INTO files (file_id, path, module, language) VALUES (?, ?, ?, ?)",
            [
                (
                    _stable_id(file["file_path"]),
                    file["file_path"],
                    file_to_module.get(file["file_path"], "root"),
                    file["language"],
                )

                for file in files
            ]
        )

    def _populate_imports(self, deps: dict):
        """Insert file-level import edges using file hash IDs."""

        graph = deps.get("graph", {})

        # Only insert edges where both source and target exist in the files table
        known_ids = {row[0] for row in self.conn.execute("SELECT file_id FROM files").fetchall()}

        rows = []
        seen = set()
        for source, targets in graph.items():
            source_id = _stable_id(source)
            if source_id not in known_ids:
                continue
            for target in targets:
                target_id = _stable_id(target)
                if target_id in known_ids:
                    key = (source_id, target_id)
                    if key not in seen:
                        seen.add(key)
                        rows.append(key)
        
        if rows:
            self.conn.executemany(
                "INSERT INTO imports (source_file_id, target_file_id) VALUES (?, ?)",
                rows
            )

    def _populate_symbols(self, files: list[dict]):
        """Insert symbol records (functions, classes, methods) from tree-sitter.

        Re-parses each file with code_parser.parse_file().
        Only stores metadata — not code. Code lives in ChromaDB chunks.
        """

        from src.codewalk.analysis.code_parser import parse_file, GRAMMAR_MAP

        rows = []

        for file in files:
            if file["language"] not in GRAMMAR_MAP:
                continue
            file_id = _stable_id(file["file_path"])
            read_path = file.get("absolute_path", file["file_path"])
            try:
                parsed_file = parse_file(read_path, file["language"])
                for item in parsed_file:
                    qualified_name = f"{file['file_path']}:{item['name']}"
                    symbol_id = _stable_id(
                        qualified_name,
                        file["file_path"],
                        str(item["start_line"])
                    )
                    rows.append((
                        symbol_id,
                        item["name"],
                        qualified_name,
                        file_id,
                        item["type"],        # "function", "class"
                        item["start_line"],
                        item["end_line"],
                    ))
            except Exception as e:
                logger.warning(f"Failed to parse {file['file_path']}: {e}")
                continue

        if rows:
            self.conn.executemany(
                "INSERT INTO symbols "
                "(symbol_id, name, qualified_name, file_id, symbol_type, start_line, end_line) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                rows
            )

    def _populate_symbol_calls(self, files: list[dict]):
        """Resolve call_extractor results against the symbols table."""
        from src.codewalk.graph.call_extractor import extract_calls_batch

        all_calls = extract_calls_batch(files)
        if not all_calls:
            return

        symbol_by_qname = {}
        symbols_by_name = {}
        for row in self.conn.execute(
            "SELECT symbol_id, qualified_name, name, f.path "
            "FROM symbols s JOIN files f ON s.file_id = f.file_id"
        ).fetchall():
            sid, qname, name, fpath = row
            symbol_by_qname[qname] = sid
            symbols_by_name.setdefault(name, []).append((sid, fpath))

        rows = []
        resolved = 0
        unresolved = 0

        for call in all_calls:
            caller_qname = call["caller"]
            callee_name = call["callee_name"]
            line = call["line"]

            caller_id = symbol_by_qname.get(caller_qname)
            if caller_id is None:
                unresolved += 1
                continue

            caller_file = caller_qname.rsplit(":", 1)[0]
            candidates = symbols_by_name.get(callee_name, [])

            callee_id = None
            for sid, fpath in candidates:
                if fpath == caller_file:
                    callee_id = sid
                    break

            if callee_id is None and candidates:
                callee_id = candidates[0][0]

            if callee_id is None:
                unresolved += 1
                continue

            rows.append((caller_id, callee_id, line))
            resolved += 1

        if rows:
            self.conn.executemany(
                "INSERT OR IGNORE INTO symbol_calls "
                "(caller_symbol_id, callee_symbol_id, line) VALUES (?, ?, ?)",
                rows
            )

        logger.info(
            f"[GraphStore] Symbol calls: {resolved} resolved, "
            f"{unresolved} unresolved (stdlib/3rd-party)"
        )

    def _populate_modules(self, module_results: dict):
        """Insert module records and module-level dependency edges."""
        modules = module_results.get("modules", {})
        module_graph = module_results.get("module_graph", {})

        if modules:
            self.conn.executemany(
                "INSERT INTO modules (name, file_count) VALUES (?, ?)",
                [
                    (name, info.get("file_count", len(info.get("files", []))))
                    for name, info in modules.items()
                ]
            )
        
        deps_row = []
        for source, targets in module_graph.items():
            for target in targets:
                deps_row.append((source, target))

        if deps_row:
            self.conn.executemany(
                "INSERT INTO module_deps (source, target) VALUES (?, ?)",
                deps_row
            )

    def get_import_edges(self) -> list[tuple[str, str]]:
        """All file-level import edges as (source_path, target_path) tuples.

        Used by runtime.py to build the igraph instance:
            edges = store.get_import_edges()
            g = igraph.Graph.TupleList(edges, directed=True)
        """
        return self.conn.execute(
            """
            SELECT sf.path,
            tf.path FROM imports i 
            JOIN files sf ON i.source_file_id = sf.file_id
            JOIN files tf on i.target_file_id = tf.file_id
            """
        ).fetchall()
    
    def get_module_dep_edges(self) -> list[tuple[str, str]]:
        """All module-level dependency edges as (source, target) tuples."""
        return self.conn.execute(
            "SELECT source, target FROM module_deps"
        ).fetchall()
    
    def get_module_file(self, file_path: str) -> str | None:
        """Which module does this file belong to?"""
        file_id = _stable_id(file_path)
        result = self.conn.execute(
            "SELECT module FROM files where file_id = ?", [file_id]
        ).fetchone()
        return result[0] if result else None
    
    def get_files_in_module(self, module_name: str) -> list[str]:
        """All file paths in a given module."""
        return [
            row[0] for row in self.conn.execute(
                "SELECT path FROM files WHERE module = ?", [module_name]
            ).fetchall()
        ]
    
    def get_symbols_in_file(self, file_path: str) -> list[dict]:
        """All symbols (functions/classes) in a file, ordered by line number."""
        file_id = _stable_id(file_path)
        rows = self.conn.execute(
            "SELECT symbol_id, name, qualified_name, symbol_type, start_line, end_line "
            "FROM symbols WHERE file_id = ? ORDER BY start_line", [file_id]
        ).fetchall()
        return [
            {
                "symbol_id": row[0],
                "name": row[1],
                "qualified_name": row[2],
                "symbol_type": row[3],
                "start_line": row[4],
                "end_line": row[5],
            }
            for row in rows
        ]
    
    def get_all_files(self) -> list[str]:
        """All file paths in the graph."""
        return [
            row[0] for row in self.conn.execute("SELECT path FROM files").fetchall()
        ]
    
    def get_importers(self, file_path: str) -> list[str]:
        """Which files import this file? (reverse lookup)"""
        file_id = _stable_id(file_path)
        return [
            row[0] for row in self.conn.execute(
                "SELECT f.path FROM imports i "
                "JOIN files f ON i.source_file_id = f.file_id "
                "WHERE i.target_file_id = ?", [file_id]
            ).fetchall()
        ]
    
    def get_imports(self, file_path: str) -> list[str]:
        """Which files does this file import? (forward lookup)"""
        file_id = _stable_id(file_path)
        return [
            row[0] for row in self.conn.execute(
                "SELECT f.path FROM imports i "
                "JOIN files f ON i.target_file_id = f.file_id "
                "WHERE i.source_file_id = ?", [file_id]
            ).fetchall()
        ]
    
    def _get_stats(self) -> dict:
        """Summary stats for all tables."""
        return {
            "files": self.conn.execute("SELECT COUNT(*) FROM files").fetchone()[0],
            "imports": self.conn.execute("SELECT COUNT(*) FROM imports").fetchone()[0],
            "symbols": self.conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0],
            "symbol_calls": self.conn.execute("SELECT COUNT(*) FROM symbol_calls").fetchone()[0],
            "chunks": self.conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0],
            "modules": self.conn.execute("SELECT COUNT(*) FROM modules").fetchone()[0],
        }
    
    def get_callers_of_symbol(self, qualified_name: str) -> list[dict]:
        """Who calls this symbol? Returns caller name, file, and call site line.

        Args:
            qualified_name: e.g. "color.go:Fprint" or "config.py:Settings"
        """
        result = self.conn.execute(
            "SELECT symbol_id FROM symbols WHERE qualified_name = ?",
            [qualified_name]
        ).fetchone()
        if not result:
            return []
        callee_id = result[0]
        rows = self.conn.execute(
            "SELECT s.name, s.qualified_name, f.path, sc.line "
            "FROM symbol_calls sc "
            "JOIN symbols s ON sc.caller_symbol_id = s.symbol_id "
            "JOIN files f ON s.file_id = f.file_id "
            "WHERE sc.callee_symbol_id = ? "
            "ORDER BY f.path, sc.line",
            [callee_id]
        ).fetchall()

        return [
            {
                "caller": row[0],           # "login"
                "caller_qualified": row[1],  # "views.py:login"
                "file": row[2],              # "views.py"
                "line": row[3],              # 32
            }
            for row in rows
        ]
    
    def get_callees_of_symbol(self, qualified_name: str) -> list[dict]:
        """What does this symbol call? Returns callee name, file, and line."""
        result = self.conn.execute(
            "SELECT symbol_id FROM symbols WHERE qualified_name = ?",
            [qualified_name]
        ).fetchone()
        if not result:
            return []
        caller_id = result[0]
        rows = self.conn.execute(
            "SELECT s.name, s.qualified_name, f.path, sc.line "
            "FROM symbol_calls sc "
            "JOIN symbols s ON sc.callee_symbol_id = s.symbol_id "
            "JOIN files f ON s.file_id = f.file_id "
            "WHERE sc.caller_symbol_id = ? "
            "ORDER BY sc.line",
            [caller_id]
        ).fetchall()
        return [
            {
                "callee": row[0],            # "setWriter"
                "callee_qualified": row[1],   # "color.go:setWriter"
                "file": row[2],               # "color.go"
                "line": row[3],               # 289
            }
            for row in rows
        ]
    
    def close(self):
        """Close the DuckDB connection."""
        self.conn.close()
        


