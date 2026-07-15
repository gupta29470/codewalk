"""Shared utilities for the one-stop review engine."""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("codewalk")


def git_head_sha(repo_path: Path) -> str:
    """Return the current HEAD SHA for a repo. Shared utility."""
    import subprocess

    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        return result.stdout.strip()
    except Exception:
        return ""


def count_tokens(text: str, model: str = "gpt-4o") -> int:
    """Count tokens in text using tiktoken if available, otherwise fallback.

    Args:
        text: The text to tokenize.
        model: Model name for tiktoken encoding selection.

    Returns:
        Estimated token count.
    """
    if not text:
        return 0

    try:
        import tiktoken
        encoding = tiktoken.encoding_for_model(model)
        return len(encoding.encode(text))
    except ImportError:
        pass
    except Exception as e:
        logger.debug(f"[tokenizer] tiktoken failed for model {model}: {e}")

    # Fallback: ~3 characters per token for code (symbols, indentation compress less).
    return len(text) // 3


def _extract_import_block(lines: list[str]) -> list[str]:
    """Return leading import/using/require statements."""
    imports: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            imports.append(line)
            continue
        if stripped.startswith(("import ", "from ", "using ", "require", "#include", "use ")):
            imports.append(line)
        else:
            break
    return imports


def smart_truncate_file_content(
    content: str,
    hunks: list[Any],
    max_tokens: int = 10000,
    context_lines: int = 50,
) -> str:
    """Truncate file content intelligently around diff hunks.

    Keeps import blocks, diff hunks plus surrounding context, and collapses
    large untouched sections with an ellipsis marker.
    """
    if not content:
        return ""

    if not hunks:
        # No diff context; fall back to top-of-file truncation.
        return content[:max_tokens * 4]

    lines = content.splitlines()
    total_lines = len(lines)

    # Build kept ranges around hunks.
    ranges: list[tuple[int, int]] = []
    for hunk in hunks:
        # DiffHunk uses 1-based start_line/end_line in the new file.
        start = getattr(hunk, "start_line", 1) - 1
        end = getattr(hunk, "end_line", start + 1) - 1
        if start < 0:
            start = 0
        if end >= total_lines:
            end = total_lines - 1
        if end < start:
            end = start

        kept_start = max(0, start - context_lines)
        kept_end = min(total_lines - 1, end + context_lines)
        ranges.append((kept_start, kept_end))

    # Merge overlapping ranges.
    ranges.sort()
    merged: list[tuple[int, int]] = []
    for start, end in ranges:
        if merged and start <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))

    # Always include import block if present.
    import_lines = _extract_import_block(lines)
    import_end = len(import_lines) - 1
    if import_lines and (not merged or import_end < merged[0][0]):
        merged.insert(0, (0, import_end))

    # Build truncated content.
    parts: list[str] = []
    prev_end = -1
    for start, end in merged:
        if start > prev_end + 1:
            omitted = start - prev_end - 1
            parts.append(f"\n... [{omitted} lines omitted] ...\n")
        parts.extend(lines[start : end + 1])
        prev_end = end

    if prev_end < total_lines - 1:
        omitted = total_lines - 1 - prev_end
        parts.append(f"\n... [{omitted} lines omitted] ...")

    truncated = "\n".join(parts)

    # Final token guard: if still over budget, shrink context aggressively.
    if count_tokens(truncated) > max_tokens and context_lines > 5:
        return smart_truncate_file_content(
            content, hunks, max_tokens=max_tokens, context_lines=context_lines // 2
        )

    return truncated

def _load_code_guidelines_from_docs_path(docs_path: str) -> str:
    """Search ``docs_path`` for a file named ``code_guidelines`` and return its text."""
    if not docs_path or not os.path.isdir(docs_path):
        return ""

    allowed_exts = {".md", ".txt", ".rst"}
    for root, _dirs, files in os.walk(docs_path):
        for filename in files:
            base, ext = os.path.splitext(filename.lower())
            if base == "code_guidelines" and ext in allowed_exts:
                full_path = os.path.join(root, filename)
                try:
                    return Path(full_path).read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    return ""
    return ""


def _load_code_guidelines_from_doc_collection(
    persist_dir: str,
    collection_name: str,
) -> str:
    """Search the indexed doc collection for a ``code_guidelines`` document."""
    try:
        import chromadb
        client = chromadb.PersistentClient(path=persist_dir)
        collection = client.get_or_create_collection(name=collection_name)
        if collection.count() == 0:
            return ""

        results = collection.get(
            where={"doc_path": {"$contains": "code_guidelines"}},
            include=["documents", "metadatas"],
        )
        if results and results.get("documents"):
            docs = results["documents"]
            metas = results.get("metadatas") or []
            # Sort by chunk_index if available to preserve order.
            pairs = list(zip(docs, metas))
            pairs.sort(key=lambda x: (x[1] or {}).get("chunk_index", 0))
            return "\n".join(text for text, _ in pairs)
    except Exception:
        return ""
    return ""


