import logging
import os
import sys
import json
import base64
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi import UploadFile, File, Form
from fastapi.responses import JSONResponse

from src.codewalk.pipeline import full_index_parallel, reindex, chunk_and_embed_parallel, incremental_reindex, write_manifest
from src.codewalk.api.models import (
    AnalyzeRequest, AnalyzeResponse,
    ChatRequest, ChatResponse,
    ModuleResponse, OverviewResponse,
    BlastRadiusResponse,
    ReviewRequest, ReviewFileRequest, ReviewResponse, ReviewFileResponse,
    GuidelinesRequest,
    DocsIndexRequest, DocsAskRequest, DocsSearchRequest, ApproveRequest,
    ResearchRequest, ApplyFixesRequest, ApplyFixesResponse, AppliedFix,
    SemanticSearchRequest, SemanticSearchResponse, SemanticSearchResult,
)
from src.codewalk.api import state
from langchain_core.messages import AIMessage, ToolMessage
from src.codewalk.agent.graph import proposed_write_action
from src.codewalk.ingestion.scanner import scan_directory
from src.codewalk.ingestion.tech_detect import detect_tech_stack
from src.codewalk.generation.diagram_generator import generate_module_diagram
from src.codewalk.generation.overview_generator import generate_overview
from src.codewalk.embeddings.vector_store import VectorStore
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


def _require_repo_path(repo_path: str | None) -> str:
    """Validate that the frontend provided an explicit repo path."""
    path = (repo_path or "").strip()
    if not path:
        raise HTTPException(status_code=400, detail="repo_path is required")
    return path


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
_rate_limit_store: dict[str, list[float]] = defaultdict(list)

