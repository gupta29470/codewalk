"""
=============================================================================
 main.py - FastAPI REST Server
=============================================================================

WHAT THIS FILE DOES:
    The REST API server for Codewalk. Provides HTTP endpoints that mirror
    MCP tool functionality plus additional features (streaming, voice).

ENDPOINTS:
    POST /analyze         - Index a codebase (scan + chunk + embed)
    POST /analyze/stream  - Same but with SSE progress events
    POST /chat            - Ask the LangGraph agent a question
    GET  /overview        - Project overview (tech, modules, diagram)
    GET  /modules         - List all modules
    GET  /modules/{name}  - Module details + blast radius
    GET  /blast-radius    - Blast radius for all files
    GET  /reading-order   - Recommended file reading order
    GET  /execution-flow  - Execution flow diagram + narration
    POST /refresh         - Rebuild analysis cache (no re-embedding)
    POST /incremental-reindex - Re-embed only changed files
    POST /review          - Review git diff
    POST /review/file     - Review single file
    POST /review/guidelines - Load team guidelines
    POST /voice/ask       - Voice Q&A (audio in, audio + text out)
    GET  /health          - Health check

WHERE IT'S CALLED:
    - `uvicorn src.codewalk.api.main:app` or via Docker
    - Web frontend talks to these endpoints

DEPENDENCIES:
    - state.py: all runtime state
    - models.py: request/response schemas
    - All analysis/generation/review/voice modules

=============================================================================
"""

import logging
import sys
import json
import base64

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi import UploadFile, File, Form
from fastapi.responses import JSONResponse

from src.codewalk.pipeline import full_index_parallel, reindex, chunk_and_embed_parallel, incremental_reindex
from src.codewalk.analysis.relevance_filter import filter_files_with_llm
from src.codewalk.api.models import (
    AnalyzeRequest, AnalyzeResponse,
    ChatRequest, ChatResponse,
    ModuleResponse, OverviewResponse,
    BlastRadiusResponse,
    ReviewRequest, ReviewFileRequest, GuidelinesRequest,
)
from src.codewalk.api import state
from src.codewalk.ingestion.scanner import scan_directory
from src.codewalk.ingestion.tech_detect import detect_tech_stack
from src.codewalk.analysis.dependency_graph import build_dependency_graph
from src.codewalk.analysis.module_detector import detect_modules
from src.codewalk.generation.diagram_generator import generate_module_diagram
from src.codewalk.generation.overview_generator import generate_overview
from src.codewalk.embeddings.vector_store import VectorStore
from src.codewalk.agent.graph import create_agent
from src.codewalk.analysis.reading_order import generate_reading_order
from src.codewalk.generation.flow_generator import generate_execution_flow
from src.codewalk.config import settings
from src.codewalk.analysis.blast_radius import (
    get_blast_radius,
    calculate_full_blast_map,
)
from src.codewalk.voice.stt import transcribe_bytes
from src.codewalk.voice.tts import synthesize
from src.codewalk.voice.router import route
from src.codewalk.voice.backends import execute_direct
from src.codewalk.log import log as _log
from src.codewalk.errors import classify_error

logger = logging.getLogger("codewalk")


# =============================================================================
# App Setup
# =============================================================================

