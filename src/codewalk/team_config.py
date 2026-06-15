from dataclasses import dataclass, field
from pathlib import Path
import fnmatch
import os as _os
import yaml

@dataclass
class TeamConfig:
    exclude: list[str] = field(default_factory=list)
    branches: list[str] = field(default_factory=list)  # allowed index branches (fnmatch)
    guidelines_path: str = ""   # relative to repo root
    docs_path: str = ""         # relative to repo root


def index_branches(config: TeamConfig) -> list[str]:
    """Branches that may trigger cloud indexing. Defaults to master if unset."""
    return config.branches if config.branches else ["master"]


def branch_allowed(branch: str, allowed: list[str]) -> bool:
    """True if branch matches any allowed pattern (exact or fnmatch, e.g. release/**)."""
    return any(fnmatch.fnmatch(branch, pattern) for pattern in allowed)


def load_codewalk_yaml(repo_root: str) -> TeamConfig:
    """Load codewalk.yaml from repo root. Returns empty TeamConfig if missing."""
    path = Path(repo_root) / "codewalk.yaml"
    if not path.exists():
        return TeamConfig()
    
    with open(path) as file:
        data = yaml.safe_load(file) or {}

    indexing = data.get("indexing", {})
    return TeamConfig(
        exclude=indexing.get("exclude", []),
        branches=indexing.get("branches") or [],
        guidelines_path=data.get("guidelines_path", ""),
        docs_path=data.get("docs_path", ""),
    )


def is_excluded_dir(dir_name: str, rel_dir: str, config: TeamConfig) -> bool:
    """Check if a directory should be pruned during os.walk.
    Prevents descending into excluded subtrees (e.g. node_modules, vendor).
    """
    if dir_name == ".git":
        return True

    full_dir = f"{rel_dir}/{dir_name}" if rel_dir != "." else dir_name

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


def is_excluded_file(filename: str, relative_path: str, config: TeamConfig) -> bool:
    """Check if a file should be excluded after directory pruning."""
    for part in config.exclude:
        # Plain name → match filename (e.g. "README.md", "Makefile")
        if "*" not in part and "?" not in part and "/" not in part:
            if part == filename:
                return True
        # Glob → match against filename or full relative path
        elif fnmatch.fnmatch(filename, part) or fnmatch.fnmatch(relative_path, part):
            return True
    return False


def team_scan_directory(directory: str, config: TeamConfig) -> list[dict]:
    """Walk a directory and filter using ONLY the team's codewalk.yaml exclude list.
    Returns same format as scanner.scan_directory: list of file dicts.

    Steps:
      1. Prune directories in-place using is_excluded_dir() only.
      2. Skip individual files using is_excluded_file() only.
    """
    from src.codewalk.ingestion.scanner import detect_language

    root = Path(directory)
    if not root.exists():
        raise FileNotFoundError(f"Directory {directory} does not exist.")

    files = []
    root_str = str(root)

    for dirpath, dirs, filenames in _os.walk(root):
        rel_dir = _os.path.relpath(dirpath, root_str)

        # Step 1: Prune excluded dirs IN-PLACE using only team config exclude
        # paths. Intentionally does NOT use file_filter.should_skip_dir().
        dirs[:] = [
            directory for directory in dirs
            if not is_excluded_dir(directory, rel_dir, config)
        ]

        # Step 2: Filter files using only team config exclude paths.
        # Intentionally does NOT use file_filter.should_skip().
        for fname in filenames:
            relative = _os.path.join(rel_dir, fname) if rel_dir != "." else fname

            if is_excluded_file(fname, relative, config):
                continue

            full_path = _os.path.join(dirpath, fname)
            files.append({
                "file_path": relative,
                "absolute_path": full_path,
                "language": detect_language(Path(full_path)),
                "size_bytes": _os.path.getsize(full_path),
            })

    return files
