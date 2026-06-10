import hmac
import hashlib
import secrets
import json
import os
import subprocess
from pathlib import Path

from fastapi import APIRouter, Request, HTTPException, Header, FastAPI
from fastapi.responses import StreamingResponse

from src.codewalk.api import state as api_state

cloud_router = APIRouter()

MAX_WEBHOOK_SIZE = 50 * 1024 * 1024  # 50MB


def is_cloud_enabled() -> bool:
    """Check if all required cloud environment variables are set."""
    return bool(
        os.environ.get("DATABASE_URL")
        and os.environ.get("GITHUB_APP_ID")
        and os.environ.get("GITHUB_APP_PRIVATE_KEY")
    )


def start_cloud_worker() -> None:
    """Start the background worker thread for cloud indexing jobs.

    Safe to call even when cloud is not configured — it becomes a no-op.
    Called from the FastAPI lifespan startup handler in main.py.
    """
    if not is_cloud_enabled():
        return

    import threading
    from src.codewalk.worker.indexer import worker_loop

    db_url       = os.environ["DATABASE_URL"]
    app_id       = os.environ["GITHUB_APP_ID"]
    private_key  = os.environ["GITHUB_APP_PRIVATE_KEY"]
    storage_path = os.environ.get("INDEX_STORAGE_PATH", "/var/codewalk")

    threading.Thread(
        target=worker_loop,
        args=(db_url, app_id, private_key, storage_path),
        daemon=True,
    ).start()


def setup_cloud(app: FastAPI) -> None:
    """Register cloud routes if cloud env vars are set.

    Call this once after creating the FastAPI app. Does nothing if cloud
    is not configured. Worker startup is handled separately via lifespan.
    """
    if is_cloud_enabled():
        app.include_router(cloud_router)


def _verify_webhook(body: bytes, signature: str, secret: str) -> bool:
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def _get_or_create_repo(db, full_name: str, clone_url: str, installation_id: str, branch: str = "master") -> dict:
    """Get repo from DB or auto-register it on first webhook."""
    parts = full_name.split("/")
    owner = parts[0] if len(parts) > 0 else ""
    name  = parts[1] if len(parts) > 1 else full_name
    storage_path = os.environ.get("INDEX_STORAGE_PATH", "/var/codewalk")
    repo_storage = f"{storage_path}/repos/{full_name}"

    row = db.fetchone("SELECT * FROM repos WHERE full_name=$1", full_name)
    if row:
        return dict(row)

    # Auto-register on first webhook
    repo_token = "cw_repo_" + secrets.token_urlsafe(16)
    db.execute(
        """INSERT INTO repos (full_name, name, owner, clone_url, branch, installation_id, repo_token, storage_path)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        ON CONFLICT (full_name) DO NOTHING
        """,
        full_name, name, owner, clone_url, branch, installation_id, repo_token, repo_storage,
    )
    row = db.fetchone("SELECT * FROM repos WHERE full_name=$1", full_name)
    return dict(row) if row else {}


def _clone_or_pull_repo(repo: dict, branch: str) -> Path:
    """Clone repo if not exists, or git pull if it does."""
    storage_path = repo.get("storage_path") or f"/var/codewalk/repos/{repo['full_name']}"
    repo_path = Path(storage_path)

    if repo_path.exists() and (repo_path / ".git").exists():
        # Pull latest
        subprocess.run(
            ["git", "pull", "origin", branch],
            cwd=str(repo_path),
            check=False,
            capture_output=True,
        )
    else:
        # Clone fresh
        repo_path.parent.mkdir(parents=True, exist_ok=True)
        clone_url = repo.get("clone_url", f"https://github.com/{repo['full_name']}.git")
        subprocess.run(
            ["git", "clone", "--depth=1", "--branch", branch, clone_url, str(repo_path)],
            check=False,
            capture_output=True,
        )

    return repo_path


