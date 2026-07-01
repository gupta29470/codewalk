"""Shared version + staleness checks for MCP tools and local API.

Surfaces two update channels:
  1. Index artifact — cloud index newer than local .codewalk/
  2. Codewalk software — cloud API / local install newer than running process
"""

from __future__ import annotations

import functools
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import requests

_CACHE_TTL_SEC = 45

_SKIP_INDEX_BANNER = frozenset({"codewalk_pull_index", "codewalk_connect_repo"})

_STALENESS_API_PREFIXES = (
    "/analyze",
    "/chat",
    "/overview",
    "/modules",
    "/blast-radius",
    "/reading-order",
    "/execution-flow",
    "/refresh",
    "/incremental-reindex",
    "/review",
    "/voice",
    "/cycles",
    "/architecture",
    "/knowledge-graph",
    "/docs",
    "/research",
)

_cache: dict[str, tuple[float, Any]] = {}


def version_info() -> dict:
    """Canonical deployment metadata — same payload for API GET /version and cloud."""
    from src.codewalk import __version__

    commit_sha = os.environ.get("CODEWALK_COMMIT_SHA", "")
    if not commit_sha:
        try:
            root = _codewalk_install_root()
            if root and (root / ".git").exists():
                result = subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=str(root),
                    capture_output=True,
                    text=True,
                    timeout=3,
                    check=True,
                )
                commit_sha = result.stdout.strip()
        except Exception:
            commit_sha = ""

    if not commit_sha:
        commit_sha = "unknown"

    released_at = os.environ.get("CODEWALK_RELEASED_AT", "")
    if not released_at:
        released_at = datetime.now(timezone.utc).isoformat()

    short_sha = commit_sha[:7] if commit_sha != "unknown" else "unknown"
    return {
        "codewalk_version": __version__,
        "commit_sha": commit_sha,
        "commit_sha_short": short_sha,
        "released_at": released_at,
        "release_notes_url": f"https://github.com/gupta29470/codewalk/releases/tag/v{__version__}",
        "update_command": "git pull origin master",
        "runtime": "api",
    }


def _cached(key: str, fetcher: Callable[[], Any]) -> Any:
    now = time.monotonic()
    entry = _cache.get(key)
    if entry and (now - entry[0]) < _CACHE_TTL_SEC:
        return entry[1]
    try:
        value = fetcher()
    except Exception:
        value = None
    _cache[key] = (now, value)
    return value


def _cloud_configured() -> tuple[str, str, str] | None:
    server_url = os.getenv("CODEWALK_SERVER_URL", "").rstrip("/")
    repo_name = os.getenv("CODEWALK_REPO_NAME", "")
    repo_token = os.getenv("CODEWALK_REPO_TOKEN", "")
    if server_url and repo_name and repo_token:
        return server_url, repo_name, repo_token
    return None


def _repo_root() -> Path:
    from src.codewalk.api import state

    # For MCP the workspace cwd is the repo root; for the local API the repo
    # path is set per-request by the middleware. Fall back to cwd so staleness
    # checks never crash when no explicit repo path is in scope.
    raw = state._repo_path or os.getcwd()
    return Path(raw).resolve()


def _codewalk_install_root() -> Path | None:
    # src/codewalk/staleness.py → repo root is parents[2]
    candidate = Path(__file__).resolve().parents[2]
    if (candidate / "src" / "codewalk").is_dir():
        return candidate
    return None


def _local_manifest_path() -> Path:
    return _repo_root() / ".codewalk" / "manifest.json"


def _read_local_manifest() -> dict:
    path = _local_manifest_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _fetch_remote_manifest() -> dict | None:
    cfg = _cloud_configured()
    if not cfg:
        return None
    server_url, repo_name, repo_token = cfg

    def _get():
        resp = requests.get(
            f"{server_url}/indexes/{repo_name}/manifest",
            headers={"X-Repo-Token": repo_token},
            timeout=5,
        )
        resp.raise_for_status()
        return resp.json()

    return _cached("remote_manifest", _get)


def _fetch_remote_deployment() -> dict | None:
    server_url = os.getenv("CODEWALK_SERVER_URL", "").rstrip("/")
    if not server_url:
        return None

    def _get():
        resp = requests.get(f"{server_url}/version", timeout=5)
        resp.raise_for_status()
        return resp.json()

    return _cached("remote_deployment", _get)


def _local_codewalk_sha() -> str:
    override = os.getenv("CODEWALK_LOCAL_SHA", "").strip()
    if override:
        return override[:12]

    root = _codewalk_install_root()
    if not root or not (root / ".git").exists():
        return ""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=3,
            check=True,
        )
        return result.stdout.strip()
    except Exception:
        return ""


def _normalize_sha(sha: str) -> str:
    return (sha or "").strip()[:12]


