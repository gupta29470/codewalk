import hmac
import hashlib
import secrets
import json
import os
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

@cloud_router.post("/webhooks/github")
async def github_webhook(request: Request):
    """Receive GitHub push events. HMAC-verified. Enqueues indexing job."""
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > MAX_WEBHOOK_SIZE:
        raise HTTPException(413, "Payload too large (max 50MB)")
    body = await request.body()
    if len(body) > MAX_WEBHOOK_SIZE:
        raise HTTPException(413, "Payload too large (max 50MB)")
    signature  = request.headers.get("X-Hub-Signature-256", "")

    if not _verify_webhook(body, signature, os.environ["GITHUB_WEBHOOK_SECRET"]):
        raise HTTPException(403, "Invalid signature")
    
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(422, "Invalid JSON in webhook payload")

    if request.headers.get("X-GitHub-Event") != "push":
        return {"status": "ignored"}                
    
    try:
        branch = payload.get("ref", "").split("/")[-1]
        repo = payload.get("repository", {}).get("name", "")
        commit = payload.get("after", "")
        msg = payload.get("head_commit", {}).get("message", "")
    except (AttributeError, KeyError):
        raise HTTPException(422, "Malformed webhook payload")

    if not repo or not branch:
        raise HTTPException(422, "Missing repository name or branch in payload")

    db = api_state.get_db()
    registered_repo = db.fetchone("SELECT branch FROM repos WHERE name=$1", repo)

    if registered_repo and registered_repo["branch"] == branch:
        db.execute(
            "INSERT INTO jobs (repo_name, commit_sha, commit_message) VALUES ($1, $2, $3)",
            repo, commit, msg,
        )
        return {"status": "queued", "repo": repo, "commit": commit[:7]}
    else:
        return {"status": "ignored", "reason": f"Repo '{repo}' not registered or branch mismatch"}

@cloud_router.post("/admin/register")
async def register_repo(request: Request, x_admin_key: str = Header(alias="X-Admin-Key")):
    """Register a repo. Returns a per-repo download token."""
    if not secrets.compare_digest(x_admin_key, os.environ["ADMIN_API_KEY"]):
        raise HTTPException(403, "Invalid admin key")

    body = await request.json()
    repo_token = "cw_repo_" + secrets.token_urlsafe(16)

    db = api_state.get_db()
    db.execute(
        """INSERT INTO repos (name, github_url, branch, installation_id, repo_token)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (name) DO UPDATE SET github_url=$2, branch=$3,
                                         installation_id=$4, repo_token=$5
        """,
        body["name"], body["github_url"], body.get("branch", "main"),
        body.get("installation_id", ""), repo_token,
    )

    return {"repo_token": repo_token}

@cloud_router.post("/admin/repos")
async def list_repos(x_admin_key: str = Header(alias="X-Admin-Key")):
    if not secrets.compare_digest(x_admin_key, os.environ["ADMIN_API_KEY"]):
        raise HTTPException(403, "Invalid admin key")
    
    db = api_state.get_db()
    rows = db.fetchall("""
        SELECT repo.name, repo.branch,
            job.status, job.commit_sha, job.finished_at, job.error
            FROM repos repo
            LEFT JOIN LATERAL (
                SELECT * FROM jobs WHERE repo_name=repo.name ORDER BY queued_at DESC LIMIT 1
            ) job ON TRUE
            ORDER BY repo.created_at DESC
        """)
    
    return {"repos": [dict(row) for row in rows]}

@cloud_router.get("/indexes/{repo_name}")
async def download_index(
    repo_name: str,
    x_repo_token: str = Header(alias="X-Repo-Token"),
):
    """Stream the latest index as tar.gz. Authenticated per repo."""
    db = api_state.get_db()
    row = db.fetchone("SELECT repo_token FROM repos WHERE name=$1", repo_name)
    if not row or not secrets.compare_digest(row["repo_token"], x_repo_token):
        raise HTTPException(403, "Invalid token")
    
    storage = os.environ.get("INDEX_STORAGE_PATH", "/var/codewalk")
    index_path = Path(storage) / repo_name / "latest"
    if not index_path.exists():
        raise HTTPException(404, "No index available yet")
    
    import tarfile

    def stream_tarball():
        # Build tar.gz on the same filesystem, then stream in chunks.
        # Avoids filling /tmp and cross-filesystem copies.
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
    row = db.fetchone("SELECT repo_token FROM repos WHERE name=$1", repo_name)
    if not row or not secrets.compare_digest(row["repo_token"], x_repo_token):
        raise HTTPException(403, "Invalid token")

    storage = os.environ.get("INDEX_STORAGE_PATH", "/var/codewalk")
    manifest_path = Path(storage) / repo_name / "latest" / "manifest.json"

    if not manifest_path.exists():
        raise HTTPException(404, "No manifest available")
    
    with open(manifest_path) as file:
        return json.load(file)