def _run_incremental_index(repo_path: Path, repo_full_name: str) -> dict:
    """Run incremental re-index on a repo path."""
    from src.codewalk.pipeline import incremental_reindex

    try:
        result = incremental_reindex(str(repo_path))
        return {
            "status": "success",
            "files_scanned": result.get("files_scanned", 0),
            "files_changed": result.get("files_changed", 0),
            "files_new": result.get("files_new", 0),
            "files_deleted": result.get("files_deleted", 0),
        }
    except Exception as e:
        return {"status": "failed", "error": str(e)}


@cloud_router.post("/webhooks/github")
async def github_webhook(request: Request):
    """Receive GitHub push/installation events. HMAC-verified. Auto-registers repo, skips unchanged, incremental re-index."""
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > MAX_WEBHOOK_SIZE:
        raise HTTPException(413, "Payload too large (max 50MB)")
    body = await request.body()
    if len(body) > MAX_WEBHOOK_SIZE:
        raise HTTPException(413, "Payload too large (max 50MB)")

    signature = request.headers.get("X-Hub-Signature-256", "")
    delivery_id = request.headers.get("X-GitHub-Delivery", "")
    event_type = request.headers.get("X-GitHub-Event", "")

    if not _verify_webhook(body, signature, os.environ["GITHUB_WEBHOOK_SECRET"]):
        raise HTTPException(403, "Invalid signature")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(422, "Invalid JSON in webhook payload")

    # ── Log webhook delivery ──────────────────────────────────────────
    db = api_state.get_db()
    repo_full_name = payload.get("repository", {}).get("full_name", "")
    commit_sha = payload.get("after", "")
    db.execute(
        "INSERT INTO webhook_deliveries (event_type, delivery_id, repo_full_name, commit_sha, payload_size, status) VALUES ($1, $2, $3, $4, $5, $6)",
        event_type, delivery_id, repo_full_name, commit_sha, len(body), "received",
    )

    # ── Handle ping (GitHub App test) ─────────────────────────────────
    if event_type == "ping":
        return {"status": "pong"}

    # ── Handle installation events ────────────────────────────────────
    if event_type in ("installation", "installation_repositories"):
        return {"status": "ignored", "reason": "Installation events are handled lazily on first push"}

    # ── Only process push events ──────────────────────────────────────
    if event_type != "push":
        return {"status": "ignored", "reason": f"Event type '{event_type}' not handled"}

    # ── Extract push payload ──────────────────────────────────────────
    try:
        branch = payload.get("ref", "").replace("refs/heads/", "")
        repo_full_name = payload.get("repository", {}).get("full_name", "")
        clone_url = payload.get("repository", {}).get("clone_url", "")
        commit = payload.get("after", "")
        msg = payload.get("head_commit", {}).get("message", "")
        installation_id = str(payload.get("installation", {}).get("id", ""))
        if commit == "0000000000000000000000000000000000000000":
            # Branch deleted
            return {"status": "ignored", "reason": "Branch deleted"}
    except (AttributeError, KeyError):
        raise HTTPException(422, "Malformed webhook payload")

    if not repo_full_name or not branch:
        raise HTTPException(422, "Missing repository name or branch in payload")

    if not commit:
        return {"status": "ignored", "reason": "No commit SHA in payload"}

    # ── Get or auto-register repo ─────────────────────────────────────
    repo = _get_or_create_repo(db, repo_full_name, clone_url, installation_id, branch)
    if not repo:
        raise HTTPException(500, "Failed to register repository")

    # ── Skip if SHA unchanged ─────────────────────────────────────────
    if repo.get("last_indexed_sha") == commit:
        db.execute(
            "UPDATE webhook_deliveries SET status=$1 WHERE delivery_id=$2",
            "ignored_unchanged", delivery_id,
        )
        return {"status": "skipped", "reason": "Commit already indexed", "sha": commit[:7]}

    # ── Queue job and update status ───────────────────────────────────
    db.execute(
        "INSERT INTO jobs (repo_name, commit_sha, commit_message, status) VALUES ($1, $2, $3, $4)",
        repo_full_name, commit, msg, "queued",
    )
    db.execute(
        "UPDATE repos SET index_status=$1, updated_at=NOW() WHERE full_name=$2",
        "indexing", repo_full_name,
    )

    # ── Clone/pull and incremental index (async-friendly: offload to thread) ──
    import asyncio
    loop = asyncio.get_event_loop()

    def _do_index():
        try:
            repo_path = _clone_or_pull_repo(repo, branch)
            result = _run_incremental_index(repo_path, repo_full_name)

            if result["status"] == "success":
                db.execute(
                    "UPDATE repos SET last_indexed_sha=$1, index_status=$2, updated_at=NOW() WHERE full_name=$3",
                    commit, "ready", repo_full_name,
                )
                db.execute(
                    "UPDATE jobs SET status=$1, finished_at=NOW() WHERE repo_name=$2 AND commit_sha=$3 AND status='queued'",
                    "done", repo_full_name, commit,
                )
                db.execute(
                    "UPDATE webhook_deliveries SET status=$1 WHERE delivery_id=$2",
                    "processed", delivery_id,
                )
                return result
            else:
                error_msg = result.get("error", "Unknown indexing error")
                db.execute(
                    "UPDATE repos SET index_status=$1, updated_at=NOW() WHERE full_name=$2",
                    "failed", repo_full_name,
                )
                db.execute(
                    "UPDATE jobs SET status=$1, error=$2, finished_at=NOW() WHERE repo_name=$3 AND commit_sha=$4 AND status='queued'",
                    "failed", error_msg, repo_full_name, commit,
                )
                db.execute(
                    "UPDATE webhook_deliveries SET status=$1, error=$2 WHERE delivery_id=$3",
                    "failed", error_msg, delivery_id,
                )
                return result
        except Exception as e:
            db.execute(
                "UPDATE repos SET index_status=$1, updated_at=NOW() WHERE full_name=$2",
                "failed", repo_full_name,
            )
            db.execute(
                "UPDATE jobs SET status=$1, error=$2, finished_at=NOW() WHERE repo_name=$3 AND commit_sha=$4 AND status='queued'",
                "failed", str(e), repo_full_name, commit,
            )
            db.execute(
                "UPDATE webhook_deliveries SET status=$1, error=$2 WHERE delivery_id=$3",
                "failed", str(e), delivery_id,
            )
            return {"status": "failed", "error": str(e)}

    # Run indexing in background thread so webhook responds immediately
    import threading
    threading.Thread(target=_do_index, daemon=True).start()

    return {
        "status": "queued",
        "repo": repo_full_name,
        "commit": commit[:7],
        "branch": branch,
        "previous_sha": repo.get("last_indexed_sha", "")[:7] if repo.get("last_indexed_sha") else "none",
    }