app = FastAPI(
    title="Codewalk API",
    description="AI-powered codebase onboarding tool",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    """Convert all unhandled exceptions to user-friendly messages."""
    user_message = classify_error(exc)
    _log(f"[api] Error: {exc}")
    return JSONResponse(status_code=500, content={"detail": user_message})


# =============================================================================
# POST /analyze - Index a Codebase
# =============================================================================

@app.post("/analyze", response_model=AnalyzeResponse)
async def analyze(request: AnalyzeRequest):
    """Index a codebase: scan -> chunk -> embed -> store -> build agent.

    Modes:
        auto    - skip indexing if collection already has data (default)
        reindex - smart re-index (only changed/new/deleted files)
        full    - nuke everything and re-embed from scratch
    """
    try:
        request.repo_path = request.repo_path or settings.repo_path
        if not request.collection_name:
            request.collection_name = request.repo_path.rstrip("/").split("/")[-1] or "codebase"
        persist_dir = f"{request.repo_path.rstrip('/')}/.codewalk/chroma"
        store = VectorStore(persist_dir=persist_dir)
        store.create_collection(request.collection_name)
        existing_count = store.collection.count()

        # Decide whether to index
        if request.index_mode == "full" or existing_count == 0:
            index_result = full_index_parallel(request.repo_path, request.collection_name, use_llm_filter=settings.use_llm_filter, persist_dir=persist_dir)
        elif request.index_mode == "reindex":
            index_result = reindex(request.repo_path, request.collection_name, persist_dir=persist_dir)
        else:
            index_result = {
                "repo_path": request.repo_path,
                "files_scanned": 0,
                "chunks_created": 0,
                "skipped": True,
            }
            _log(f"[api] Skipping indexing - collection already has {existing_count} chunks")

        # Always run analysis (fast - no embedding)
        files = scan_directory(request.repo_path)
        deps = build_dependency_graph(files)
        modules_result = detect_modules(files, deps)

        # Create agent
        agent = create_agent(store, modules_result, files=files, deps=deps)

        # Save state
        state.initialize(store, agent, modules_result, index_result,
                         files=files, deps=deps, repo_path=request.repo_path)

        return AnalyzeResponse(
            status="complete",
            repo_path=request.repo_path,
            files_scanned=index_result["files_scanned"],
            chunks_created=index_result["chunks_created"],
            modules=list(modules_result["modules"].keys()),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# POST /analyze/stream - SSE Progress Stream
# =============================================================================

@app.post("/analyze/stream")
async def analyze_stream(request: AnalyzeRequest):
    """Stream analysis progress via Server-Sent Events."""

    def event_stream():
        try:
            request.repo_path = request.repo_path or settings.repo_path
            if not request.collection_name:
                request.collection_name = request.repo_path.rstrip("/").split("/")[-1] or "codebase"

            yield f"data: {json.dumps({'step': 'init', 'message': 'Checking existing index...'})}\n\n"
            persist_dir = f"{request.repo_path.rstrip('/')}/.codewalk/chroma"
            store = VectorStore(persist_dir=persist_dir)
            store.create_collection(request.collection_name)
            existing_count = store.collection.count()

            if request.index_mode == "full" or existing_count == 0:
                yield f"data: {json.dumps({'step': 'scan', 'message': 'Scanning directory...'})}\n\n"
                files = scan_directory(request.repo_path)
                scanned_count = len(files)
                yield f"data: {json.dumps({'step': 'scan', 'message': f'Scanned {scanned_count} files'})}\n\n"

                if settings.use_llm_filter:
                    yield f"data: {json.dumps({'step': 'filter', 'message': f'Smart filtering {scanned_count} files via LLM...'})}\n\n"
                    files = filter_files_with_llm(files)
                    yield f"data: {json.dumps({'step': 'filter', 'message': f'Kept {len(files)} relevant files'})}\n\n"
                else:
                    yield f"data: {json.dumps({'step': 'filter', 'message': 'LLM filter disabled'})}\n\n"

                yield f"data: {json.dumps({'step': 'chunk', 'message': 'Chunking + embedding in parallel...'})}\n\n"
                embedded, chunks_created = chunk_and_embed_parallel(files)
                yield f"data: {json.dumps({'step': 'chunk', 'message': f'Created {chunks_created} chunks'})}\n\n"
                yield f"data: {json.dumps({'step': 'embed', 'message': f'Embedded {len(embedded)} chunks'})}\n\n"

                yield f"data: {json.dumps({'step': 'store', 'message': 'Storing in vector database...'})}\n\n"
                store.clear_collection()
                store.add_chunks(embedded)
                yield f"data: {json.dumps({'step': 'store', 'message': f'Stored {len(embedded)} chunks'})}\n\n"

                index_result = {"repo_path": request.repo_path, "files_scanned": len(files), "chunks_created": chunks_created}

            elif request.index_mode == "reindex":
                yield f"data: {json.dumps({'step': 'scan', 'message': 'Scanning for changes...'})}\n\n"
                index_result = reindex(request.repo_path, request.collection_name, persist_dir=persist_dir)
                msg = f"New: {index_result['new_files']}, Changed: {index_result['changed_files']}, Deleted: {index_result['deleted_files']}"
                yield f"data: {json.dumps({'step': 'reindex', 'message': msg})}\n\n"
            else:
                yield f"data: {json.dumps({'step': 'skip', 'message': f'Index exists ({existing_count} chunks) - skipping'})}\n\n"
                index_result = {"repo_path": request.repo_path, "files_scanned": 0, "chunks_created": 0, "skipped": True}

            # Analysis
            yield f"data: {json.dumps({'step': 'analyze', 'message': 'Building dependency graph...'})}\n\n"
            files = scan_directory(request.repo_path)
            deps = build_dependency_graph(files)
            modules_result = detect_modules(files, deps)
            num_modules = len(modules_result['modules'])
            yield f"data: {json.dumps({'step': 'analyze', 'message': f'Detected {num_modules} modules'})}\n\n"

            # Agent
            yield f"data: {json.dumps({'step': 'agent', 'message': 'Creating AI agent...'})}\n\n"
            agent = create_agent(store, modules_result, files=files, deps=deps)

            # Save state
            state.initialize(store, agent, modules_result, index_result,
                             files=files, deps=deps, repo_path=request.repo_path)

            yield f"data: {json.dumps({'step': 'done', 'message': 'Analysis complete!', 'result': {'status': 'complete', 'repo_path': request.repo_path, 'files_scanned': index_result.get('files_scanned', 0), 'chunks_created': index_result.get('chunks_created', 0), 'modules': list(modules_result['modules'].keys())}})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'step': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# =============================================================================
# POST /chat - Agent Q&A
# =============================================================================

@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """Ask the LangGraph agent a question about the codebase."""
    try:
        state.ensure_initialized()
        agent = state.get_agent()
        config = {"configurable": {"thread_id": request.thread_id}}
        result = agent.invoke({"messages": [("human", request.message)]}, config=config)
        answer = result["messages"][-1].content
        return ChatResponse(answer=answer, thread_id=request.thread_id)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# GET /overview - Project Overview
# =============================================================================

@app.get("/overview", response_model=OverviewResponse)
async def overview():
    """Get project overview (tech stack, modules, diagram, LLM summary)."""
    try:
        state.ensure_initialized()
        modules_result = state.get_modules_result()
        store = state.get_store()

        diagram = generate_module_diagram(modules_result["module_graph"])
        analyze_result = state.get_analyze_result()
        tech = detect_tech_stack(analyze_result.get("repo_path", settings.repo_path))
        overview_text = generate_overview(tech, modules_result, diagram)

        deps = state.get_deps()
        blast_map = calculate_full_blast_map(deps["graph"])
        top_risky = []
        for item in blast_map["blast_map"][:3]:
            radius = get_blast_radius(item["file"], deps["graph"])
            top_risky.append({
                **item,
                "direct": [f.split("/")[-1] for f in radius["direct"]],
                "transitive": [f.split("/")[-1] for f in radius["transitive"]],
            })

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


# =============================================================================
# GET /modules - List and Details
# =============================================================================

@app.get("/modules")
def list_modules():
    """List all available modules."""
    try:
        state.ensure_initialized()
        modules_result = state.get_modules_result()
        return {"modules": list(modules_result["modules"].keys()), "total": modules_result["stats"]["total_modules"]}
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/modules/{module_name}", response_model=ModuleResponse)
async def get_module(module_name: str):
    """Get details about a specific module (files, deps, blast radius)."""
    try:
        state.ensure_initialized()
        module_result = state.get_modules_result()
        modules = module_result["modules"]
        module_graph = module_result["module_graph"]

        # Case-insensitive lookup
        actual_name = None
        for name in modules:
            if name.lower() == module_name.lower():
                actual_name = name
                break

        # Fallback: search for sub-folder (e.g. "users" inside features/)
        matched_as_feature = False
        if not actual_name:
            source_root = module_result.get("source_root", "")
            for mod_name, mod_info in modules.items():
                prefix = f"{source_root}/{mod_name}/{module_name.lower()}/" if source_root else f"{mod_name}/{module_name.lower()}/"
                matching_files = [f for f in mod_info["files"] if f.lower().startswith(prefix.lower())]
                if matching_files:
                    actual_name = mod_name
                    matched_as_feature = True
                    from collections import Counter
                    lang_counter = Counter()
                    all_files = state.get_files()
                    matching_set = set(matching_files)
                    for f in all_files:
                        if f["file_path"] in matching_set:
                            lang_counter[f["language"]] += 1
                    info = {"files": matching_files, "file_count": len(matching_files), "languages": dict(lang_counter)}
                    break

        if not actual_name:
            available = ", ".join(sorted(modules.keys()))
            raise HTTPException(status_code=404, detail=f"Module '{module_name}' not found. Available: {available}")

        if not matched_as_feature:
            info = modules[actual_name]
        depends_on = module_graph.get(actual_name, [])
        depended_by = [other for other, deps_list in module_graph.items() if actual_name in deps_list]

        deps = state.get_deps()
        graph = deps["graph"]
        file_risks = []
        risk_order = {"critical": 4, "high": 3, "moderate": 2, "low": 1, "none": 0}
        max_risk = "low"

        for file_path in sorted(info["files"]):
            radius = get_blast_radius(file_path, graph)
            file_risks.append({
                "file": file_path,
                "affected_files": radius["affected_files"],
                "risk_level": radius["risk_level"],
                "direct": [f.split("/")[-1] for f in radius["direct"]],
                "transitive": [f.split("/")[-1] for f in radius["transitive"]],
            })
            if risk_order.get(radius["risk_level"], 0) > risk_order.get(max_risk, 0):
                max_risk = radius["risk_level"]

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


# =============================================================================
# GET /blast-radius
# =============================================================================

@app.get("/blast-radius/{module_name}", response_model=BlastRadiusResponse)
@app.get("/blast-radius", response_model=BlastRadiusResponse)
async def get_blast_radius_for_module(module_name: str = ""):
    """Get blast radius for files, optionally scoped to a module."""
    try:
        state.ensure_initialized()
        modules_result = state.get_modules_result()
        deps = state.get_deps()
        graph = deps["graph"]

        if module_name:
            modules = modules_result["modules"]
            actual_name = None
            for name in modules:
                if name.lower() == module_name.lower():
                    actual_name = name
                    break
            if not actual_name:
                available = ", ".join(sorted(modules.keys()))
                raise HTTPException(status_code=404, detail=f"Module '{module_name}' not found. Available: {available}")
            target_files = sorted(modules[actual_name]["files"])
            scope = actual_name
        else:
            target_files = sorted(graph.keys())
            scope = "all"

        risk_order = {"critical": 4, "high": 3, "moderate": 2, "low": 1, "none": 0}
        max_risk = "low"
        file_results = []

        for file_path in target_files:
            radius = get_blast_radius(file_path, graph)
            file_results.append({
                "file": file_path,
                "risk_level": radius["risk_level"],
                "affected_files": radius["affected_files"],
                "direct": [f.split("/")[-1] for f in radius["direct"]],
                "transitive": [f.split("/")[-1] for f in radius["transitive"]],
            })
            if risk_order.get(radius["risk_level"], 0) > risk_order.get(max_risk, 0):
                max_risk = radius["risk_level"]

        file_results.sort(key=lambda x: x["affected_files"], reverse=True)

        return BlastRadiusResponse(module=scope, module_risk=max_risk, total_files=len(file_results), files=file_results)
    except HTTPException:
        raise
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# GET /reading-order and /execution-flow
# =============================================================================

@app.get("/reading-order")
def get_reading_order_endpoint():
    """Get recommended reading order with blast radius info."""
    try:
        state.ensure_initialized()
        files = state.get_files()
        deps = state.get_deps()
        order = generate_reading_order(files, deps)
        graph = deps["graph"]
        for item in order["order"]:
            radius = get_blast_radius(item["file"], graph)
            item["risk_level"] = radius["risk_level"]
            item["affected_files"] = radius["affected_files"]
            item["direct"] = [f.split("/")[-1] for f in radius["direct"]]
            item["transitive"] = [f.split("/")[-1] for f in radius["transitive"]]
        return order
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/execution-flow")
def get_execution_flow_endpoint():
    """Get execution flow diagram and narration."""
    try:
        state.ensure_initialized()
        files = state.get_files()
        deps = state.get_deps()
        order = generate_reading_order(files, deps)
        flow = generate_execution_flow(order, deps)
        return {"flow": flow}
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# POST /refresh and /incremental-reindex
# =============================================================================

@app.post("/refresh")
async def refresh_analysis():
    """Re-scan files and rebuild analysis. No re-embedding."""
    try:
        state.ensure_initialized()
        analyze_result = state.get_analyze_result()
        repo_path = analyze_result.get("repo_path", settings.repo_path)

        files = scan_directory(repo_path)
        deps = build_dependency_graph(files)
        modules_result = detect_modules(files, deps)
        state.refresh(files, deps, modules_result)

        return {"status": "refreshed", "files": len(files), "modules": list(modules_result["modules"].keys())}
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/incremental-reindex")
async def incremental_reindex_endpoint():
    """Re-embed only files that changed since last indexing."""
    try:
        store = state.get_store()
        repo_path = state.get_analyze_result().get("repo_path", settings.repo_path)
        collection_name = store.collection.name
        persist_dir = f"{repo_path.rstrip('/')}/.codewalk/chroma"
        indexed_files = list(store.get_all_indexed_files())
        if not indexed_files:
            raise HTTPException(status_code=400, detail="No files indexed yet. Run /analyze first.")

        result = incremental_reindex(indexed_files, repo_path, collection_name, persist_dir=persist_dir)

        files = scan_directory(repo_path)
        deps = build_dependency_graph(files)
        modules_result = detect_modules(files, deps)
        state.refresh(files, deps, modules_result)

        return result
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# POST /review endpoints
# =============================================================================

@app.post("/review")
async def review_endpoint(request: ReviewRequest):
    """Review current git diff for bugs, security issues, and style."""
    try:
        from src.codewalk.review.reviewer import review_diff

        store = None
        deps = None
        try:
            store = state.get_store()
            deps = state.get_deps()
        except RuntimeError:
            pass

        result = review_diff(
            staged=request.staged,
            target_branch=request.target_branch,
            use_llm=True,
            store=store,
            deps=deps,
        )

        issues = [
            {
                "severity": issue.severity.value,
                "category": issue.category.value,
                "file_path": issue.file_path,
                "line_number": issue.line_number,
                "title": issue.title,
                "explanation": issue.explanation,
                "suggestion": issue.suggestion,
                "code_snippet": issue.code_snippet,
            }
            for issue in result.issues
        ]

        return {
            "issues": issues,
            "summary": result.summary,
            "files_reviewed": result.files_reviewed,
            "lines_added": result.lines_added,
            "lines_removed": result.lines_removed,
        }
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/review/file")
async def review_file_endpoint(request: ReviewFileRequest):
    """Review a single file against codebase conventions."""
    try:
        from src.codewalk.rag.chain import format_context
        from src.codewalk.config import get_llm

        store = state.get_store()

        with open(request.file_path, "r") as f:
            content = f.read()

        results = store.search(f"code in {request.file_path}", n_results=5)
        patterns = format_context(results) if results else "No indexed context."

        llm = get_llm(temperature=0)
        response = llm.invoke([
            {"role": "system", "content": (
                "You review a file against its codebase conventions. "
                "Compare to patterns elsewhere. Focus on: consistency, "
                "error handling, naming, potential bugs. Be specific with lines."
            )},
            {"role": "user", "content": (
                f"## File:\n```\n{content[:10000]}\n```\n\n"
                f"## Patterns elsewhere:\n{patterns}"
            )},
        ])

        return {"review": response.content, "file_path": request.file_path}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"File not found: {request.file_path}")
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/review/guidelines")
async def load_guidelines_endpoint(request: GuidelinesRequest):
    """Load team coding guidelines for use in reviews."""
    try:
        from src.codewalk.review.guidelines_loader import get_guidelines_store
        import os

        path = request.docs_path or settings.review_guidelines_path
        if not path:
            raise HTTPException(status_code=400, detail="No path provided. Pass docs_path or set REVIEW_GUIDELINES_PATH.")
        if not os.path.isdir(path):
            raise HTTPException(status_code=404, detail=f"Directory not found: {path}")

        store = get_guidelines_store()
        if not store:
            raise HTTPException(status_code=400, detail=f"No guideline files found in {path}")

        count = store.collection.count()
        return {"status": "loaded", "chunks": count, "path": path}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# POST /voice/ask - Voice Q&A
# =============================================================================

@app.post("/voice/ask")
async def voice_ask_endpoint(
    audio: UploadFile = File(...),
    thread_id: str = Form("voice"),
):
    """Voice-in, voice-out codebase Q&A.

    Accepts audio file -> transcribes -> routes to tool -> executes ->
    generates speech summary -> returns text + audio.
    """
    # 1. Speech-to-text
    audio_bytes = await audio.read()
    question = transcribe_bytes(audio_bytes, file_name=audio.filename or "audio.webm")

    if not question.strip():
        fallback = "I didn't catch that. Could you try again?"
        return JSONResponse({
            "question": "", "tool": None, "answer": fallback,
            "speech": fallback, "audio_base64": base64.b64encode(synthesize(fallback)).decode(),
        })

    # 2. Route to tool
    route_result = route(question)
    tool_name = route_result.get("tool")
    arguments = route_result.get("arguments", {})

    if not tool_name:
        fallback = "Sorry, I couldn't match that to a Codewalk tool."
        return JSONResponse({
            "question": question, "tool": None, "answer": fallback,
            "speech": fallback, "audio_base64": base64.b64encode(synthesize(fallback)).decode(),
        })

    # 3. Auto-load index if needed
    state.ensure_initialized()

    # 4. Execute tool
    result = execute_direct(tool_name, arguments)

    # 5. Generate speech version
    from src.codewalk.voice.companion import format_voice_response
    voice = format_voice_response(result)

    # 6. TTS
    audio_response = synthesize(voice["speech"])

    return JSONResponse({
        "question": question,
        "tool": tool_name,
        "answer": voice["technical"],
        "speech": voice["speech"],
        "audio_base64": base64.b64encode(audio_response).decode(),
    })


# =============================================================================
# Health Check
# =============================================================================

@app.get("/health")
def health():
    """Health check endpoint."""
    return {"status": "ok"}
