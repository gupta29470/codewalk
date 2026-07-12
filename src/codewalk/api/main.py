"""FastAPI application exposing Codewalk endpoints for analysis, chat, review, search, docs, voice, and cloud."""
import logging
import os
import sys
import json
import base64
import asyncio
import queue
from pathlib import Path
from contextlib import asynccontextmanager

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi import UploadFile, File, Form
from fastapi.responses import JSONResponse


from src.codewalk.api.models import (
    AnalyzeRequest, AnalyzeResponse,
    ApplyAndVerifyRequest, ApplyAndVerifyResponse,
    CancelReviewRequest, CancelReviewResponse,
    ChatRequest, ChatResponse,
    ModuleResponse, OverviewResponse,
    BlastRadiusResponse,
    ReviewRequest, ReviewResponse, ReviewStreamRequest,
    ReviewVerdictRequest, ReviewVerdictResponse,
    GuidelinesRequest,
    DocsIndexRequest, DocsAskRequest, DocsSearchRequest, ApproveRequest,
    ResearchRequest, ResearchResponse, ApplyFixesRequest, ApplyFixesResponse, AppliedFix,
    SemanticSearchRequest, SemanticSearchResponse, SemanticSearchResult,
    StaticAnalysisRequest, StaticAnalysisResponse, StaticAnalysisIssue,
    TestRunRequest, TestRunResponse,
    ExpandQueryRequest, ExpandQueryResponse,
    RerankRequest, RerankResponse,
    SymbolLookupRequest, SymbolLookupResponse,
)
from src.codewalk.api import state
from langchain_core.messages import AIMessage, ToolMessage
from src.codewalk.agent.graph import proposed_write_action
from src.codewalk.ingestion.scanner import scan_directory
from src.codewalk.ingestion.tech_detect import detect_tech_stack
from src.codewalk.generation.overview_generator import generate_overview
from src.codewalk.analysis.reading_order import generate_reading_order
from src.codewalk.generation.flow_generator import generate_execution_flow
from src.codewalk.config import settings
from src.codewalk import __version__
from src.codewalk.analysis.blast_radius import calculate_full_blast_map
from src.codewalk.query import (
    compute_file_risks, resolve_module_with_fallback, short_name,
)
from src.codewalk.log import log as _log
from src.codewalk.errors import classify_error


logger = logging.getLogger("codewalk")


def _resolve_repo_path(repo_path: str | None = None) -> str:
    """Resolve repo root from explicit path, in-process state, or discovery.

    Resolution order:
      1. Explicit repo_path from the request.
      2. In-process state fallback (set by a previous /analyze call).
      3. codewalk.yaml discovery upward from cwd (auto-creating if missing).

    Raises HTTPException(400) if no repo root can be determined.
    """
    from src.codewalk.repo_discovery import ensure_codewalk_yaml, RepoNotFoundError

    path = (repo_path or "").strip()
    if path:
        if not os.path.isdir(path):
            raise HTTPException(status_code=400, detail=f"repo_path is not a directory: {path}")

        # If an index is already loaded for a different repo, refuse to silently
        # use the wrong store/modules/graph. The caller must re-analyze first.
        if state.ensure_initialized():
            current_repo = state.get_repo_path()
            if Path(path).resolve() != Path(current_repo).resolve():
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Requested repo {path} does not match the currently "
                        f"loaded index ({current_repo}). Call POST /analyze first."
                    ),
                )

        try:
            root = ensure_codewalk_yaml(path, create=True)
        except RepoNotFoundError as e:
            raise HTTPException(status_code=400, detail=str(e))
        root_str = str(root)
        state.set_repo_path(root_str)
        return root_str

    try:
        return state.get_repo_path()
    except RuntimeError:
        pass

    try:
        root = ensure_codewalk_yaml(create=True)
    except RepoNotFoundError as e:
        raise HTTPException(status_code=400, detail=str(e))
    root_str = str(root)
    state.set_repo_path(root_str)
    return root_str


# Backward-compatible aliases for existing call sites
def _require_repo_path(repo_path: str | None) -> str:
    """Resolve repo root (explicit path or codewalk.yaml discovery)."""
    return _resolve_repo_path(repo_path)


def _ensure_repo_path(repo_path: str | None = None) -> str:
    """Resolve repo root (explicit path, discovery, or in-process state)."""
    return _resolve_repo_path(repo_path)


def _resolve_extras_paths(repo_path: str, codewalk_config) -> str:
    """Resolve docs_path from codewalk.yaml, relative to repo_path."""
    docs_path = codewalk_config.docs_path
    if docs_path and not os.path.isabs(docs_path):
        docs_path = os.path.join(repo_path, docs_path)
    return docs_path