@cloud_router.post("/admin/register")
async def register_repo(request: Request, x_admin_key: str = Header(alias="X-Admin-Key")):
    """Register a repo manually. Returns a per-repo download token."""
    if not secrets.compare_digest(x_admin_key, os.environ["ADMIN_API_KEY"]):
        raise HTTPException(403, "Invalid admin key")

    body = await request.json()
    full_name = body.get("full_name") or body.get("name", "")
    if "/" not in full_name:
        raise HTTPException(422, "full_name must be in 'owner/repo' format")

    parts = full_name.split("/")
    owner = parts[0]
    name = parts[1]
    clone_url = body.get("github_url", f"https://github.com/{full_name}.git")
    branch = body.get("branch", "master")
    installation_id = body.get("installation_id", "")
    repo_token = "cw_repo_" + secrets.token_urlsafe(16)
    storage_path = os.environ.get("INDEX_STORAGE_PATH", "/var/codewalk")
    repo_storage = f"{storage_path}/repos/{full_name}"

    db = api_state.get_db()
    db.execute(
        """INSERT INTO repos (full_name, name, owner, clone_url, branch, installation_id, repo_token, storage_path)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        ON CONFLICT (full_name) DO UPDATE SET clone_url=$4, branch=$5, installation_id=$6, repo_token=$7, storage_path=$8, updated_at=NOW()
        """,
        full_name, name, owner, clone_url, branch, installation_id, repo_token, repo_storage,
    )

    return {"repo_token": repo_token, "full_name": full_name, "status": "registered"}


