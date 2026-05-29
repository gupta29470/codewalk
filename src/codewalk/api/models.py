from pydantic import BaseModel

# ─── REQUEST MODELS ──────────────────────────────────────────────────

class AnalyzeRequest(BaseModel):
    """POST /analyze — request body."""
    repo_path: str = ""
    collection_name: str = ""
    index_mode: str = "auto"  # "auto" | "reindex" | "full"

class ChatRequest(BaseModel):
    """POST /chat — request body."""
    message: str
    thread_id: str = "default"

# ─── RESPONSE MODELS ────────────────────────────────────────────────

class AnalyzeResponse(BaseModel):
    """POST /analyze — response body."""
    status: str
    repo_path: str
    files_scanned: int
    chunks_created: int
    modules: list[str]

class ChatResponse(BaseModel):
    """POST /chat — response body."""
    answer: str
    thread_id: str

class ModuleResponse(BaseModel):
    """GET /modules/{name} — response body."""
    name: str
    file_count: int
    files: list[str]
    languages: dict[str, int]
    depends_on: list[str]
    depended_by: list[str]
    blast_radius: list[dict] = []
    module_risk: str = "low"

class OverviewResponse(BaseModel):
    """GET /overview — response body."""
    tech_stack: list[str]
    total_files: int
    total_modules: int
    modules: list[str]
    diagram: str
    overview_text: str
    riskiest_files: list[dict] = []

class BlastRadiusResponse(BaseModel):
    """GET /blast-radius/{module_name} — response body."""
    module: str
    module_risk: str
    total_files: int
    files: list[dict]

class ReviewRequest(BaseModel):
    """POST /review — request body."""
    staged: bool = False
    target_branch: str | None = None
    commit: str | None = None

class ReviewFileRequest(BaseModel):
    """POST /review/file — request body."""
    file_path: str

class GuidelinesRequest(BaseModel):
    """POST /review/guidelines — request body."""
    docs_path: str | None = None

class ErrorResponse(BaseModel):
    """Error response for any endpoint."""
    error: str
    detail: str = ""

class DocsIndexRequest(BaseModel):
    docs_path: str

class DocsSearchRequest(BaseModel):
    query: str
    n_results: int = 5

class DocsAskRequest(BaseModel):
    question: str
    n_results: int = 5