# ─── Lifespan handler (replaces deprecated @app.on_event) ─────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle for the FastAPI app.

    Startup:  Start cloud background worker if cloud env vars are set.
    Shutdown: (nothing to clean up yet — worker is a daemon thread)
    """
    from src.codewalk.api.cloud import start_cloud_worker
    start_cloud_worker()
    yield


# ─── Create the FastAPI app ─────────────────────────────────────────

app = FastAPI(
    title="Codewalk API",
    description="AI-powered codebase onboarding tool",
    version=__version__,
    lifespan=lifespan,
)

# Parse CORS origins from env (comma-separated) or fall back to "*"
_cors_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()] or ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Simple in-memory rate limiting ──────────────────────────────────
import time
from collections import defaultdict

_RATE_LIMIT_REQUESTS = int(os.getenv("RATE_LIMIT_REQUESTS", "60"))
_RATE_LIMIT_WINDOW = int(os.getenv("RATE_LIMIT_WINDOW", "60"))
_RATE_LIMIT_DISABLED = os.getenv("DISABLE_RATE_LIMIT", "").lower() in ("true", "1", "yes")
_rate_limit_store: dict[str, list[float]] = defaultdict(list)
_rate_limit_lock = asyncio.Lock()

@app.middleware("http")
async def rate_limit_middleware(request, call_next):
    """Simple sliding-window rate limiter per client IP."""
    if _RATE_LIMIT_DISABLED:
        return await call_next(request)

    client_ip = request.client.host if request.client else "unknown"
    now = time.time()
    async with _rate_limit_lock:
        window = _rate_limit_store[client_ip]
        # Remove entries outside the window
        window[:] = [t for t in window if now - t < _RATE_LIMIT_WINDOW]
        if len(window) >= _RATE_LIMIT_REQUESTS:
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded. Please try again later."},
            )
        window.append(now)
    return await call_next(request)

# ─── Cloud mode middleware ────────────────────────────────────────────
_CLOUD_DISABLED_PREFIXES = (
    "/analyze", "/chat", "/overview", "/modules",
    "/blast-radius", "/reading-order", "/execution-flow",
    "/refresh", "/incremental-reindex", "/review",
    "/voice", "/cycles", "/architecture", "/knowledge-graph", "/docs",
)

@app.middleware("http")
async def cloud_mode_middleware(request, call_next):
    """Block query/analysis endpoints when running in cloud-only indexing mode."""
    from src.codewalk.api.cloud import is_cloud_enabled
    if is_cloud_enabled():
        path = request.url.path
        if any(path.startswith(p) for p in _CLOUD_DISABLED_PREFIXES):
            return JSONResponse(
                status_code=400,
                content={
                    "detail": (
                        "Cloud mode serves indexes only. "
                        "Download the index via GET /indexes/{owner}/{repo} "
                        "and query locally with MCP."
                    )
                },
            )
    return await call_next(request)


@app.middleware("http")
async def staleness_middleware(request, call_next):
    """Attach index/software staleness to local API responses (same checks as MCP)."""
    from src.codewalk.api.cloud import is_cloud_enabled
    from src.codewalk.staleness import should_attach_staleness, staleness_status

    response = await call_next(request)

    if is_cloud_enabled() or not should_attach_staleness(request.url.path):
        return response

    status = staleness_status()
    if not status.get("has_updates"):
        return response

    response.headers["X-Codewalk-Staleness"] = json.dumps(status)
    return response


@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    """Convert unhandled exceptions to user-friendly messages.

    HTTPException and RequestValidationError are passed through unchanged so
    clients receive the correct 4xx status codes.
    """
    if isinstance(exc, HTTPException):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    if isinstance(exc, RequestValidationError):
        return JSONResponse(status_code=422, content={"detail": exc.errors()})
    user_message = classify_error(exc)
    _log(f"[api] Error: {exc}")
    return JSONResponse(
        status_code=500,
        content={"detail": user_message},
    )

# ─── POST /analyze ───────────────────────────────────────────────────
@app.post("/analyze", response_model=AnalyzeResponse)
def analyze(request: AnalyzeRequest):
    """Index a codebase: scan → chunk → embed → store → build agent.

    Modes:
        auto    — load complete index, full build if empty, or warn if behind (default)
        reindex — smart re-index (only changed/new/deleted files); resumes partial indexes
        full    — nuke everything and re-embed from scratch
    """
    try:
        from src.codewalk.codewalk_config import load_codewalk_yaml

        request.repo_path = _require_repo_path(request.repo_path)
        state.set_repo_path(request.repo_path)
        if not request.collection_name:
            request.collection_name = state.get_collection_name()
        codewalk_config = load_codewalk_yaml(request.repo_path)
        docs_path = _resolve_extras_paths(request.repo_path, codewalk_config)

        result = state.analyze_or_reindex_index(
            request.repo_path,
            docs_path=docs_path,
            mode=request.index_mode,
        )

        status = result.get("status")
        if status == "behind":
            sample = result.get("missing_sample", [])
            sample_text = ""
            if sample:
                sample_text = "; examples: " + ", ".join(sample)
                if result["missing_count"] > len(sample):
                    sample_text += f" and {result['missing_count'] - len(sample)} more"
            message = (
                f"⚠️ Indexing is behind from repo. "
                f"Missing {result['missing_count']} files{sample_text}. "
                f"Run /incremental-reindex or /analyze with index_mode='reindex' to sync."
            )
            return AnalyzeResponse(
                status="behind",
                repo_path=request.repo_path,
                files_scanned=0,
                chunks_created=0,
                modules=[],
                message=message,
            )

        return AnalyzeResponse(
            status="complete",
            repo_path=request.repo_path,
            files_scanned=result["files_scanned"],
            chunks_created=result["chunks_embedded"],
            modules=list(state._modules_result["modules"].keys()),
            message="",
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/analyze/stream")
def analyze_stream(request: AnalyzeRequest):
    """Stream analysis progress via Server-Sent Events."""
    request.repo_path = _require_repo_path(request.repo_path)

    async def event_stream():
        """Async generator that yields SSE events around analyze_or_reindex_index."""
        try:
            from src.codewalk.codewalk_config import load_codewalk_yaml

            state.set_repo_path(request.repo_path)
            if not request.collection_name:
                request.collection_name = state.get_collection_name()
            codewalk_config = load_codewalk_yaml(request.repo_path)
            docs_path = _resolve_extras_paths(request.repo_path, codewalk_config)

            yield f"data: {json.dumps({'step': 'init', 'message': 'Checking existing index...'})}\n\n"

            index_result = await asyncio.to_thread(
                state.analyze_or_reindex_index,
                request.repo_path,
                docs_path=docs_path,
                mode=request.index_mode,
            )

            status = index_result.get("status")
            if status == "ready":
                msg = f"Loaded existing index ({index_result['chunks_embedded']} chunks)"
                yield f"data: {json.dumps({'step': 'skip', 'message': msg})}\n\n"
            elif status == "behind":
                sample = index_result.get("missing_sample", [])
                sample_text = ""
                if sample:
                    sample_text = "; examples: " + ", ".join(sample)
                    if index_result["missing_count"] > len(sample):
                        sample_text += f" and {index_result['missing_count'] - len(sample)} more"
                msg = (
                    f"Indexing is behind from repo — "
                    f"missing {index_result['missing_count']} files{sample_text}. "
                    f"Run /incremental-reindex or /analyze with index_mode='reindex' to sync."
                )
                yield f"data: {json.dumps({'step': 'behind', 'message': msg})}\n\n"
            elif status == "reindexed":
                msg = f"Reindexed {index_result['files_scanned']} files ({index_result['chunks_embedded']} chunks)"
                yield f"data: {json.dumps({'step': 'reindex', 'message': msg})}\n\n"
            else:
                msg = f"Indexed {index_result['files_scanned']} files ({index_result['chunks_embedded']} chunks)"
                yield f"data: {json.dumps({'step': 'store', 'message': msg})}\n\n"

            if status == "behind":
                yield f"data: {json.dumps({'step': 'done', 'message': 'Analysis check complete', 'result': {'status': 'behind', 'repo_path': request.repo_path, 'files_scanned': 0, 'chunks_created': 0, 'modules': []}})}\n\n"
            else:
                yield f"data: {json.dumps({'step': 'analyze', 'message': 'Building dependency graph...'})}\n\n"
                num_modules = len(state._modules_result['modules'])
                yield f"data: {json.dumps({'step': 'analyze', 'message': f'Detected {num_modules} modules'})}\n\n"
                yield f"data: {json.dumps({'step': 'done', 'message': 'Analysis complete!', 'result': {'status': 'complete', 'repo_path': request.repo_path, 'files_scanned': index_result['files_scanned'], 'chunks_created': index_result['chunks_embedded'], 'modules': list(state._modules_result['modules'].keys())}})}\n\n"

        except HTTPException:
            raise
        except Exception as e:
            yield f"data: {json.dumps({'step': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
    
def _run_chat_stream(agent, message: str, config: dict) -> list[dict]:
    """Run the agent synchronously and collect SSE event payloads.

    Uses stream_mode='messages' so the sync SqliteSaver checkpointer works.
    Token events contain full assistant messages (not per-token deltas).
    """
    events: list[dict] = []
    for msg, _metadata in agent.stream(
        {"messages": [("human", message)]},
        config=config,
        stream_mode="messages",
    ):
        if isinstance(msg, ToolMessage):
            name = msg.name or "tool"
            events.append({"type": "tool_start", "name": name})
            events.append({"type": "tool_end", "name": name})
        elif isinstance(msg, AIMessage) and msg.content and not msg.tool_calls:
            events.append({"type": "token", "content": msg.content})

    graph_state = agent.get_state(config)
    if graph_state.next:
        events.append({
            "type": "interrupted",
            "proposed_action": proposed_write_action(graph_state.values["messages"]).replace("\n", "; "),
        })
    else:
        events.append({"type": "done"})
    return events

# ─── POST /chat ──────────────────────────────────────────────────────
@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest):
    """Run one turn of the LangGraph agent. Returns an answer or a HITL approval request."""
    try:
        state.require_index()
        agent = state.get_agent()
        config = {
            "configurable": {
                "thread_id": request.thread_id
            }
        }
        result = agent.invoke(
            {"messages": [("human", request.message)]},
            config=config
        )

        # Check if graph was interrupted (HITL — waiting for apply_fix approval)
        graph_state = agent.get_state(config)
        if graph_state.next:
            proposed = proposed_write_action(graph_state.values["messages"])
            return ChatResponse(
                answer="The agent wants to apply a code fix. Approve or reject it via POST /chat/approve.",
                thread_id=request.thread_id,
                interrupted=True,
                proposed_action=proposed,
            )

        answer = result["messages"][-1].content
        return ChatResponse(answer=answer, thread_id=request.thread_id)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ─── POST /chat/stream ───────────────────────────────────────────────
@app.post("/chat/stream")
def chat_stream(request: ChatRequest):
    """Stream the agent's response using Server-Sent Events.

    Each SSE message is a JSON object with a `type` field:
      {"type": "token",      "content": "..."}  assistant message (full text)
      {"type": "tool_start", "name": "..."}     tool call began
      {"type": "tool_end",   "name": "..."}     tool call finished
      {"type": "interrupted", "proposed_action": "..."}  HITL pause for apply_fix
      {"type": "done"}                           stream complete
      {"type": "error",      "message": "..."}  something went wrong
    """
    async def event_generator():
        try:
            state.require_index()
            agent = state.get_agent()

            config = {
                "configurable": {
                    "thread_id": request.thread_id
                }
            }

            events = await asyncio.to_thread(
                _run_chat_stream, agent, request.message, config
            )
            for event in events:
                yield f"data: {json.dumps(event)}\n\n"

        except RuntimeError as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
        except HTTPException:
            raise
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

    
# ─── GET /overview ───────────────────────────────────────────────────
@app.get("/overview", response_model=OverviewResponse)
def overview():
    """Get the project overview (tech stack, modules, LLM summary)."""
    try:
        state.require_index()
        modules_result = state.get_modules_result()

        # Detect tech stack
        analyze_result = state.get_analyze_result()
        tech = detect_tech_stack(analyze_result.get("repo_path") or state.get_repo_path())

        # Generate overview (calls LLM)
        overview_text = generate_overview(tech, modules_result)

        deps = state.get_deps()
        runtime = state._graph_runtime or deps["graph"]
        blast_map = calculate_full_blast_map(runtime)
        top_files = [item["file"] for item in blast_map["blast_map"][:30]]
        top_risky, _ = compute_file_risks(top_files, runtime)

        return OverviewResponse(
            tech_stack=tech,
            total_files=modules_result["stats"]["total_files"],
            total_modules=modules_result["stats"]["total_modules"],
            modules=list(modules_result["modules"].keys()),
            diagram=None,
            overview_text=overview_text,
            riskiest_files=top_risky,
        )
    
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
# ─── GET /modules/{name} ────────────────────────────────────────────
@app.get("/modules/{module_name}", response_model=ModuleResponse)
def get_module(module_name: str):
    """Get details about a specific module."""
    try:
        state.require_index()
        module_result = state.get_modules_result()
        modules = module_result["modules"]
        module_graph = module_result["module_graph"]

        actual_name, info, matched_as_feature = resolve_module_with_fallback(
            module_name, module_result, files=state.get_files()
        )

        if not actual_name:
            available = ", ".join(sorted(modules.keys()))
            raise HTTPException(
                status_code=404,
                detail=f"Module '{module_name}' not found. Available: {available}",
            )

        depends_on = module_graph.get(actual_name, [])
        depended_by = [
            other for other, deps in module_graph.items()
            if actual_name in deps
        ]

        deps = state.get_deps()
        runtime = state._graph_runtime or deps["graph"]
        file_risks, max_risk = compute_file_risks(sorted(info["files"]), runtime)

        return ModuleResponse(
            name=f"{module_name} (inside '{actual_name}')" if matched_as_feature else actual_name,
            file_count=info["file_count"],
            files=sorted(info["files"]),
            languages=info["languages"],
            depends_on=depends_on,
            depended_by=depended_by,
            blast_radius=file_risks, 
            module_risk=max_risk,
        )
    
    except HTTPException:
        raise
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ─── GET /blast-radius ───────────────────────────────────────────────
@app.get("/blast-radius/{module_name}", response_model=BlastRadiusResponse)
@app.get("/blast-radius", response_model=BlastRadiusResponse)
def get_blast_radius_for_module(module_name: str = ""):
    """Get blast radius for files. Optionally scope to a module."""
    try:
        state.require_index()
        modules_result = state.get_modules_result()
        deps = state.get_deps()
        runtime = state._graph_runtime or deps["graph"]

        # Determine scope
        if module_name:
            modules = modules_result["modules"]
            actual_name, _, _ = resolve_module_with_fallback(module_name, modules_result)
            if not actual_name:
                available = ", ".join(sorted(modules.keys()))
                raise HTTPException(
                    status_code=404,
                    detail=f"Module '{module_name}' not found. Available: {available}",
                )
            target_files = sorted(modules[actual_name]["files"])
            scope = actual_name
        else:
            target_files = sorted(deps["graph"].keys())
            scope = "all"

        file_results, max_risk = compute_file_risks(target_files, runtime)

        return BlastRadiusResponse(
            module=scope,
            module_risk=max_risk,
            total_files=len(file_results),
            files=file_results,
        )

    except HTTPException:
        raise
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ─── GET /modules (list all) ────────────────────────────────────────
@app.get("/modules")
def list_modules():
    """List all available modules."""
    try:
        state.require_index()
        modules_result = state.get_modules_result()
        return {
            "modules": list(modules_result["modules"].keys()),
            "total": modules_result["stats"]["total_modules"],
        }
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    
# ─── GET /reading-order ────────────────────────────────────────
@app.get("/reading-order")
def get_reading_order():
    """Get the recommended reading order for the codebase."""
    try:
       state.require_index()
       files = state.get_files()
       deps = state.get_deps()
       runtime = state._graph_runtime or deps["graph"]
       order = generate_reading_order(files, deps, graph_runtime=runtime)
       order_files = [item["file"] for item in order["order"]]
       risks, _ = compute_file_risks(order_files, runtime)
       risks_by_file = {r["file"]: r for r in risks}
       for item in order["order"]:
           risk = risks_by_file.get(item["file"], {})
           item["risk_level"] = risk.get("risk_level", "none")
           item["affected_files"] = risk.get("affected_files", 0)
           item["direct"] = risk.get("direct", [])
           item["transitive"] = risk.get("transitive", [])
           # Map backend fields to frontend expectations
           item["priority"] = item.get("relevance", "optional")
           item["reason"] = item.get("why", "")
           
       return order
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
# ─── GET /execution-flow ───────────────────────────────────────
@app.get("/execution-flow")
def get_execution_flow():
    """Get the execution flow diagram and narration."""
    try: 
        state.require_index()
        analyze_result = state.get_analyze_result()
        repo_path = analyze_result.get("repo_path") or state.get_repo_path()
        files = state.get_files()
        deps = state.get_deps()
        runtime = state._graph_runtime or deps["graph"]
        order = generate_reading_order(files, deps, graph_runtime=runtime)
        flow = generate_execution_flow(order, deps)
        return flow
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
# ─── POST /refresh ─────────────────────────────────────────────────
@app.post("/refresh")
def refresh_analysis():
    """Re-scan files and rebuild dependency graph + modules.

    Does NOT re-embed or re-index. Use this after code changes
    to update blast radius, reading order, and module structure.
    """
    try:
        state.require_index()
        state.rebuild_analysis_cache()

        return {
            "status": "refreshed",
            "files": len(state._files),
            "modules": list(state._modules_result["modules"].keys()),
        }
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ─── POST /incremental-reindex ───────────────────────────────────────
@app.post("/incremental-reindex")
def incremental_reindex_endpoint():
    """Re-embed only files that changed since last indexing.

    Also resumes a partial/interrupted index and performs a full build when no
    index exists, so it always leaves the repo in a queryable state.
    """
    try:
        from src.codewalk.codewalk_config import load_codewalk_yaml

        repo_path = _require_repo_path(None)
        state.set_repo_path(repo_path)
        codewalk_config = load_codewalk_yaml(repo_path)
        docs_path = _resolve_extras_paths(repo_path, codewalk_config)

        result = state.analyze_or_reindex_index(
            repo_path,
            docs_path=docs_path,
            mode="reindex",
        )

        return result
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ─── POST /review ────────────────────────────────────────────────────
@app.post("/review", response_model=ReviewResponse)
async def review_endpoint(request: ReviewRequest):
    """Review current git diff for bugs, security issues, and style.
    Runs the one-stop review engine (graph + deterministic + LLM + hard verdict).
    """
    try:
        import secrets

        from src.codewalk.review.engine import run_review
        from src.codewalk.config import get_llm

        state.ensure_initialized()
        repo_path = state.get_repo_path()
        if not repo_path:
            raise HTTPException(status_code=400, detail="Repository path not available")

        review_id = secrets.token_urlsafe(12)
        llm = get_llm(temperature=0)
        report = await asyncio.to_thread(
            run_review,
            repo_path=Path(repo_path),
            target_branch=request.target_branch,
            commit=request.commit,
            staged=request.staged,
            llm=llm,
            incremental=request.incremental,
            force_full_review=request.force_full_review,
            review_id=review_id,
            narrative_summary=request.narrative_summary,
        )

        from src.codewalk.review.renderers import render_api_response

        response_data = render_api_response(report)

        return ReviewResponse(
            verdict=response_data["verdict"],
            verdict_reason=response_data["verdict_reason"],
            issues=response_data["issues"],
            summary=response_data["summary"],
            files_reviewed=response_data["files_reviewed"],
            lines_added=response_data["lines_added"],
            lines_removed=response_data["lines_removed"],
            session_id=response_data["session_id"],
            architecture_flags=response_data["architecture_flags"],
        )
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ─── POST /review/cancel ─────────────────────────────────────────────
@app.post("/review/cancel", response_model=CancelReviewResponse)
async def cancel_review_endpoint(request: CancelReviewRequest):
    """Cancel a running review by its review_id."""
    try:
        from src.codewalk.review.cancellation import cancel_review

        cancelled = cancel_review(request.review_id)
        if cancelled:
            return CancelReviewResponse(
                cancelled=True,
                message=f"Review {request.review_id} cancellation requested.",
            )
        return CancelReviewResponse(
            cancelled=False,
            message=f"Review {request.review_id} not found or already completed.",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ─── POST /review/stream ─────────────────────────────────────────────
@app.post("/review/stream")
async def review_stream_endpoint(request: ReviewStreamRequest):
    """Run a review and stream phase progress as Server-Sent Events.

    The client receives one SSE event per pipeline phase. The final event has
    phase ``complete`` and includes the full review payload under ``data.report``.
    """
    import secrets

    from src.codewalk.config import get_llm
    from src.codewalk.review.engine import run_review
    from src.codewalk.review.progress import ReviewProgressReporter, review_progress_bus
    from src.codewalk.review.renderers import render_api_response

    state.ensure_initialized()
    repo_path = state.get_repo_path()
    if not repo_path:
        raise HTTPException(status_code=400, detail="Repository path not available")

    review_id = secrets.token_urlsafe(12)
    reporter = ReviewProgressReporter(review_id)
    review_progress_bus.register(review_id, reporter)

    async def _event_generator():
        try:
            while True:
                try:
                    event = await asyncio.to_thread(reporter.get, timeout=30.0)
                except queue.Empty:
                    break
                if event is None:
                    break
                payload = {
                    "phase": event.phase,
                    "message": event.message,
                    "data": event.data,
                }
                yield f"data: {json.dumps(payload)}\n\n".encode("utf-8")
        finally:
            review_progress_bus.unregister(review_id)

    async def _run_review():
        try:
            llm = get_llm(temperature=0)
            report = await asyncio.to_thread(
                run_review,
                repo_path=Path(repo_path),
                target_branch=request.target_branch,
                commit=request.commit,
                staged=request.staged,
                llm=llm,
                incremental=request.incremental,
                force_full_review=request.force_full_review,
                review_id=review_id,
                progress_reporter=reporter,
                narrative_summary=request.narrative_summary,
            )
            response_data = render_api_response(report)
            reporter.report(
                "complete",
                "Review complete",
                {"report": response_data},
            )
        except Exception as e:
            logger.exception("[review/stream] review failed")
            reporter.report("error", f"Review failed: {e}", {"error": str(e)})
        finally:
            reporter.done()

    # Start the review in the background; the generator streams events as they
    # are produced.
    asyncio.create_task(_run_review())
    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )

# ─── POST /review/verdict ────────────────────────────────────────────
@app.post("/review/verdict")
def review_verdict_endpoint(request: ReviewVerdictRequest):
    """Record a user verdict (accepted/rejected) on a review finding."""
    from src.codewalk.review.session_store import load_findings, save_findings, load_session
    from datetime import datetime, timezone

    if request.verdict not in ("accepted", "rejected"):
        raise HTTPException(status_code=422, detail="verdict must be 'accepted' or 'rejected'")

    repo_path = state.get_repo_path()
    if not repo_path:
        raise HTTPException(status_code=400, detail="Repository path not available")

    session = load_session(Path(repo_path), request.session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session {request.session_id} not found")

    folder = session.folder_name or session.session_id
    findings = load_findings(Path(repo_path), folder)
    if not findings or request.finding_index < 0 or request.finding_index >= len(findings):
        raise HTTPException(status_code=400, detail=f"Finding index {request.finding_index} out of range")

    findings[request.finding_index]["user_verdict"] = request.verdict
    findings[request.finding_index]["verdict_at"] = datetime.now(timezone.utc).isoformat()
    if request.reason:
        findings[request.finding_index]["verdict_reason"] = request.reason

    save_findings(Path(repo_path), folder, findings)

    title = findings[request.finding_index].get("title", "Untitled")
    return {"success": True, "message": f"Finding #{request.finding_index} ({title}) marked as {request.verdict}"}


# ─── POST /review/apply-and-verify ───────────────────────────────────
@app.post("/review/apply-and-verify")
def apply_and_verify_endpoint(request: ApplyAndVerifyRequest):
    """Batch-set verdicts, apply accepted fixes, and run verification in one call.

    Accepts a dict of {finding_index: verdict} to set verdicts, then applies
    all accepted fixes, runs static analysis + tests, and persists verification
    status back to the session JSON.
    """
    import os
    import json as _json
    from datetime import datetime, timezone
    from src.codewalk.review.fix_applier import apply_fix_to_file
    from src.codewalk.review.session_store import load_session, _session_dir
    from src.codewalk.review.finding_store import find_last_review
    from src.codewalk.review.utils import get_current_branch
    from src.codewalk.tools.static_analysis import run_static_analysis
    from src.codewalk.tools.test_runner import run_tests
    from src.codewalk.review.renderers.markdown import render_findings_markdown

    repo_path = state.get_repo_path()
    if not repo_path:
        raise HTTPException(status_code=400, detail="Repository path not available")

    # Resolve session
    if request.session_id:
        session = load_session(Path(repo_path), request.session_id)
        if session is None:
            raise HTTPException(status_code=404, detail=f"Session {request.session_id} not found")
        folder = session.folder_name or session.session_id
    else:
        branch = get_current_branch(Path(repo_path))
        last_store = find_last_review(Path(repo_path), branch)
        if not last_store:
            raise HTTPException(status_code=404, detail="No previous review session found on this branch")
        session = load_session(Path(repo_path), last_store.review_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Could not load latest review session")
        folder = session.folder_name or session.session_id

    session_dir = _session_dir(Path(repo_path), folder)
    llm_path = session_dir / "llm_findings.json"
    if not llm_path.exists():
        raise HTTPException(status_code=400, detail="No llm_findings.json found for this session")

    findings = _json.loads(llm_path.read_text(encoding="utf-8"))
    if not findings:
        raise HTTPException(status_code=400, detail="No findings in this session")

    # Step 1: Write verdicts from the request
    now = datetime.now(timezone.utc).isoformat()
    for idx_str, verdict in request.verdicts.items():
        try:
            idx = int(idx_str)
        except ValueError:
            continue
        if 0 <= idx < len(findings) and verdict in ("accepted", "rejected"):
            findings[idx]["user_verdict"] = verdict
            findings[idx]["verdict_at"] = now

    # Step 2: Filter accepted findings with code
    to_apply = [
        (i, f) for i, f in enumerate(findings)
        if f.get("user_verdict") == "accepted"
        and f.get("recommended_code")
        and f.get("current_code")
        and f.get("file_path")
    ]

    if not to_apply:
        # Still persist the verdicts even if nothing to apply
        llm_path.write_text(_json.dumps(findings, indent=2), encoding="utf-8")
        (session_dir / "llm_findings.md").write_text(
            render_findings_markdown(findings, title="LLM Findings", source_label="review LLM"),
            encoding="utf-8",
        )
        return ApplyAndVerifyResponse(applied=[], failed=[], total_accepted=0)

    # Step 3: Apply each fix
    applied_labels: list[str] = []
    failed_labels: list[str] = []
    applied_indices: list[int] = []
    modified_files: list[str] = []

    for idx, finding in to_apply:
        file_path = finding["file_path"]
        old_code = finding["current_code"]
        new_code = finding["recommended_code"]

        full_path = os.path.join(repo_path, file_path)
        resolved_repo = os.path.realpath(repo_path)
        resolved_target = os.path.realpath(full_path)
        if not resolved_target.startswith(resolved_repo + os.sep) and resolved_target != resolved_repo:
            failed_labels.append(f"#{idx} {file_path}: path traversal blocked")
            continue

        result = apply_fix_to_file(repo_path, file_path, old_code, new_code)
        if result["ok"]:
            applied_indices.append(idx)
            applied_labels.append(f"#{idx} {file_path}: {finding.get('title', 'applied')}")
            if file_path not in modified_files:
                modified_files.append(file_path)
        else:
            failed_labels.append(f"#{idx} {file_path}: {result.get('error', 'unknown')}")

    # Step 4: Run verification
    sa_issues = []
    test_result = None
    if modified_files:
        sa_issues = run_static_analysis(repo_path, modified_files)
        test_result = run_tests(repo_path, modified_files)

    has_sa_errors = any(
        getattr(i, "severity", "").lower() in ("critical", "high", "warning")
        for i in sa_issues
    )
    tests_passed = test_result is None or test_result.ok
    verification_passed = not has_sa_errors and tests_passed

    # Step 5: Persist status back to findings
    sa_summary = f"{len(sa_issues)} issue(s)" if sa_issues else "clean"
    test_summary = "pass" if tests_passed else "fail"

    for idx in applied_indices:
        findings[idx]["status"] = "fixed" if verification_passed else "still_present"
        findings[idx]["verifier_notes"] = f"SA: {sa_summary}, Tests: {test_summary}"

    llm_path.write_text(_json.dumps(findings, indent=2), encoding="utf-8")
    (session_dir / "llm_findings.md").write_text(
        render_findings_markdown(findings, title="LLM Findings", source_label="review LLM"),
        encoding="utf-8",
    )

    return ApplyAndVerifyResponse(
        applied=applied_labels,
        failed=failed_labels,
        total_accepted=len(to_apply),
        static_analysis_issues=len(sa_issues),
        tests_passed=tests_passed,
        verification_passed=verification_passed,
    )


# ─── POST /review/guidelines ─────────────────────────────────────────
@app.post("/review/guidelines")
def load_guidelines_endpoint(request: GuidelinesRequest):
    """Load project docs/standards for use in reviews."""
    try:
        import os
        from src.codewalk.doc_knowledge.doc_store import DocStore

        repo_path = _ensure_repo_path(request.repo_path)

        path = request.docs_path
        if not path:
            raise HTTPException(
                status_code=400,
                detail="No path provided. Pass docs_path.",
            )
        if not os.path.isdir(path):
            raise HTTPException(status_code=404, detail=f"Directory not found: {path}")

        col = f"{state.get_collection_name()}_docs"
        store = DocStore(persist_dir=state.chroma_path(), collection_name=col)
        store.create_collection()
        store.clear()
        count = store.index_docs(path)
        return {"status": "loaded", "chunks": count, "path": path}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    

# ─── POST /voice/ask ─────────────────────────────────────────
@app.post("/voice/ask")
async def voice_ask_endpoint(
    audio: UploadFile = File(...),
    thread_id: str = Form("voice"),
):
    """Voice-in, voice-out codebase Q&A.

    Accepts audio file (webm/mp3/wav from browser mic).
    Sends transcript directly to the chat agent (which has all tools),
    then summarizes the answer for TTS.

    Returns JSON:
      question: transcribed text
      answer: agent's full text answer
      speech: summarized text for TTS
      audio_base64: MP3 audio as base64
    """
    # Lazy imports — voice deps are optional in production images
    from src.codewalk.voice.stt import transcribe_bytes
    from src.codewalk.voice.tts import synthesize

    # 1. STT
    MAX_VOICE_SIZE = 50 * 1024 * 1024  # 50MB
    audio_bytes = await audio.read()
    if len(audio_bytes) > MAX_VOICE_SIZE:
        raise HTTPException(413, "Audio file too large (max 50MB)")
    question = transcribe_bytes(audio_bytes, file_name=audio.filename or "audio.webm")

    if not question.strip():
        fallback = "I didn't catch that. Could you try again?"
        return JSONResponse({
            "question": "",
            "answer": fallback,
            "speech": fallback,
            "audio_base64": base64.b64encode(synthesize(fallback)).decode(),
        })

    # 2. Auto-load index if server restarted
    try:
        state.require_index()
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # 3. Send transcript to the agent — it picks the right tool natively
    try:
        agent = state.get_agent()
        config = {"configurable": {"thread_id": thread_id}}
        result = await asyncio.to_thread(
            agent.invoke,
            {"messages": [("human", question)]},
            config=config,
        )
        answer = result["messages"][-1].content
    except HTTPException:
        raise
    except Exception as e:
        answer = f"Sorry, I couldn't process that: {e}"

    # 4. Summarize for TTS
    from src.codewalk.voice.companion import format_voice_response
    voice = format_voice_response(answer)

    # 5. TTS
    audio_response = synthesize(voice["speech"])

    return JSONResponse({
        "question": question,
        "answer": answer,
        "speech": voice["speech"],
        "audio_base64": base64.b64encode(audio_response).decode(),
    })

@app.get("/cycles")
def get_cycles():
    """Detect circular dependencies."""
    state.require_index()
    runtime = state.get_graph_runtime()
    return runtime.detect_cycles()


@app.get("/architecture")
def get_architecture():
    """Architecture health report: stats, centrality, cycles."""
    state.require_index()
    runtime = state.get_graph_runtime()
    return {
        "stats": runtime.get_graph_stats(),
        "centrality": runtime.centrality(top_n=10),
        "cycles": runtime.detect_cycles(),
    }

# ─── POST /docs/index ────────────────────────────────────────────────
@app.post("/docs/index")
def docs_index(req: DocsIndexRequest):
    """Index team docs/guidelines from docs_path into the doc store."""
    _ensure_repo_path(req.repo_path)
    store = state.get_doc_store()
    result = store.index_docs(req.docs_path)
    # Map the store's internal keys to the API contract expected by the frontend.
    return {
        "status": "indexed",
        "files_indexed": result.get("docs_found", 0),
        "chunks_created": result.get("chunks_stored", 0),
    }

# ─── POST /docs/search ──────────────────────────────────────────────
@app.post("/docs/search")
def docs_search(req: DocsSearchRequest):
    """Semantic search over indexed team docs/guidelines."""
    _ensure_repo_path(req.repo_path)
    store = state.get_doc_store()

    if store.chunk_count() == 0:
        raise HTTPException(status_code=400, detail="No documents indexed yet.")

    results = store.search(req.query, n_results=req.n_results)

    return {
        "query": req.query,
        "results": [
            {
                "text": r["text"],
                "metadata": r["metadata"],
                "distance": r["distance"],
            }
            for r in results
        ],
    }


# ─── POST /docs/ask ─────────────────────────────────────────────────
@app.post("/docs/ask")
async def docs_ask(req: DocsAskRequest):
    from src.codewalk.config import get_llm
    from src.codewalk.doc_knowledge.prompts import DOC_ASK_PROMPT
    from src.codewalk.rag.query_expander import expand_query

    _ensure_repo_path(req.repo_path)
    store = state.get_doc_store()

    if store.chunk_count() == 0:
        raise HTTPException(status_code=400, detail="No documents indexed yet.")

    # Expand the question into 1-3 search angles for better recall.
    try:
        expanded = expand_query(req.question)
        queries = expanded.queries[:3]
    except Exception:
        queries = [req.question]
    if req.question not in queries:
        queries.insert(0, req.question)
    queries = queries[:3]

    results = store.multi_search(queries, n_results=req.n_results)

    if not results:
        return {"answer": "No relevant documents found.", "sources": []}

    # Build context
    context_parts = []
    for result in results:
        metadata = result["metadata"]
        source = f"{metadata.get('doc_path', '?')} > {metadata.get('section', '?')}"
        context_parts.append(f"--- {source} ---\n{result['text']}")

    context = "\n\n".join(context_parts)

    prompt = DOC_ASK_PROMPT.format(context=context, question=req.question)

    llm = get_llm(temperature=0)

    response = await asyncio.to_thread(llm.invoke, prompt)

    sources = [
        {
            "doc_path": r["metadata"].get("doc_path", "?"),
            "section": r["metadata"].get("section", "?"),
        }
        for r in results
    ]

    return {
        "answer": response.content,
        "sources": sources,
    }


# ─── POST /chat/approve ─────────────────────────────────────────────────
@app.post("/chat/approve")
def chat_approve(request: ApproveRequest):
    """Resume or reject a graph that is waiting for human approval.

    Call this after /chat returns a response where the agent stopped at an
    interrupt node (detected by the frontend when the agent response ends
    mid-task with a proposed action payload).

    Args:
        thread_id: The thread_id from the original /chat call.
        action:    "approve" → graph resumes from checkpoint.
                   "reject"  → checkpoint is discarded, action is not taken.
    """
    try:
        state.require_index()

        if request.action not in ("approve", "reject"):
            raise HTTPException(status_code=422, detail=f"action must be 'approve' or 'reject', got '{request.action}'")

        graph = state.get_agent()

        config = {
            "configurable": {
                "thread_id": request.thread_id
            }
        }

        if request.action == "reject":
            # Replace the LAST tool-calling message with a rejection so the graph can reach END.
            # Scan backwards to find the most recent message with tool_calls,
            # avoiding race conditions where newer messages may have been added.
            from langchain_core.messages import AIMessage
            current = graph.get_state(config)
            messages = list(current.values["messages"])
            tool_call_index = None
            for i in range(len(messages) - 1, -1, -1):
                if hasattr(messages[i], "tool_calls") and messages[i].tool_calls:
                    tool_call_index = i
                    break
            if tool_call_index is not None:
                messages[tool_call_index] = AIMessage(
                    content="I understand. I won't take that action without your approval."
                )
                graph.update_state(config, {"messages": messages})
            result = graph.invoke(None, config=config)
            answer = result["messages"][-1].content
            return {
                "status": "rejected",
                "message": "Action discarded. No changes made.",
                "result": answer,
            }
        
        result = graph.invoke(None, config=config)

        graph_state = graph.get_state(config)
        if graph_state.next:
            return {
                "status": "interrupted",
                "next_node": graph_state.next[0],
                "state": {key: str(value) for key, value in graph_state.values.items()},
            }
        
        return {"status": "completed", "result": str(result)}
    
    except HTTPException:
        raise
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/research", response_model=ResearchResponse)
async def research_endpoint(request: ResearchRequest):
    """Run deep research on a complex codebase question."""
    try:
        from src.codewalk.research.deep_research import deep_research
        state.require_index()
        store = state.get_store()
        report = await asyncio.to_thread(
            deep_research,
            request.question,
            store,
            state._graph_store,
            request.depth,
        )
        return {
            "question": report.question,
            "report": report.markdown,
            "sources": report.sources,
        }
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



# ─── Cloud routes — noop if cloud env vars not set ───────────────────
from src.codewalk.api.cloud import setup_cloud
setup_cloud(app)

# ─── POST /review/apply ──────────────────────────────────────────────
@app.post("/review/apply", response_model=ApplyFixesResponse)
def apply_fixes_endpoint(request: ApplyFixesRequest):
    """Apply approved code fixes to files on disk.

    Each fix is validated before application:
      - File must exist inside the repo
      - old_code must be found uniquely (exact, normalized, or context-line)
      - Write is atomic (temp file + rename)
      - Python files are syntax-checked after applying
      - Optional formatter is run if configured

    This endpoint REQUIRES the user to have already reviewed the fixes.
    It does NOT ask for approval — the caller (frontend/CLI) is responsible
    for showing fixes and getting user consent before calling this endpoint.

    Returns 200 with all applied fixes and any failures. Partial failures are
    reported in the ``failed`` array so the frontend can show per-fix errors.
    """
    try:
        from src.codewalk.review.fix_applier import apply_fixes_batch

        repo_path = state.get_repo_path()
        fixes = [fix.model_dump() for fix in request.fixes]

        result = apply_fixes_batch(
            repo_path,
            fixes,
            continue_on_error=request.continue_on_error,
            validate_only=request.validate_only,
            run_formatter=request.run_formatter,
        )

        return ApplyFixesResponse(
            applied=[
                AppliedFix(
                    file_path=a["file_path"],
                    old_code=a["old_code"],
                    new_code=a["new_code"],
                    message=a["message"],
                )
                for a in result["applied"]
            ],
            failed=result["failed"],
            total=result["total"],
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── POST /tools/static-analysis ─────────────────────────────────────
@app.post("/tools/static-analysis", response_model=StaticAnalysisResponse)
def run_static_analysis_endpoint(request: StaticAnalysisRequest):
    """Run language-aware static analyzers on the given files."""
    from src.codewalk.tools.static_analysis import run_static_analysis

    repo_path = state.get_repo_path()
    issues = run_static_analysis(repo_path, request.file_paths, request.language_hint)
    return StaticAnalysisResponse(
        issues=[
            StaticAnalysisIssue(
                file_path=i.file_path,
                line=i.line,
                column=i.column,
                severity=i.severity,
                rule=i.rule,
                message=i.message,
                category=i.category,
                tool=i.tool,
            )
            for i in issues
        ],
        total=len(issues),
    )


# ─── POST /tools/run-tests ───────────────────────────────────────────
@app.post("/tools/run-tests", response_model=TestRunResponse)
def run_tests_endpoint(request: TestRunRequest):
    """Run the project's test suite with language-aware auto-detection."""
    from src.codewalk.tools.test_runner import run_tests

    repo_path = state.get_repo_path()
    result = run_tests(
        repo_path,
        file_paths=request.file_paths or [],
        language_hint=request.language_hint,
        command=request.command,
    )
    return TestRunResponse(
        command=" ".join(result.command) if result.command else "",
        ok=result.ok,
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
        error=result.error,
    )


