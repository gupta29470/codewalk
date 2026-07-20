"""Load and validate codewalk.yaml configuration."""
from dataclasses import dataclass, field
from pathlib import Path
import fnmatch
import os as _os
import yaml

from src.codewalk.ingestion.file_filter import should_skip, should_skip_dir


@dataclass
class CodewalkConfig:
    """Per-repo configuration extracted from codewalk.yaml."""
    exclude: list[str] = field(default_factory=list)
    include: list[str] = field(default_factory=list)  # override exclude + core filter
    branches: list[str] = field(default_factory=list)  # allowed index branches (fnmatch)
    docs_path: str = ""         # relative to repo root
    code_guidelines: str = ""   # optional explicit path to review guidelines file
    tools: dict = field(default_factory=dict)  # tool command overrides (e.g. static_analysis, test_command)


def index_branches(config: CodewalkConfig) -> list[str]:
    """Branches that may trigger cloud indexing. Defaults to master if unset."""
    return config.branches if config.branches else ["master"]


def branch_allowed(branch: str, allowed: list[str]) -> bool:
    """True if branch matches any allowed pattern (exact or fnmatch, e.g. release/**)."""
    return any(fnmatch.fnmatch(branch, pattern) for pattern in allowed)


def load_codewalk_yaml(repo_root: str) -> CodewalkConfig:
    """Load codewalk.yaml from repo root. Returns empty CodewalkConfig if missing."""
    path = Path(repo_root) / "codewalk.yaml"
    if not path.exists():
        return CodewalkConfig()
    
    with open(path) as file:
        data = yaml.safe_load(file) or {}

    indexing = data.get("indexing", {})
    return CodewalkConfig(
        exclude=indexing.get("exclude", []),
        include=indexing.get("include", []),
        branches=indexing.get("branches") or [],
        docs_path=data.get("docs_path", ""),
        code_guidelines=data.get("code_guidelines", ""),
        tools=data.get("tools", {}),
    )


def _include_keeps_dir(pattern: str, dir_path: str) -> bool:
    """Return True if dir_path (or anything under it) is covered by an include pattern."""
    # Normalize trailing /** (if any)
    base = pattern.rstrip("/")
    if base.endswith("/**"):
        base = base[:-3]

    if not base:
        return True

    if "*" in base or "?" in base:
        # Glob: keep the dir if it matches, or if a concrete prefix of the
        # pattern is a prefix of the dir path (meaning the include tree lives
        # somewhere inside this dir).
        if fnmatch.fnmatch(dir_path, base):
            return True
        concrete_prefix = base.split("*", 1)[0].rstrip("/")
        if concrete_prefix and (dir_path == concrete_prefix or dir_path.startswith(concrete_prefix + "/")):
            return True
        return False

    # Plain path: exact match, this dir is under the base, or the base is
    # inside this dir (so we must keep this dir to reach the included subtree).
    return (
        dir_path == base
        or dir_path.startswith(base + "/")
        or base.startswith(dir_path + "/")
    )


def _include_keeps_file(pattern: str, relative_path: str, filename: str) -> bool:
    """Return True if a file matches an include pattern."""
    if "*" in pattern or "?" in pattern:
        return fnmatch.fnmatch(relative_path, pattern) or fnmatch.fnmatch(filename, pattern)
    return relative_path == pattern or relative_path.startswith(pattern + "/") or filename == pattern


def is_excluded_dir(dir_name: str, rel_dir: str, config: CodewalkConfig) -> bool:
    """Check if a directory should be pruned during os.walk.

    Order:
      1. include patterns override everything (keep the dir).
      2. core safety net (file_filter.should_skip_dir).
      3. codewalk.yaml exclude patterns.
    """
    full_dir = f"{rel_dir}/{dir_name}" if rel_dir != "." else dir_name

    # 1. Explicit include wins.
    if config.include and any(_include_keeps_dir(p, full_dir) for p in config.include):
        return False

    # 2. Core safety net.
    if should_skip_dir(dir_name):
        return True

    # 3. Codewalk excludes.
    for part in config.exclude:
        # Plain name → match dir name directly (e.g. "node_modules", "vendor")
        if "*" not in part and "?" not in part and "/" not in part:
            if part == dir_name:
                return True
        # "tests/**" → prune dir named "tests"
        elif part.endswith("/**"):
            dir_pat = part[:-3]
            if full_dir == dir_pat or fnmatch.fnmatch(full_dir, dir_pat):
                return True
        # "src/generated" (path, no glob) → prune if dir path matches
        elif "/" in part and "*" not in part and "?" not in part:
            if full_dir == part or full_dir.startswith(part + "/"):
                return True
    return False


def _exclude_matches_file(pattern: str, filename: str, relative_path: str) -> bool:
    """Check if a codewalk.yaml exclude pattern matches a file."""
    # Glob → match against filename or full relative path.
    if "*" in pattern or "?" in pattern:
        return fnmatch.fnmatch(filename, pattern) or fnmatch.fnmatch(relative_path, pattern)

    # Path prefix (e.g. "src/generated") → match exact file or anything under it.
    if "/" in pattern:
        return relative_path == pattern or relative_path.startswith(pattern + "/")

    # Plain name → match filename exactly, or any ancestor directory named pattern.
    if pattern == filename:
        return True
    return pattern in relative_path.split("/")


def is_excluded_file(filename: str, relative_path: str, config: CodewalkConfig, repo_path: str | None = None) -> bool:
    """Check if a file should be excluded after directory pruning.

    Order:
      1. include patterns override everything (keep the file).
      2. core safety net (file_filter.should_skip).
      3. codewalk.yaml exclude patterns.
    """
    # 1. Explicit include wins.
    if config.include and any(_include_keeps_file(p, relative_path, filename) for p in config.include):
        return False

    # 2. Core safety net (binaries, generated files, .codewalkignore, etc.).
    if should_skip(relative_path, repo_path=repo_path):
        return True

    # 3. Codewalk excludes.
    for part in config.exclude:
        if _exclude_matches_file(part, filename, relative_path):
            return True
    return False


def codewalk_scan_directory(directory: str, config: CodewalkConfig) -> list[dict]:
    """Walk a directory and filter using the core safety net + codewalk config.

    Returns same format as scanner.scan_directory: list of file dicts.

    Steps:
      1. Prune directories in-place using is_excluded_dir().
      2. Skip individual files using is_excluded_file().
    """
    from src.codewalk.ingestion.scanner import detect_language

    root = Path(directory)
    if not root.exists():
        raise FileNotFoundError(f"Directory {directory} does not exist.")

    files = []
    root_str = str(root)

    for dirpath, dirs, filenames in _os.walk(root, followlinks=False):
        rel_dir = _os.path.relpath(dirpath, root_str)

        # Step 1: Prune excluded dirs IN-PLACE.
        dirs[:] = [
            d for d in dirs
            if not is_excluded_dir(d, rel_dir, config)
        ]

        # Step 2: Filter files.
        for fname in filenames:
            relative = _os.path.join(rel_dir, fname) if rel_dir != "." else fname

            if is_excluded_file(fname, relative, config, repo_path=root_str):
                continue

            full_path = _os.path.join(dirpath, fname)
            try:
                size = _os.path.getsize(full_path)
            except OSError:
                continue  # broken symlink or inaccessible file
            files.append({
                "file_path": relative,
                "absolute_path": full_path,
                "language": detect_language(Path(full_path)),
                "size_bytes": size,
            })

    return files