@app.middleware("http")
async def rate_limit_middleware(request, call_next):
    """Simple sliding-window rate limiter per client IP."""
    client_ip = request.client.host if request.client else "unknown"
    now = time.time()
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
    """Convert all unhandled exceptions to user-friendly messages."""
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
        auto    — skip indexing if collection already has data (default)
        reindex — smart re-index (only changed/new/deleted files)
        full    — nuke everything and re-embed from scratch
    """
    try:
        from src.codewalk.team_config import load_codewalk_yaml

        request.repo_path = _require_repo_path(request.repo_path)
        state.set_repo_path(request.repo_path)
        if not request.collection_name:
            request.collection_name = state.get_collection_name()
        persist_dir = f"{request.repo_path.rstrip('/')}/.codewalk/chroma"
        guidelines_path = os.getenv("REVIEW_GUIDELINES_PATH", "")
        docs_path = os.getenv("CODE_DOCS_PATH", "")
        team_config = load_codewalk_yaml(request.repo_path)

        # ── Index on disk + auto mode → load only (no re-embed) ──
        if request.index_mode == "auto" and state.index_on_disk(request.repo_path):
            state.load_scoped_analysis()
            if guidelines_path or docs_path:
                state.rebuild_analysis_cache(
                    files=state._files,
                    guidelines_path=guidelines_path,
                    docs_path=docs_path,
                )
            _log(f"[api] Loaded existing index — {state._store.chunk_count()} chunks")
            return AnalyzeResponse(
                status="complete",
                repo_path=request.repo_path,
                files_scanned=len(state._files or []),
                chunks_created=state._store.chunk_count(),
                modules=list(state._modules_result["modules"].keys()),
            )

        store = VectorStore(persist_dir=persist_dir)
        store.create_collection(request.collection_name)
        existing_count = store.chunk_count()

        # ── Decide whether to index ──────────────────────────────
        files = None
        if request.index_mode == "full" or existing_count == 0:
            index_result = full_index_parallel(
                request.repo_path, request.collection_name, persist_dir=persist_dir,
                team_config=team_config,
            )
            files = index_result.get("files")
        elif request.index_mode == "reindex":
            index_result = reindex(
                request.repo_path, request.collection_name, persist_dir=persist_dir,
                team_config=team_config,
            )
        else:
            index_result = {
                "repo_path": request.repo_path,
                "files_scanned": 0,
                "chunks_created": 0,
                "skipped": True,
            }
            _log(f"[api] Skipping indexing — collection already has {existing_count} chunks")

        if files is None:
            files = state.scan_repo_files(request.repo_path)

        state.initialize(store, None, None, index_result,
                         files=files, deps=None, repo_path=request.repo_path,
                         embedded_chunks=index_result.get("embedded_chunks"),
                         guidelines_path=guidelines_path, docs_path=docs_path)

        return AnalyzeResponse(
            status="complete",
            repo_path=request.repo_path,
            files_scanned=index_result["files_scanned"],
            chunks_created=index_result.get("chunks_embedded", index_result.get("chunks_created", 0)),
            modules=list(state._modules_result["modules"].keys()),
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
        """Async generator that yields SSE events at each pipeline step.
        Blocking operations run in thread pool so events stream in real time."""
        try:
            from src.codewalk.team_config import load_codewalk_yaml

            request.repo_path = _require_repo_path(request.repo_path)
            state.set_repo_path(request.repo_path)
            if not request.collection_name:
                request.collection_name = state.get_collection_name()
            team_config = load_codewalk_yaml(request.repo_path)
            guidelines_path = os.getenv("REVIEW_GUIDELINES_PATH", "")
            docs_path = os.getenv("CODE_DOCS_PATH", "")

            # Step 1: Check existing data
            yield f"data: {json.dumps({'step': 'init', 'message': 'Checking existing index...'})}\n\n"
            if request.index_mode == "auto" and state.index_on_disk(request.repo_path):
                await asyncio.to_thread(state.load_scoped_analysis)
                count = state._store.chunk_count()
                yield f"data: {json.dumps({'step': 'skip', 'message': f'Loaded existing index ({count} chunks)'})}\n\n"
                yield f"data: {json.dumps({'step': 'done', 'message': 'Analysis complete!', 'result': {'status': 'complete', 'repo_path': request.repo_path, 'files_scanned': len(state._files or []), 'chunks_created': count, 'modules': list(state._modules_result['modules'].keys())}})}\n\n"
                return

            persist_dir = f"{request.repo_path.rstrip('/')}/.codewalk/chroma"
            store = VectorStore(persist_dir=persist_dir)
            store.create_collection(request.collection_name)
            existing_count = store.chunk_count()
            files = None  # set during full-index; avoids redundant scan at Step 3

            # Step 2: Indexing
            if request.index_mode == "full" or existing_count == 0:
                # ── Full index with progress ──
                yield f"data: {json.dumps({'step': 'scan', 'message': 'Scanning directory...'})}\n\n"
                files = await asyncio.to_thread(state.scan_repo_files, request.repo_path)
                scanned_count = len(files)
                yield f"data: {json.dumps({'step': 'scan', 'message': f'Scanned {scanned_count} files (codewalk.yaml excludes applied)'})}\n\n"

                yield f"data: {json.dumps({'step': 'chunk', 'message': 'Chunking + embedding in parallel...'})}\n\n"

                embedded, chunks_created = await asyncio.to_thread(chunk_and_embed_parallel, files)

                yield f"data: {json.dumps({'step': 'chunk', 'message': f'Created {chunks_created} chunks'})}\n\n"
                yield f"data: {json.dumps({'step': 'embed', 'message': f'Embedded {len(embedded)} chunks'})}\n\n"

                yield f"data: {json.dumps({'step': 'store', 'message': 'Storing in vector database...'})}\n\n"
                await asyncio.to_thread(store.clear_collection)
                await asyncio.to_thread(store.add_parent_child_chunks, embedded)
                await asyncio.to_thread(
                    write_manifest,
                    f"{request.repo_path.rstrip('/')}/.codewalk",
                    file_count=len(files),
                    chunk_count=len(embedded),
                    collection_name=request.collection_name,
                )
                yield f"data: {json.dumps({'step': 'store', 'message': f'Stored {len(embedded)} chunks in ChromaDB'})}\n\n"

                index_result = {
                    "repo_path": request.repo_path,
                    "files_scanned": len(files),
                    "chunks_created": chunks_created,
                    "embedded_chunks": embedded,
                    "files": files,
                }

            elif request.index_mode == "reindex":
                yield f"data: {json.dumps({'step': 'scan', 'message': 'Scanning for changes...'})}\n\n"
                index_result = await asyncio.to_thread(
                    reindex, request.repo_path, request.collection_name,
                    persist_dir=persist_dir, team_config=team_config,
                )
                new = index_result['new_files']
                changed = index_result['changed_files']
                deleted = index_result['deleted_files']
                msg = f'New: {new}, Changed: {changed}, Deleted: {deleted}'
                yield f"data: {json.dumps({'step': 'reindex', 'message': msg})}\n\n"

            else:
                yield f"data: {json.dumps({'step': 'skip', 'message': f'Index exists ({existing_count} chunks) — skipping'})}\n\n"
                index_result = {
                    "repo_path": request.repo_path,
                    "files_scanned": 0,
                    "chunks_created": 0,
                    "skipped": True,
                }

            # Step 3: Analysis + DuckDB (via build_full_analysis inside initialize)
            yield f"data: {json.dumps({'step': 'analyze', 'message': 'Building dependency graph...'})}\n\n"
            if files is None:
                files = await asyncio.to_thread(state.scan_repo_files, request.repo_path)

            # Step 4: Save state — initialize does deps → modules → DuckDB → docs → guidelines → agent
            yield f"data: {json.dumps({'step': 'agent', 'message': 'Creating AI agent...'})}\n\n"
            await asyncio.to_thread(
                state.initialize, store, None, None, index_result,
                files=files, deps=None, repo_path=request.repo_path,
                embedded_chunks=index_result.get("embedded_chunks"),
                guidelines_path=guidelines_path, docs_path=docs_path
            )

            num_modules = len(state._modules_result['modules'])
            yield f"data: {json.dumps({'step': 'analyze', 'message': f'Detected {num_modules} modules'})}\n\n"

            # Final event — includes full result
            chunks = index_result.get('chunks_embedded', index_result.get('chunks_created', 0))
            yield f"data: {json.dumps({'step': 'done', 'message': 'Analysis complete!', 'result': {'status': 'complete', 'repo_path': request.repo_path, 'files_scanned': index_result.get('files_scanned', 0), 'chunks_created': chunks, 'modules': list(state._modules_result['modules'].keys())}})}\n\n"

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
    """Get the project overview (tech stack, modules, diagram, LLM summary)."""
    try:
        state.require_index()
        modules_result = state.get_modules_result()
        store = state.get_store()

        # Generate diagram
        diagram = generate_module_diagram(modules_result["module_graph"])

        # Detect tech stack
        analyze_result = state.get_analyze_result()
        tech = detect_tech_stack(analyze_result.get("repo_path") or state.get_repo_path())

        # Generate overview (calls LLM)
        overview_text = generate_overview(tech, modules_result, diagram)

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
            diagram=diagram,
            overview_text=overview_text,
            riskiest_files=top_risky, 
        )
    
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
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
        return {"flow": flow}
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
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
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ─── POST /incremental-reindex ───────────────────────────────────────
@app.post("/incremental-reindex")
def incremental_reindex_endpoint():
    """Re-embed only files that changed since last indexing."""
    try:
        from src.codewalk.team_config import load_codewalk_yaml

        state.require_index()
        store = state.get_store()
        repo_path = state.get_repo_path()
        collection_name = state.get_collection_name()
        persist_dir = state.chroma_path()
        team_config = load_codewalk_yaml(repo_path)
        indexed_files = list(store.get_all_indexed_files())
        if not indexed_files:
            raise HTTPException(status_code=400, detail="No files indexed yet. Run /analyze first.")

        result = incremental_reindex(
            indexed_files, repo_path, collection_name,
            persist_dir=persist_dir, team_config=team_config,
        )

        # Refresh analysis cache + re-index docs/guidelines
        guidelines_path = os.getenv("REVIEW_GUIDELINES_PATH", "")
        docs_path = os.getenv("CODE_DOCS_PATH", "")
        state.rebuild_analysis_cache(
            embedded_chunks=result.get("embedded_chunks"),
            guidelines_path=guidelines_path,
            docs_path=docs_path,
            force_reindex_extras=True,
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
    Uses parallel context loading (guidelines + architecture + per-file) via asyncio.gather.
    """
    try:
        from src.codewalk.review.reviewer import review_diff_async

        state.ensure_initialized()

        store = None
        deps = None
        try:
            store = state.get_store()
            deps = state.get_deps()
        except RuntimeError:
            pass  # works without indexing, just less context

        result = await review_diff_async(
            staged=request.staged,
            target_branch=request.target_branch,
            commit=request.commit,
            store=store,
            deps=deps,
            graph_store=state.get_graph_store(),
            repo_path=state.get_repo_path(),
        )

        if request.reflect and result.diff_text:
            from src.codewalk.review.reflector import reflect_on_review
            result = await asyncio.to_thread(
                reflect_on_review, result, result.diff_text, request.iterations
            )

        issues = [
            {
                "severity": issue.severity.value,
                "confidence": issue.confidence.value,
                "category": issue.category.value,
                "file_path": issue.file_path,
                "line_number": issue.line_number,
                "title": issue.title,
                "explanation": issue.explanation,
                "suggestion": issue.suggestion,
                "fix_description": issue.fix_description,
                "code_snippet": issue.code_snippet,
            }
            for issue in result.issues
        ]

        return ReviewResponse(
            verdict=result.verdict.value,
            verdict_reason=result.verdict_reason,
            issues=issues,
            summary=result.summary,
            files_reviewed=result.files_reviewed,
            lines_added=result.lines_added,
            lines_removed=result.lines_removed,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ─── POST /review/file ───────────────────────────────────────────────
@app.post("/review/file", response_model=ReviewFileResponse)
async def review_file_endpoint(request: ReviewFileRequest):
    """Review a single file against codebase conventions."""
    try:
        from src.codewalk.rag.chain import format_context, retrieve_corrective
        from src.codewalk.config import get_llm
        from src.codewalk.review.reviewer import _get_caller_context
        from src.codewalk.review.models import DiffFile, DiffHunk, ChangedLine
        from src.codewalk.review.guidelines_loader import get_guidelines_store, search_guidelines
        import os

        state.require_index()
        store = state.get_store()
        repo_path = state.get_repo_path()

        full_path = (
            os.path.join(repo_path, request.file_path)
            if not os.path.isabs(request.file_path)
            else request.file_path
        )

        with open(full_path, "r", errors="replace") as f:
            content = f.read()

        lines = content.splitlines()

        # Synthetic DiffFile so we can reuse context helpers
        changed_lines = [
            ChangedLine(line_number=i + 1, content=line, change_type="added")
            for i, line in enumerate(lines)
        ]
        synthetic_diff = DiffFile(
            file_path=request.file_path,
            language="",
            hunks=[DiffHunk(start_line=1, end_line=len(lines), lines=changed_lines)],
            is_new_file=True,
            added_lines=len(lines),
            removed_lines=0,
        )

        # Corrective RAG for similar patterns (same pipeline as MCP)
        result = retrieve_corrective(
            f"code in {request.file_path}", store,
            graph_store=state.get_graph_store(),
        )
        patterns = format_context(result["chunks"]) if result["chunks"] else "No indexed context."

        # Caller context (who imports this file)
        caller_ctx = _get_caller_context(synthetic_diff, state._deps, state._graph_store)

        # Guidelines
        guidelines_ctx = ""
        guidelines_store = get_guidelines_store()
        if guidelines_store:
            guidelines_ctx = search_guidelines(guidelines_store, [synthetic_diff], n_results=3)

        context_parts = []
        if caller_ctx:
            context_parts.append(caller_ctx)
        if guidelines_ctx:
            context_parts.append(guidelines_ctx)
        context_parts.append(f"## Similar patterns elsewhere:\n{patterns}")
        context_sections = "\n\n".join(context_parts)

        llm = get_llm(temperature=0)
        response = await asyncio.to_thread(
            llm.invoke,
            [
                {"role": "system", "content": (
                    "You are a senior engineer reviewing a file against its codebase conventions. "
                    "Compare to patterns elsewhere. Focus on: bugs, security (OWASP top 10), "
                    "consistency, error handling, naming, race conditions, resource leaks. "
                    "Be specific with line numbers. For each issue, provide the corrected code."
                )},
                {"role": "user", "content": (
                    f"## File: {request.file_path} ({len(lines)} lines)\n"
                    f"```\n{content[:15000]}\n```\n\n"
                    f"{context_sections}"
                )},
            ]
        )

        return {"review": response.content, "file_path": request.file_path}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"File not found: {request.file_path}")
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ─── POST /review/guidelines ─────────────────────────────────────────
@app.post("/review/guidelines")
def load_guidelines_endpoint(request: GuidelinesRequest):
    """Load team coding guidelines for use in reviews."""
    try:
        from src.codewalk.review.guidelines_loader import get_guidelines_store
        import os

        path = request.docs_path or settings.review_guidelines_path
        if not path:
            raise HTTPException(
                status_code=400,
                detail="No path provided. Pass docs_path or set REVIEW_GUIDELINES_PATH.",
            )
        if not os.path.isdir(path):
            raise HTTPException(status_code=404, detail=f"Directory not found: {path}")

        store = get_guidelines_store()
        if not store:
            raise HTTPException(status_code=400, detail=f"No guideline files found in {path}")

        count = store.chunk_count()
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
    store = state.get_doc_store()
    result = store.index_docs(req.docs_path)
    return result