@cloud_router.post("/admin/repos")
async def list_repos(x_admin_key: str = Header(alias="X-Admin-Key")):
    if not secrets.compare_digest(x_admin_key, os.environ["ADMIN_API_KEY"]):
        raise HTTPException(403, "Invalid admin key")

    db = api_state.get_db()
    rows = db.fetchall("""
        SELECT r.full_name, r.name, r.owner, r.branch, r.last_indexed_sha, r.index_status, r.created_at, r.updated_at,
            j.status AS job_status, j.commit_sha AS job_commit, j.finished_at AS job_finished, j.error AS job_error
        FROM repos r
        LEFT JOIN LATERAL (
            SELECT * FROM jobs WHERE repo_name=r.full_name ORDER BY queued_at DESC LIMIT 1
        ) j ON TRUE
        ORDER BY r.updated_at DESC
    """)

    return {"repos": [dict(row) for row in rows]}


@cloud_router.post("/admin/index")
async def trigger_index(
    request: Request,
    x_admin_key: str = Header(alias="X-Admin-Key"),
):
    """Manually trigger indexing for a registered repo."""
    if not secrets.compare_digest(x_admin_key, os.environ["ADMIN_API_KEY"]):
        raise HTTPException(403, "Invalid admin key")

    body = await request.json()
    full_name = body.get("full_name", "")
    branch = body.get("branch", "")

    db = api_state.get_db()
    repo = db.fetchone("SELECT * FROM repos WHERE full_name=$1", full_name)
    if not repo:
        raise HTTPException(404, f"Repo '{full_name}' not registered")

    repo = dict(repo)
    repo_path = _clone_or_pull_repo(repo, branch or repo["branch"])
    result = _run_incremental_index(repo_path, full_name)

    if result["status"] == "success":
        db.execute(
            "UPDATE repos SET index_status=$1, updated_at=NOW() WHERE full_name=$2",
            "ready", full_name,
        )
    else:
        db.execute(
            "UPDATE repos SET index_status=$1, updated_at=NOW() WHERE full_name=$2",
            "failed", full_name,
        )

    return {"repo": full_name, **result}


@cloud_router.get("/indexes/{repo_name}")
async def download_index(
    repo_name: str,
    x_repo_token: str = Header(alias="X-Repo-Token"),
):
    """Stream the latest index as tar.gz. Authenticated per repo."""
    db = api_state.get_db()
    row = db.fetchone("SELECT repo_token FROM repos WHERE full_name=$1", repo_name)
    if not row or not secrets.compare_digest(row["repo_token"], x_repo_token):
        raise HTTPException(403, "Invalid token")

    storage = os.environ.get("INDEX_STORAGE_PATH", "/var/codewalk")
    index_path = Path(storage) / repo_name / "latest"
    if not index_path.exists():
        raise HTTPException(404, "No index available yet")

    import tarfile

    def stream_tarball():
        tmp_path = Path(storage) / f"{repo_name}.tmp.tar.gz"
        try:
            with tarfile.open(tmp_path, "w:gz") as tar:
                tar.add(index_path, arcname=".codewalk")
            with open(tmp_path, "rb") as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    yield chunk
        finally:
            tmp_path.unlink(missing_ok=True)

    return StreamingResponse(stream_tarball(), media_type="application/gzip")


@cloud_router.get("/indexes/{repo_name}/manifest")
async def get_manifest(
    repo_name: str,
    x_repo_token: str = Header(alias="X-Repo-Token"),
):
    db = api_state.get_db()
    row = db.fetchone("SELECT repo_token FROM repos WHERE full_name=$1", repo_name)
    if not row or not secrets.compare_digest(row["repo_token"], x_repo_token):
        raise HTTPException(403, "Invalid token")

    storage = os.environ.get("INDEX_STORAGE_PATH", "/var/codewalk")
    manifest_path = Path(storage) / repo_name / "latest" / "manifest.json"

    if not manifest_path.exists():
        raise HTTPException(404, "No manifest available")

    with open(manifest_path) as file:
        return json.load(file)