def _index_staleness(tool_name: str) -> dict | None:
    if tool_name in _SKIP_INDEX_BANNER:
        return None

    remote = _fetch_remote_manifest()
    if not remote:
        return None

    local = _read_local_manifest()
    if not local:
        return None

    local_v = local.get("index_version", 0)
    remote_v = remote.get("index_version", 0)
    local_sha = _normalize_sha(local.get("commit_sha", ""))
    remote_sha = _normalize_sha(remote.get("commit_sha", ""))

    behind_version = isinstance(local_v, int) and isinstance(remote_v, int) and remote_v > local_v
    behind_sha = bool(remote_sha and local_sha and remote_sha != local_sha)

    if not behind_version and not behind_sha:
        return None

    return {
        "kind": "index",
        "stale": True,
        "context": "cloud",
        "local_version": local_v,
        "remote_version": remote_v,
        "local_commit_sha": local_sha,
        "remote_commit_sha": remote_sha,
        "indexed_at": remote.get("indexed_at"),
        "title": "New index available on cloud",
        "message": (
            f"Cloud index v{remote_v} (commit {remote_sha[:7]}) "
            f"is newer than local v{local_v} (commit {local_sha[:7] or 'none'})."
        ),
        "action_mcp": "Run codewalk_pull_index",
        "action_api": "Download cloud index (GET /indexes/{owner}/{repo}) or re-analyze after pull",
    }


def _software_staleness() -> dict | None:
    remote = _fetch_remote_deployment()
    if not remote:
        return None

    from src.codewalk import __version__

    local_version = __version__
    remote_version = remote.get("codewalk_version", "")
    local_sha = _normalize_sha(_local_codewalk_sha())
    remote_sha = _normalize_sha(remote.get("commit_sha", ""))

    behind_semver = False
    try:
        from packaging.version import parse as parse_version

        if remote_version and parse_version(str(remote_version)) > parse_version(local_version):
            behind_semver = True
    except Exception:
        pass

    behind_sha = bool(
        remote_sha
        and remote_sha not in ("unknown", "")
        and local_sha
        and remote_sha != local_sha
    )

    remote_manifest = _fetch_remote_manifest() or {}
    min_mcp = remote_manifest.get("minimum_mcp_version") or remote.get("minimum_mcp_version")
    behind_min = False
    if min_mcp:
        try:
            from packaging.version import parse as parse_version

            behind_min = parse_version(str(min_mcp)) > parse_version(local_version)
        except Exception:
            pass

    if not behind_semver and not behind_sha and not behind_min:
        return None

    return {
        "kind": "software",
        "stale": True,
        "context": "cloud" if _is_cloud_mode() else "local",
        "local_version": local_version,
        "remote_version": remote_version,
        "local_commit_sha": local_sha,
        "remote_commit_sha": remote_sha,
        "minimum_mcp_version": min_mcp,
        "title": "New Codewalk version on cloud",
        "message": (
            f"Cloud API v{remote_version or '?'} ({remote_sha[:7] if remote_sha else '?'}) "
            f"differs from local v{local_version}"
            + (f" ({local_sha[:7]})" if local_sha else "")
            + "."
        ),
        "action_mcp": (
            f"{remote.get('update_command', 'git pull origin master')} "
            "in your codewalk install, then restart MCP in Cursor"
        ),
        "action_api": remote.get("update_command", "git pull origin master"),
        "release_notes_url": remote.get("release_notes_url"),
    }


def _is_cloud_mode() -> bool:
    return _cloud_configured() is not None


def _index_build_staleness() -> dict | None:
    """Index on disk was built with an older Codewalk than this MCP/API process."""
    local_manifest = _read_local_manifest()
    if not local_manifest:
        return None

    stored = local_manifest.get("codewalk_version", "")
    if not stored:
        return None

    from src.codewalk import __version__

    try:
        from packaging.version import parse as parse_version

        if parse_version(str(stored)) >= parse_version(__version__):
            return None
    except Exception:
        return None

    cloud = _is_cloud_mode()
    if cloud:
        remote_manifest = _fetch_remote_manifest() or {}
        remote_api = _fetch_remote_deployment() or {}
        remote_stamp = remote_manifest.get("codewalk_version", "")
        api_version = remote_api.get("codewalk_version", "")

        server_behind = False
        try:
            if api_version and remote_stamp:
                server_behind = parse_version(str(api_version)) > parse_version(str(remote_stamp))
        except Exception:
            pass

        if server_behind:
            title = "Cloud index needs server re-index"
            message = (
                f"Downloaded index has codewalk v{stored}; cloud API is v{api_version}. "
                f"Server must re-index before pull will fix this."
            )
            action_mcp = (
                "Wait for post-deploy catch-up on server (or run reset-repo.sh --index), "
                "then run codewalk_pull_index"
            )
            action_api = (
                "Trigger server re-index (reset-repo.sh --index), then download the cloud index"
            )
        else:
            title = "Downloaded cloud index built with older Codewalk"
            message = (
                f"Local .codewalk/ manifest has codewalk v{stored}; running v{__version__}."
            )
            action_mcp = "Run codewalk_pull_index to download a fresh cloud index"
            action_api = "Download cloud index tarball or wait for server re-index, then pull locally"
    else:
        title = "Local index built with older Codewalk"
        message = f"Index was built with v{stored}; running v{__version__}."
        action_mcp = "Run codewalk_incremental_reindex to sync the local index"
        action_api = "POST /incremental-reindex to sync the local index"

    return {
        "kind": "index_build",
        "stale": True,
        "context": "cloud" if cloud else "local",
        "index_codewalk_version": stored,
        "running_version": __version__,
        "title": title,
        "message": message,
        "action_mcp": action_mcp,
        "action_api": action_api,
    }