# ─── POST /docs/search ──────────────────────────────────────────────
@app.post("/docs/search")
def docs_search(req: DocsSearchRequest):
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

    store = state.get_doc_store()

    if store.chunk_count() == 0:
        raise HTTPException(status_code=400, detail="No documents indexed yet.")
    
    results = store.search(req.question, n_results=req.n_results)

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
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    

@app.post("/research")
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
        return {"question": report.question, "report": report.markdown, "sources": report.sources}
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
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
      - old_code must be found exactly once
      - Write is atomic (temp file + rename)

    This endpoint REQUIRES the user to have already reviewed the fixes.
    It does NOT ask for approval — the caller (frontend/CLI) is responsible
    for showing fixes and getting user consent before calling this endpoint.

    Returns:
      - 200 with list of applied fixes
      - 400 if any fix fails (stops at first failure, returns what succeeded)
    """
    try:
        from src.codewalk.review.fix_applier import apply_fixes_batch

        repo_path = state.get_repo_path()
        fixes = [fix.model_dump() for fix in request.fixes]

        result = apply_fixes_batch(repo_path, fixes)

        if result["failed"]:
            # Partial success: some applied, one failed
            raise HTTPException(
                status_code=400,
                detail={
                    "message": f"Fix {result['failed']['index']} failed",
                    "error": result["failed"]["error"],
                    "applied_count": len(result["applied"]),
                    "failed_index": result["failed"]["index"],
                },
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
            failed=None,
            total=result["total"],
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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


# ─── Semantic search over codebase embeddings ─────────────────────────

@app.post("/semantic-search", response_model=SemanticSearchResponse)
def semantic_search(request: SemanticSearchRequest):
    """Search the vector index for chunks semantically similar to the query."""
    persist_dir = os.path.join(request.repo_path, ".codewalk", "chroma")
    if not os.path.isdir(persist_dir):
        return SemanticSearchResponse(results=[])

    try:
        store = VectorStore(persist_dir=persist_dir)
        store.create_collection("codebase")
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
    except Exception as e:
        _log(f"[semantic-search] error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ─── Health check ───────────────────────────────────────────────────

@app.get("/health")
def health():
    """Health check endpoint."""
    return {"status": "ok", "codewalk_version": __version__}
