import subprocess
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from src.codewalk.config import settings

CLONE_BASE = Path(settings.clone_dir).resolve()

def parse_github_url(url: str) -> tuple[str, str]:
    """
    Parse a GitHub URL → return (owner, repo_name).

    Examples:
        "https://github.com/fastapi/fastapi"     → ("fastapi", "fastapi")
        "https://github.com/flutter/flutter.git"  → ("flutter", "flutter")
    """
    parsed = urlparse(url)

    parts = parsed.path.strip("/").split("/")

    if len(parts) < 2:
        raise ValueError(f"Invalid GitHub URL: {url}. Expected format: https://github.com/owner/repo")
    
    owner = parts[0]
    repo = parts[1].removesuffix(".git")

    return owner, repo

def is_url(input_path: str) -> bool:
    """Check if the input looks like a URL (not a local path)."""
    parsed = urlparse(input_path)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)

def _build_clone_url(url: str) -> str:
    """If a GitHub token is configured, inject it into the URL for auth.

    Public:  https://github.com/owner/repo
    Private: https://ghp_xxxx@github.com/owner/repo
    """

    if not settings.github_token:
        return url
    
    parsed = urlparse(url)

    authed = parsed._replace(netloc=f"{settings.github_token}@{parsed.netloc}")
    return urlunparse(authed)

def clone_repo(url: str) -> str:
    """
    Clone a GitHub repo → return the local path where it was cloned.

    Uses --depth=1 for speed (only latest commit).
    If already cloned, returns existing path without re-cloning.
    """
    owner, repo = parse_github_url(url)

    target = CLONE_BASE /owner /repo

    if target.exists():
        print(f"Repo already cloned at {target}")
        return str(target)
    
    target.parent.mkdir(parents=True, exist_ok=True)

    try:
        subprocess.run(
            ["git", "clone", "--depth", "1", _build_clone_url(url), str(target)],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to clone {url}: {e.stderr.strip()}")
    
    print(f"Cloned {url} → {target}")
    return str(target)

def get_repo_path(user_input: str) -> str:
    """
    Main entry point: accept URL or local path → return a local path.

    If URL → clone it first, then return the clone path.
    If local path → validate it exists, return it.
    """
    if is_url(user_input):
        return clone_repo(user_input)

    path = Path(user_input).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"Path does not exist: {user_input}")
    return str(path)