def load_code_guidelines_text(
    repo_path: Path,
    docs_path: str | None = None,
    code_guidelines: str | None = None,
    *,
    use_doc_collection: bool = True,
) -> str:
    """Load code guidelines text.

    1. Use an explicit ``code_guidelines`` file path if provided.
    2. Search the indexed doc collection for a ``code_guidelines`` document (optional).
    3. Fall back to scanning ``docs_path`` on disk for ``code_guidelines.*``.
    4. Return empty string if none of the above finds it.

    Args:
        use_doc_collection: If False, skip the ChromaDB doc collection lookup.
            Review paths set this to False to avoid cold-start latency.
    """
    guidelines = ""

    # 1. Explicit file path wins.
    if code_guidelines:
        path = code_guidelines
        if not os.path.isabs(path):
            path = os.path.join(str(repo_path), path)
        try:
            return Path(path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            pass

    # 2. Indexed doc collection lookup.
    if use_doc_collection:
        # Derive collection prefix from repo folder name or manifest.
        collection_prefix = repo_path.name or "codebase"
        manifest_path = repo_path / ".codewalk" / "manifest.json"
        if manifest_path.exists():
            try:
                with open(manifest_path, encoding="utf-8") as f:
                    manifest = json.load(f)
                if manifest.get("collection_name"):
                    collection_prefix = manifest["collection_name"]
            except Exception:
                pass

        persist_dir = str(repo_path / ".codewalk" / "chroma")
        collection_name = f"{collection_prefix}_docs"

        guidelines = _load_code_guidelines_from_doc_collection(persist_dir, collection_name)
        if guidelines:
            return guidelines

    # 3. Fallback: scan docs_path on disk for code_guidelines.*
    if docs_path:
        if not os.path.isabs(docs_path):
            docs_path = os.path.join(str(repo_path), docs_path)
        guidelines = _load_code_guidelines_from_docs_path(docs_path)
    return guidelines


def _sanitize_branch_name(branch: str | None) -> str:
    """Make a git branch name safe for use in a directory name.

    Preserves readability but replaces filesystem-unsafe characters.
    """
    if not branch:
        return "none"
    # Replace path separators and other unsafe chars with a hyphen.
    sanitized = re.sub(r'[\\/:*?"<>|]+', "-", branch)
    # Collapse multiple hyphens/underscores for readability.
    sanitized = re.sub(r"-+", "-", sanitized)
    return sanitized.strip("-").strip("_") or "none"


def build_session_folder_name(
    created_at: datetime,
    current_branch: str | None,
    target_branch: str | None,
) -> str:
    """Build a descriptive session folder name.

    Format: 23-June-2026-143052-<current_branch>[-to-<target_branch>]
    Includes time to prevent same-day collisions.
    """
    date_part = created_at.strftime("%d-%B-%Y-%H%M%S")
    current = _sanitize_branch_name(current_branch)
    parts = [date_part, current]
    if target_branch:
        parts.append("to")
        parts.append(_sanitize_branch_name(target_branch))
    return "-".join(parts)


def get_current_branch(repo_path: Path) -> str | None:
    """Best-effort current git branch name."""
    import subprocess

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip() or None
    except Exception:
        return None


def get_full_file_tree(repo_path: Path, codewalk_config: Any | None = None) -> list[str]:
    """Return the repository file tree as relative paths, respecting codewalk.yaml.

    If ``codewalk_config`` is provided, files and directories matching
    codewalk.yaml exclude patterns are filtered out. This applies to both
    the ``git ls-files`` path and the ``os.walk`` fallback.

    Uses ``git ls-files`` when inside a git repo, otherwise falls back to
    ``os.walk``. Hidden files and common dependency/build directories are
    always skipped via the core safety net.
    """
    import subprocess

    # Lazy-import exclusion helpers only when codewalk_config is provided
    _is_excluded_file = None
    if codewalk_config is not None:
        from src.codewalk.codewalk_config import is_excluded_file as _is_excluded_file

    try:
        result = subprocess.run(
            ["git", "ls-files"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
        )
        paths = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        if paths:
            if _is_excluded_file is not None:
                repo_str = str(repo_path)
                paths = [
                    p for p in paths
                    if not _is_excluded_file(
                        p.rsplit("/", 1)[-1],  # filename
                        p,                      # relative path
                        codewalk_config,
                        repo_path=repo_str,
                    )
                ]
            return paths
    except Exception:
        pass

    paths: list[str] = []
    skip_dirs = {
        ".git", ".codewalk", "node_modules", "__pycache__", ".venv",
        "venv", "dist", "build", ".next", ".turbo", ".nx",
    }

    if codewalk_config is not None:
        from src.codewalk.codewalk_config import is_excluded_dir

    for root, dirs, files in os.walk(repo_path):
        rel_dir = os.path.relpath(root, repo_path)

        # Prune directories
        if codewalk_config is not None:
            dirs[:] = [
                d for d in dirs
                if d not in skip_dirs and not is_excluded_dir(d, rel_dir, codewalk_config)
            ]
        else:
            dirs[:] = [d for d in dirs if d not in skip_dirs]

        for f in files:
            full = Path(root) / f
            try:
                rel = full.relative_to(repo_path).as_posix()
            except ValueError:
                continue

            if _is_excluded_file is not None:
                if _is_excluded_file(f, rel, codewalk_config, repo_path=str(repo_path)):
                    continue

            paths.append(rel)
    return paths


def load_finding_by_session_and_index(
    repo_path: Path,
    session_id: str,
    finding_index: int,
) -> dict[str, Any] | None:
    """Load a single finding from a session's llm_findings.json by index."""
    from src.codewalk.review.session_store import load_session, load_findings

    session = load_session(repo_path, session_id)
    if session is None:
        return None

    findings = load_findings(repo_path, session.folder_name or session.session_id)
    if not findings or finding_index < 0 or finding_index >= len(findings):
        return None
    return findings[finding_index]
