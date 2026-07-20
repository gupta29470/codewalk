"""Neighborhood expansion for one-stop review."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from src.codewalk.review.diff_parser import DiffFile


@dataclass
class NeighborhoodSnippet:
    """One contextual snippet from a neighbor of a changed file."""
    file_path: str
    content: str
    source: str  # "caller", "test", "interface", "callee"


@dataclass
class NeighborhoodResult:
    """Collection of snippets surrounding the changed files."""
    snippets: list[NeighborhoodSnippet] = field(default_factory=list)


def _read_lines(path: Path, start: int, end: int) -> str:
    """Read lines [start, end] from a file (1-based, inclusive)."""
    if not path.exists():
        return ""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return ""

    start_idx = max(0, start - 1)
    end_idx = max(start_idx, end)
    return "\n".join(lines[start_idx:end_idx])


def _extract_imported_modules(file_path: str, source: str) -> set[str]:
    """Extract imported module names from TypeScript/Python source."""
    modules: set[str] = set()
    for line in source.splitlines():
        line = line.strip()
        if line.startswith("import ") or line.startswith("from "):
            # TypeScript: import X from 'path'
            if "from" in line:
                parts = line.split("from")
                if len(parts) == 2:
                    module = parts[1].strip().strip('"\';')
                    if module.startswith("."):
                        continue
                    modules.add(module)
    return modules


def _find_test_files(repo_path: Path, symbol_name: str, source_file: str) -> list[Path]:
    """Find test files for a symbol or source file.

    Searches only tracked files (via git ls-files) to avoid walking dependency
    directories like node_modules, .venv, or .codewalk-env, which can hang the
    review on real repositories.
    """
    from src.codewalk.review.utils import get_full_file_tree

    base = Path(source_file).stem
    escaped_base = re.escape(base)
    escaped_symbol = re.escape(symbol_name)
    patterns = [
        re.compile(rf"^{escaped_base}\.test\..*$"),
        re.compile(rf"^{escaped_base}\.spec\..*$"),
        re.compile(rf"^.*/{escaped_base}\.test\..*$"),
        re.compile(rf"^.*/{escaped_base}\.spec\..*$"),
        re.compile(rf"^.*/{escaped_symbol}\.test\..*$"),
        re.compile(rf"^.*/{escaped_symbol}\.spec\..*$"),
    ]

    candidates: list[Path] = []
    for fp in get_full_file_tree(repo_path):
        if any(p.match(fp) for p in patterns):
            candidates.append(repo_path / fp)
            if len(candidates) >= 5:
                break
    return candidates


def _get_graph_store(graph_store=None):
    """Best-effort graph store retrieval.

    Uses an explicitly passed store first, then falls back to the API state singleton.
    """
    if graph_store is not None:
        return graph_store
    try:
        from src.codewalk.api.state import get_graph_store
        return get_graph_store()
    except Exception:
        return None


def _find_callers(repo_path: Path, diff_file: DiffFile, graph_store=None, deep: bool = False) -> list[NeighborhoodSnippet]:
    """Find top callers of changed symbols in a file.

    When deep=True (single-file review), use a wider caller window (−10/+50)
    and allow more callers per symbol.
    """
    snippets: list[NeighborhoodSnippet] = []
    store = _get_graph_store(graph_store)
    if not store:
        return snippets

    try:
        symbols = store.get_symbols_in_file(str(repo_path / diff_file.file_path))
    except Exception:
        return snippets

    max_callers_per_symbol = 10 if deep else 5
    caller_before = 10 if deep else 5
    caller_after = 50 if deep else 25

    seen_files: set[str] = set()
    for sym in symbols:
        qname = sym.get("qualified_name", "")
        if not qname:
            continue
        try:
            callers = store.get_callers_of_symbol(qname)
        except Exception:
            continue

        for caller in callers[:max_callers_per_symbol]:
            caller_file = caller.get("file_path") or caller.get("path")
            if not caller_file or caller_file in seen_files:
                continue
            seen_files.add(caller_file)
            caller_path = repo_path / caller_file
            # Use line_number from graph to read around the actual call site
            caller_line = caller.get("line_number") or caller.get("line", 1)
            start = max(1, caller_line - caller_before)
            end = caller_line + caller_after
            content = _read_lines(caller_path, start, end)
            if content:
                snippets.append(
                    NeighborhoodSnippet(
                        file_path=caller_file,
                        content=content,
                        source="caller",
                    )
                )

    return snippets


def _find_tests(repo_path: Path, diff_file: DiffFile) -> list[NeighborhoodSnippet]:
    """Find test files related to the changed file."""
    snippets: list[NeighborhoodSnippet] = []
    base = Path(diff_file.file_path).stem
    test_files = _find_test_files(repo_path, base, diff_file.file_path)

    for test_path in test_files:
        rel_path = str(test_path.relative_to(repo_path))
        content = _read_lines(test_path, 1, 200)
        if content:
            snippets.append(
                NeighborhoodSnippet(
                    file_path=rel_path,
                    content=content,
                    source="test",
                )
            )

    return snippets


def _extensions_for_file(file_path: str) -> tuple[str, ...]:
    """Return likely extensions for relative-import resolution based on source language."""
    suffix = Path(file_path).suffix.lower()
    if suffix in {".py"}:
        return (".py",)
    if suffix in {".go"}:
        return (".go",)
    if suffix in {".dart"}:
        return (".dart",)
    if suffix in {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}:
        return (".ts", ".tsx", ".js", ".jsx")
    # Fallback for unknown languages.
    return (".ts", ".tsx", ".js", ".jsx", ".py", ".go", ".dart")


def _find_interfaces(repo_path: Path, diff_file: DiffFile) -> list[NeighborhoodSnippet]:
    """Find interface/type files imported by the changed file."""
    snippets: list[NeighborhoodSnippet] = []
    full_path = repo_path / diff_file.file_path
    if not full_path.exists():
        return snippets

    try:
        source = full_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return snippets

    imported = _extract_imported_modules(diff_file.file_path, source)

    # Try to resolve relative imports to files
    extensions = _extensions_for_file(diff_file.file_path)
    seen_candidates: set[str] = set()
    for line in source.splitlines():
        line = line.strip()
        # Only match actual import statements, not comments/strings/variables
        if not (line.startswith(("import ", "from ")) or line.startswith("import(")):
            continue
        if "from" in line:
            parts = line.split("from")
            if len(parts) == 2:
                module = parts[1].strip().strip('"\';')
                if module.startswith("."):
                    resolved = (full_path.parent / module).resolve()
                    for ext in extensions:
                        candidate = Path(str(resolved) + ext)
                        if candidate.exists():
                            rel = str(candidate.relative_to(repo_path))
                            if rel in seen_candidates:
                                break
                            seen_candidates.add(rel)
                            content = _read_lines(candidate, 1, 200)
                            if content:
                                snippets.append(
                                    NeighborhoodSnippet(
                                        file_path=rel,
                                        content=content,
                                        source="interface",
                                    )
                                )
                            break
                        # Also try index files: module/foo -> module/foo/index.ext
                        index_candidate = resolved / f"index{ext}"
                        if index_candidate.exists():
                            rel = str(index_candidate.relative_to(repo_path))
                            if rel in seen_candidates:
                                break
                            seen_candidates.add(rel)
                            content = _read_lines(index_candidate, 1, 200)
                            if content:
                                snippets.append(
                                    NeighborhoodSnippet(
                                        file_path=rel,
                                        content=content,
                                        source="interface",
                                    )
                                )
                            break

    return snippets


def _is_test_file(file_path: str) -> bool:
    """Return True if the file looks like a test file."""
    lower = file_path.lower()
    return ".test." in lower or ".spec." in lower or lower.startswith("test_") or lower.endswith("_test.py")


def _snippet_priority(snippet: NeighborhoodSnippet, relevant_files: set[str]) -> int:
    """Return priority where smaller values are more relevant."""
    if snippet.file_path in relevant_files:
        return 0
    if snippet.source == "test" or _is_test_file(snippet.file_path):
        return 1
    if snippet.source == "caller":
        return 2
    return 3


def expand_neighborhood(
    repo_path: Path,
    diff_files: list[DiffFile],
    relevant_files: set[str] | None = None,
    max_snippets: int = 20,
    max_tokens: int = 30_000,
    graph_store=None,
    deep: bool = False,
) -> NeighborhoodResult:
    """Expand neighborhood context for changed files.

    Args:
        repo_path: Repository root.
        diff_files: Changed files in the current review scope.
        relevant_files: Optional set of files considered most relevant (e.g., other
            changed files). Callers inside this set are prioritized.
        max_snippets: Hard cap on the number of snippets returned.
        max_tokens: Hard token budget for total neighborhood context (default 30K).
        graph_store: Optional graph store instance. If provided, used instead of
            the api.state singleton (important for MCP mode).
        deep: When True (single-file mode), widens caller window and raises budget.
    """
    if deep:
        max_snippets = max(max_snippets, 30)
        max_tokens = max(max_tokens, 60_000)

    if relevant_files is None:
        relevant_files = {df.file_path for df in diff_files}

    snippets: list[NeighborhoodSnippet] = []
    seen: set[tuple[str, str]] = set()

    for df in diff_files:
        for snippet in _find_callers(repo_path, df, graph_store=graph_store, deep=deep):
            key = (snippet.file_path, snippet.source)
            if key not in seen:
                seen.add(key)
                snippets.append(snippet)

        for snippet in _find_tests(repo_path, df):
            key = (snippet.file_path, snippet.source)
            if key not in seen:
                seen.add(key)
                snippets.append(snippet)

        for snippet in _find_interfaces(repo_path, df):
            key = (snippet.file_path, snippet.source)
            if key not in seen:
                seen.add(key)
                snippets.append(snippet)

    # Sort by relevance and cap by both count and token budget.
    snippets.sort(key=lambda s: _snippet_priority(s, relevant_files))

    kept: list[NeighborhoodSnippet] = []
    tokens_used = 0
    for snippet in snippets[:max_snippets]:
        # Estimate ~3 chars per token for code
        snippet_tokens = len(snippet.content) // 3
        if tokens_used + snippet_tokens > max_tokens:
            break
        kept.append(snippet)
        tokens_used += snippet_tokens

    return NeighborhoodResult(snippets=kept)