def staleness_status(tool_name: str | None = None) -> dict:
    """Structured staleness for API GET /staleness and response headers."""
    name = tool_name or ""
    items = [
        _index_staleness(name),
        _software_staleness(),
        _index_build_staleness(),
    ]
    alerts = [item for item in items if item]

    return {
        "has_updates": bool(alerts),
        "index_stale": any(a["kind"] == "index" for a in alerts),
        "software_stale": any(a["kind"] == "software" for a in alerts),
        "index_build_stale": any(a["kind"] == "index_build" for a in alerts),
        "alerts": alerts,
        "version": version_info(),
        "cloud_configured": _cloud_configured() is not None,
    }


def get_staleness_banners(tool_name: str) -> str:
    """Plain-text banners for MCP tool responses."""
    status = staleness_status(tool_name)
    if not status["has_updates"]:
        return ""

    blocks = []
    for alert in status["alerts"]:
        ctx = alert.get("context", "local")
        prefix = "[Cloud] " if ctx == "cloud" else "[Local] "
        if alert["kind"] == "index":
            blocks.append(
                f"⚡ {prefix}NEW INDEX AVAILABLE\n"
                f"  {alert['message']}\n"
                f"  → {alert['action_mcp']}"
            )
        elif alert["kind"] == "software":
            blocks.append(
                f"🆕 {prefix}NEW CODEWALK VERSION\n"
                f"  {alert['message']}\n"
                f"  → {alert['action_mcp']}"
            )
            if alert.get("release_notes_url"):
                blocks.append(f"  Notes: {alert['release_notes_url']}")
        elif alert["kind"] == "index_build":
            blocks.append(
                f"⚠️  {prefix}{alert['title']}\n"
                f"  {alert['message']}\n"
                f"  → {alert['action_mcp']}"
            )
    return "\n\n".join(blocks)


def prepend_staleness_banner(result: str, tool_name: str) -> str:
    """Add a staleness warning banner to a tool result when the local index is outdated."""
    if not isinstance(result, str) or not result.strip():
        return result
    banner = get_staleness_banners(tool_name)
    if not banner:
        return result
    return f"{banner}\n\n---\n\n{result}"


def wrap_tool_fn(fn: Callable[..., Any], tool_name: str) -> Callable[..., Any]:
    """Wrap an MCP tool so its output may include a staleness banner."""
    if getattr(fn, "_codewalk_staleness_wrapped", False):
        return fn

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        result = fn(*args, **kwargs)
        if isinstance(result, str):
            return prepend_staleness_banner(result, tool_name)
        return result

    wrapper._codewalk_staleness_wrapped = True  # type: ignore[attr-defined]
    return wrapper


def install_staleness_wrappers(tool_manager: Any) -> None:
    """Wrap every registered MCP tool to prepend staleness banners."""
    for tool in tool_manager.list_tools():
        tool.fn = wrap_tool_fn(tool.fn, tool.name)


def check_version_message() -> str:
    """Human-readable deployment freshness (codewalk_check_version MCP tool)."""
    remote = _fetch_remote_deployment()
    if not remote:
        return "Cloud not configured. Set CODEWALK_SERVER_URL to enable version checks."

    software = _software_staleness()
    if software:
        return get_staleness_banners("codewalk_check_version") or software["message"]

    from src.codewalk import __version__

    local_sha = _local_codewalk_sha()
    remote_sha = remote.get("commit_sha", "")
    sha_note = ""
    if local_sha and remote_sha and remote_sha != "unknown":
        sha_note = f"  SHAs: local {local_sha[:7]} / cloud {remote_sha[:7]}\n"

    return (
        f"✅ Codewalk is up to date (v{__version__}).\n"
        f"{sha_note}"
        f"  Cloud API: v{remote.get('codewalk_version', '?')} ({remote_sha[:7] if remote_sha else '?'})"
    ).strip()


def should_attach_staleness(path: str) -> bool:
    """True if the tool path should be checked for index staleness."""
    return any(path.startswith(prefix) for prefix in _STALENESS_API_PREFIXES)
