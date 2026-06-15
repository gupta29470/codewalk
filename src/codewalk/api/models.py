from pydantic import BaseModel

# ─── REQUEST MODELS ──────────────────────────────────────────────────

class AnalyzeRequest(BaseModel):
    """POST /analyze — request body."""
    repo_path: str
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
    interrupted: bool = False
    proposed_action: str = ""

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
    reflect: bool = False  # if True, run reflection pass after initial review
    iterations: int = 1    # reflection iterations (only used when reflect=True)

class ReviewFileRequest(BaseModel):
    """POST /review/file — request body."""
    file_path: str

class ReviewResponse(BaseModel):
    """POST /review — response body."""
    verdict: str
    verdict_reason: str
    issues: list[dict]
    summary: str
    files_reviewed: int
    lines_added: int
    lines_removed: int

class ReviewFileResponse(BaseModel):
    """POST /review/file — response body."""
    review: str
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

class SemanticSearchRequest(BaseModel):
    """POST /semantic-search — request body."""
    query: str
    repo_path: str
    n_results: int = 5

class SemanticSearchResult(BaseModel):
    """One semantic search hit."""
    id: str
    text: str
    metadata: dict
    distance: float | None = None

class SemanticSearchResponse(BaseModel):
    """POST /semantic-search — response body."""
    results: list[SemanticSearchResult]

class ApproveRequest(BaseModel):
    thread_id: str         
    action: str = "approve"

class ResearchRequest(BaseModel):
    question: str
    depth: str = "standard"   # quick | standard | deep

class FixItem(BaseModel):
    """A single code fix to apply."""
    file_path: str
    old_code: str
    new_code: str
class ApplyFixesRequest(BaseModel):
    """POST /review/apply — request body."""
    fixes: list[FixItem]

class AppliedFix(BaseModel):
    """One successfully applied fix."""
    file_path: str
    old_code: str
    new_code: str
    message: str


class ApplyFixesResponse(BaseModel):
    """POST /review/apply — response body."""
    applied: list[AppliedFix]
    failed: dict | None = None
    total: int