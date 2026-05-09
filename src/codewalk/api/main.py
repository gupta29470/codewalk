from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from src.codewalk.api.models import (
    AnalyzeRequest, AnalyzeResponse,
    ChatRequest, ChatResponse,
    ModuleResponse, OverviewResponse,
    BlastRadiusResponse,
)
from src.codewalk.api import state

from src.codewalk.pipeline import full_index, reindex
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
            index_result = full_index(request.repo_path, request.collection_name)
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
            print(f"Skipping indexing — collection already has {existing_count} chunks")

        # ── Always run analysis (fast — no embedding) ────────────
        files = scan_directory(request.repo_path)
        deps = build_dependency_graph(files)
        modules_result = detect_modules(files, deps)

        # ── Create agent ─────────────────────────────────────────
        agent = create_agent(store, modules_result)

        # ── Save state ───────────────────────────────────────────
        state.initialize(store, agent, modules_result, index_result)

        return AnalyzeResponse(
            status="complete",
            repo_path=request.repo_path,
            files_scanned=index_result["files_scanned"],
            chunks_created=index_result["chunks_created"],
            modules=list(modules_result["modules"].keys()),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
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

        all_files = scan_directory(analyze_result.get("repo_path", settings.repo_path))
        deps = build_dependency_graph(all_files)
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

        analyze_result = state.get_analyze_result()
        repo_path = analyze_result.get("repo_path", settings.repo_path)
        all_files = scan_directory(repo_path)
        deps = build_dependency_graph(all_files)
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
        all_files = scan_directory(repo_path)
        deps = build_dependency_graph(all_files)
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
       files = scan_directory(repo_path)
       deps = build_dependency_graph(files)
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
        files = scan_directory(repo_path)
        deps = build_dependency_graph(files)
        order = generate_reading_order(files, deps)
        flow = generate_execution_flow(order, deps)
        return {"flow": flow}
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
# ─── Health check ───────────────────────────────────────────────────

@app.get("/health")
def health():
    """Health check endpoint."""
    return {"status": "ok"}