# ─── Version & staleness (shared with MCP) ───────────────────────────

@app.get("/version")
def get_version():
    """Codewalk software version — same schema as cloud GET /version."""
    from src.codewalk.staleness import version_info

    info = version_info()
    info["runtime"] = "api"
    return info


@app.get("/staleness")
def get_staleness():
    """Index + software freshness for API/frontend clients."""
    from src.codewalk.staleness import staleness_status

    return staleness_status()


@app.get("/index-status")
def index_status(repo_path: str | None = Query(None, description="Optional repo path to check. Falls back to the current Codewalk state / cwd discovery.")):
    """Return whether a Codewalk index exists for the discovered repo.

    Used by the frontend to lock/unlock navigation tabs until the repo has been
    analyzed at least once.
    """
    try:
        resolved = _resolve_repo_path(repo_path)
    except HTTPException:
        return {"indexed": False, "repo_path": repo_path}

    manifest_path = os.path.join(resolved, ".codewalk", "manifest.json")
    return {
        "indexed": os.path.isfile(manifest_path),
        "repo_path": resolved,
    }


# ─── Semantic search over codebase embeddings ─────────────────────────

@app.post("/semantic-search", response_model=SemanticSearchResponse)
def semantic_search(request: SemanticSearchRequest):
    """Search the vector index for chunks semantically similar to the query."""
    repo_path = _ensure_repo_path(request.repo_path)

    persist_dir = os.path.join(repo_path, ".codewalk", "chroma")
    if not os.path.isdir(persist_dir):
        raise HTTPException(
            status_code=404,
            detail=f"No index found for {repo_path}. Run POST /analyze first.",
        )

    try:
        store = state.get_store()
        raw_results = store.search(request.query, n_results=request.n_results)
        results = [
            SemanticSearchResult(
                id=r.get("id", ""),
                text=r.get("text", ""),
                metadata=r.get("metadata", {}),
                distance=r.get("distance"),
            )
            for r in raw_results
        ]
        return SemanticSearchResponse(results=results)
    except HTTPException:
        raise
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        _log(f"[semantic-search] error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ─── RAG utility endpoints ────────────────────────────────────────────

@app.post("/rag/expand-query", response_model=ExpandQueryResponse)
def expand_query_endpoint(request: ExpandQueryRequest):
    """Expand a natural-language query into multiple retrieval queries + symbol hint.

    Uses an LLM. API-only; MCP must not call this.
    """
    from src.codewalk.rag.query_expander import expand_query
    try:
        result = expand_query(request.query)
        return ExpandQueryResponse(
            original=result.original,
            queries=result.queries,
            symbol_hint=result.symbol_hint,
        )
    except Exception as e:
        _log(f"[rag/expand-query] error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/rag/rerank", response_model=RerankResponse)
def rerank_endpoint(request: RerankRequest):
    """Rerank a list of retrieved chunks by relevance to the query.

    Uses an LLM. API-only; MCP must not call this.
    """
    from src.codewalk.rag.reranker import rerank_chunks
    try:
        raw_results = [
            {
                "id": r.id,
                "text": r.text,
                "metadata": r.metadata,
                "distance": r.distance,
            }
            for r in request.results
        ]
        reranked = rerank_chunks(request.query, raw_results, top_k=request.top_k)
        results = [
            SemanticSearchResult(
                id=r.get("id", ""),
                text=r.get("text", ""),
                metadata=r.get("metadata", {}),
                distance=r.get("distance"),
            )
            for r in reranked
        ]
        return RerankResponse(results=results)
    except Exception as e:
        _log(f"[rag/rerank] error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/rag/symbol-lookup", response_model=SymbolLookupResponse)
def symbol_lookup_endpoint(request: SymbolLookupRequest):
    """Deterministic symbol lookup via the knowledge graph and vector store."""
    from src.codewalk.services.symbol_service import lookup
    repo_path = _ensure_repo_path()
    persist_dir = os.path.join(repo_path, ".codewalk", "chroma")
    if not os.path.isdir(persist_dir):
        raise HTTPException(
            status_code=404,
            detail=f"No index found for {repo_path}. Run POST /analyze first.",
        )
    try:
        store = state.get_store()
        graph_store = state.get_graph_store()
        raw_results = lookup(
            request.query,
            store,
            graph_store,
            include_callers=request.include_callers,
            include_callees=request.include_callees,
        )
        results = [
            SemanticSearchResult(
                id=r.get("id", ""),
                text=r.get("text", ""),
                metadata=r.get("metadata", {}),
                distance=r.get("distance"),
            )
            for r in raw_results
        ]
        return SymbolLookupResponse(results=results)
    except HTTPException:
        raise
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        _log(f"[rag/symbol-lookup] error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ─── Health check ───────────────────────────────────────────────────

@app.get("/health")
def health():
    """Health check endpoint."""
    return {"status": "ok", "codewalk_version": __version__}
