import logging
import sys
import json

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

logger = logging.getLogger("codewalk")
def _log(msg: str):
    print(msg, file=sys.stderr)
    logger.info(msg)

from src.codewalk.pipeline import full_index_parallel, reindex, chunk_and_embed_parallel
from src.codewalk.analysis.relevance_filter import filter_files_with_llm
from src.codewalk.api.models import (
    AnalyzeRequest, AnalyzeResponse,
    ChatRequest, ChatResponse,
    ModuleResponse, OverviewResponse,
    BlastRadiusResponse,
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


# ─── Create the FastAPI app ─────────────────────────────────────────

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

# ─── POST /analyze ───────────────────────────────────────────────────
@app.post("/analyze", response_model=AnalyzeResponse)
async def analyze(request: AnalyzeRequest):
    """Index a codebase: scan → chunk → embed → store → build agent.

    Modes:
        auto    — skip indexing if collection already has data (default)
        reindex — smart re-index (only changed/new/deleted files)
        full    — nuke everything and re-embed from scratch
    """
    try:
        request.repo_path = request.repo_path or settings.repo_path
        store = VectorStore()
        store.create_collection(request.collection_name)
        existing_count = store.collection.count()

        # ── Decide whether to index ──────────────────────────────
        if request.index_mode == "full" or existing_count == 0:
            index_result = full_index_parallel(request.repo_path, request.collection_name)
        elif request.index_mode == "reindex":
            index_result = reindex(request.repo_path, request.collection_name)
        else:
            # Auto mode + data exists → skip indexing entirely
            index_result = {
                "repo_path": request.repo_path,
                "files_scanned": 0,
                "chunks_created": 0,
                "skipped": True,
            }
            _log(f"[api] Skipping indexing — collection already has {existing_count} chunks")

        # ── Always run analysis (fast — no embedding) ────────────
        files = scan_directory(request.repo_path)
        deps = build_dependency_graph(files)
        modules_result = detect_modules(files, deps)

        # ── Create agent ─────────────────────────────────────────
        agent = create_agent(store, modules_result)

        # ── Save state (including files/deps cache) ─────────────
        state.initialize(store, agent, modules_result, index_result,
                         files=files, deps=deps)

        return AnalyzeResponse(
            status="complete",
            repo_path=request.repo_path,
            files_scanned=index_result["files_scanned"],
            chunks_created=index_result["chunks_created"],
            modules=list(modules_result["modules"].keys()),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
@app.post("/analyze/stream")
async def analyze_stream(request: AnalyzeRequest):
    """Stream analysis progress via Server-Sent Events."""

    def event_stream():
        """Generator that yields SSE events at each pipeline step."""
        try:
            request.repo_path = request.repo_path or settings.repo_path

            # Step 1: Check existing data
            yield f"data: {json.dumps({'step': 'init', 'message': 'Checking existing index...'})}\n\n"
            store = VectorStore()
            store.create_collection(request.collection_name)
            existing_count = store.collection.count()

            # Step 2: Indexing
            if request.index_mode == "full" or existing_count == 0:
                # ── Full index with progress ──
                yield f"data: {json.dumps({'step': 'scan', 'message': 'Scanning directory...'})}\n\n"
                files = scan_directory(request.repo_path)
                scanned_count = len(files)
                yield f"data: {json.dumps({'step': 'scan', 'message': f'Scanned {scanned_count} files'})}\n\n"

                yield f"data: {json.dumps({'step': 'filter', 'message': f'Smart filtering {scanned_count} files via LLM — wait time depends on number of files...'})}\n\n"
                files = filter_files_with_llm(files)
                yield f"data: {json.dumps({'step': 'filter', 'message': f'Kept {len(files)} relevant files (filtered out {scanned_count - len(files)})'})}\n\n"

                yield f"data: {json.dumps({'step': 'chunk', 'message': 'Chunking + embedding in parallel...'})}\n\n"

                embedded, chunks_created = chunk_and_embed_parallel(files)

                yield f"data: {json.dumps({'step': 'chunk', 'message': f'Created {chunks_created} chunks'})}\n\n"
                yield f"data: {json.dumps({'step': 'embed', 'message': f'Embedded {len(embedded)} chunks'})}\n\n"

                yield f"data: {json.dumps({'step': 'store', 'message': 'Storing in vector database...'})}\n\n"
                store.clear_collection()
                store.add_chunks(embedded)
                yield f"data: {json.dumps({'step': 'store', 'message': f'Stored {len(embedded)} chunks in ChromaDB'})}\n\n"

                index_result = {
                    "repo_path": request.repo_path,
                    "files_scanned": len(files),
                    "chunks_created": chunks_created,
                }

            elif request.index_mode == "reindex":
                yield f"data: {json.dumps({'step': 'scan', 'message': 'Scanning for changes...'})}\n\n"
                index_result = reindex(request.repo_path, request.collection_name)
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

            # Step 3: Analysis
            yield f"data: {json.dumps({'step': 'analyze', 'message': 'Building dependency graph...'})}\n\n"
            files = scan_directory(request.repo_path)
            deps = build_dependency_graph(files)
            modules_result = detect_modules(files, deps)
            num_modules = len(modules_result['modules'])
            yield f"data: {json.dumps({'step': 'analyze', 'message': f'Detected {num_modules} modules'})}\n\n"

            # Step 4: Create agent
            yield f"data: {json.dumps({'step': 'agent', 'message': 'Creating AI agent...'})}\n\n"
            agent = create_agent(store, modules_result)

            # Step 5: Save state (including files/deps cache)
            state.initialize(store, agent, modules_result, index_result,
                             files=files, deps=deps)

            # Final event — includes full result
            yield f"data: {json.dumps({'step': 'done', 'message': 'Analysis complete!', 'result': {'status': 'complete', 'repo_path': request.repo_path, 'files_scanned': index_result.get('files_scanned', 0), 'chunks_created': index_result.get('chunks_created', 0), 'modules': list(modules_result['modules'].keys())}})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'step': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
    
# ─── POST /chat ──────────────────────────────────────────────────────
@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """Ask the agent a question about the codebase."""
    try:
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
        answer = result["messages"][-1].content
        return ChatResponse(answer=answer, thread_id=request.thread_id)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
# ─── GET /overview ───────────────────────────────────────────────────
@app.get("/overview", response_model=OverviewResponse)
async def overview():
    """Get the project overview (tech stack, modules, diagram, LLM summary)."""
    try:
        modules_result = state.get_modules_result()
        store = state.get_store()

        # Generate diagram
        diagram = generate_module_diagram(modules_result["module_graph"])

        # Detect tech stack
        analyze_result = state.get_analyze_result()
        tech = detect_tech_stack(analyze_result.get("repo_path", settings.repo_path))

        # Generate overview (calls LLM)
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
    
# ─── GET /modules/{name} ────────────────────────────────────────────
@app.get("/modules/{module_name}", response_model=ModuleResponse)
async def get_module(module_name: str):
    """Get details about a specific module."""
    try:
        module_result = state.get_modules_result()
        modules = module_result["modules"]
        module_graph = module_result["module_graph"]

        # Case-insensitive lookup
        actual_name = None
        for name in modules:
            if name.lower() == module_name.lower():
                actual_name = name
                break
        
        if not actual_name:
            available = ", ".join(sorted(modules.keys()))
            raise HTTPException(
                status_code=404,
                detail=f"Module '{module_name}' not found. Available: {available}",
            )
        
        info = modules[actual_name]
        depends_on = module_graph.get(actual_name, [])
        depended_by = [
            other for other, deps in module_graph.items()
            if actual_name in deps
        ]

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
            name=actual_name,
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
async def get_blast_radius_for_module(module_name: str = ""):
    """Get blast radius for files. Optionally scope to a module."""
    try:
        modules_result = state.get_modules_result()
        analyze_result = state.get_analyze_result()
        repo_path = analyze_result.get("repo_path", settings.repo_path)
        deps = state.get_deps()
        graph = deps["graph"]

        # Determine scope
        if module_name:
            modules = modules_result["modules"]
            actual_name = None
            for name in modules:
                if name.lower() == module_name.lower():
                    actual_name = name
                    break
            if not actual_name:
                available = ", ".join(sorted(modules.keys()))
                raise HTTPException(
                    status_code=404,
                    detail=f"Module '{module_name}' not found. Available: {available}",
                )
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
       analyze_result = state.get_analyze_result()
       repo_path = analyze_result.get("repo_path", settings.repo_path)
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
    
# ─── GET /execution-flow ───────────────────────────────────────
@app.get("/execution-flow")
def get_execution_flow():
    """Get the execution flow diagram and narration."""
    try: 
        analyze_result = state.get_analyze_result()
        repo_path = analyze_result.get("repo_path", settings.repo_path)
        files = state.get_files()
        deps = state.get_deps()
        order = generate_reading_order(files, deps)
        flow = generate_execution_flow(order, deps)
        return {"flow": flow}
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
# ─── POST /refresh ─────────────────────────────────────────────────
@app.post("/refresh")
async def refresh_analysis():
    """Re-scan files and rebuild dependency graph + modules.

    Does NOT re-embed or re-index. Use this after code changes
    to update blast radius, reading order, and module structure.
    """
    try:
        analyze_result = state.get_analyze_result()
        repo_path = analyze_result.get("repo_path", settings.repo_path)

        files = scan_directory(repo_path)
        deps = build_dependency_graph(files)
        modules_result = detect_modules(files, deps)

        state.refresh(files, deps, modules_result)

        return {
            "status": "refreshed",
            "files": len(files),
            "modules": list(modules_result["modules"].keys()),
        }
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ─── Health check ───────────────────────────────────────────────────

@app.get("/health")
def health():
    """Health check endpoint."""
    return {"status": "ok"}
