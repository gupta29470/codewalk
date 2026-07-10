"""Pydantic request/response models for the FastAPI server."""
from typing import Any

from pydantic import BaseModel

# ─── REQUEST MODELS ──────────────────────────────────────────────────

class AnalyzeRequest(BaseModel):
    """POST /analyze — request body."""
    repo_path: str | None = None
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
    message: str = ""

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
    diagram: str | None = None
    overview_text: str
    riskiest_files: list[dict] = []

class BlastRadiusResponse(BaseModel):
    """GET /blast-radius/{module_name} — response body."""
    module: str
    module_risk: str
    total_files: int
    files: list[dict]

class ReviewRequest(BaseModel):
    """POST /review — request body.

    By default reviews ALL changes: staged, unstaged, AND new untracked files.
    Use ``staged=True`` for narrow staged-only mode.
    """
    staged: bool = False
    target_branch: str | None = None
    commit: str | None = None
    incremental: bool = False
    force_full_review: bool = False
    narrative_summary: bool = False

class CancelReviewRequest(BaseModel):
    """POST /review/cancel — request body."""
    review_id: str

class CancelReviewResponse(BaseModel):
    """POST /review/cancel — response body."""
    cancelled: bool
    message: str

class ReviewStreamRequest(BaseModel):
    """POST /review/stream — request body.

    By default reviews ALL changes: staged, unstaged, AND new untracked files.
    Use ``staged=True`` for narrow staged-only mode.
    """
    staged: bool = False
    target_branch: str | None = None
    commit: str | None = None
    incremental: bool = False
    force_full_review: bool = False
    narrative_summary: bool = False

class ReviewResponse(BaseModel):
    """POST /review — response body."""
    verdict: str
    verdict_reason: str
    issues: list[dict]
    summary: str
    narrative_summary: str = ""
    files_reviewed: int
    lines_added: int
    lines_removed: int
    session_id: str | None = None
    architecture_flags: dict[str, Any] | None = None
    schema_version: str = "2.0"
    merge_blockers: list[str] = []
    clusters: list[dict] = []
    fixed_count: int = 0
    new_count: int = 0
    still_present_count: int = 0

class ReviewVerdictRequest(BaseModel):
    """POST /review/verdict — record user verdict on a finding."""
    session_id: str
    finding_index: int
    verdict: str  # "accepted" | "rejected"
    reason: str = ""

class ReviewVerdictResponse(BaseModel):
    """POST /review/verdict — response."""
    success: bool
    message: str

class ApplyAcceptedRequest(BaseModel):
    """POST /review/apply-accepted — apply all accepted fixes."""
    session_id: str = ""  # empty = use latest session on branch

class ApplyAcceptedResponse(BaseModel):
    """POST /review/apply-accepted — response."""
    applied: list[str]
    failed: list[str]
    total_accepted: int

class GuidelinesRequest(BaseModel):
    """POST /review/guidelines — request body."""
    docs_path: str | None = None
    repo_path: str | None = None

class ErrorResponse(BaseModel):
    """Error response for any endpoint."""
    error: str
    detail: str = ""

class DocsIndexRequest(BaseModel):
    """Request body for POST /docs/index."""
    docs_path: str
    repo_path: str | None = None

class DocsSearchRequest(BaseModel):
    """Request body for POST /docs/search."""
    query: str
    n_results: int = 5
    repo_path: str | None = None

class DocsAskRequest(BaseModel):
    """Request body for POST /docs/ask."""
    question: str
    n_results: int = 5
    repo_path: str | None = None

class SemanticSearchRequest(BaseModel):
    """POST /semantic-search — request body."""
    query: str
    repo_path: str | None = None
    n_results: int = 5
    collection_name: str | None = None

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
    """Request body for POST /chat/approve."""
    thread_id: str
    action: str = "approve"

class ResearchRequest(BaseModel):
    """Request body for POST /research."""
    question: str
    depth: str = "standard"   # quick | standard | deep

class ResearchDiagramNode(BaseModel):
    """A node in the research architecture diagram."""
    id: str
    type: str  # file | function | class | method
    name: str
    full_path: str
    x: float | None = None
    y: float | None = None
    level: int | None = None
    parentId: str | None = None
    module: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    parent_class: str | None = None
    qualified_name: str | None = None

class ResearchDiagramEdge(BaseModel):
    """An edge in the research architecture diagram."""
    source: str
    target: str
    type: str  # contains | imports | calls | member_of

class ResearchDiagram(BaseModel):
    """Structured diagram payload for the research report."""
    nodes: list[ResearchDiagramNode]
    edges: list[ResearchDiagramEdge]

class ResearchResponse(BaseModel):
    """POST /research — response body."""
    question: str
    report: str
    sources: list[str]
    diagram: ResearchDiagram | None = None

class FixItem(BaseModel):
    """A single code fix to apply."""
    file_path: str
    old_code: str
    new_code: str
class ApplyFixesRequest(BaseModel):
    """POST /review/apply — request body."""
    fixes: list[FixItem]
    continue_on_error: bool = False
    validate_only: bool = False
    run_formatter: bool = True


class AppliedFix(BaseModel):
    """One successfully applied fix."""
    file_path: str
    old_code: str
    new_code: str
    message: str


class ApplyFixesResponse(BaseModel):
    """POST /review/apply — response body."""
    applied: list[AppliedFix]
    failed: list[dict] | None = None
    total: int


class StaticAnalysisIssue(BaseModel):
    """One normalized static-analysis finding."""
    file_path: str
    line: int | None
    column: int | None
    severity: str
    rule: str
    message: str
    category: str
    tool: str


class StaticAnalysisRequest(BaseModel):
    """POST /tools/static-analysis — request body."""
    file_paths: list[str]
    language_hint: str | None = None


class StaticAnalysisResponse(BaseModel):
    """POST /tools/static-analysis — response body."""
    issues: list[StaticAnalysisIssue]
    total: int


class TestRunRequest(BaseModel):
    """POST /tools/run-tests — request body."""
    file_paths: list[str] | None = None
    language_hint: str | None = None
    command: list[str] | None = None


class TestRunResponse(BaseModel):
    """POST /tools/run-tests — response body."""
    command: str
    ok: bool
    returncode: int
    stdout: str
    stderr: str
    error: str | None


# ─── RAG utility endpoints ───────────────────────────────────────────

class ExpandQueryRequest(BaseModel):
    """POST /rag/expand-query — request body."""
    query: str


class ExpandQueryResponse(BaseModel):
    """POST /rag/expand-query — response body."""
    original: str
    queries: list[str]
    symbol_hint: str | None = None


class RerankRequest(BaseModel):
    """POST /rag/rerank — request body."""
    query: str
    results: list[SemanticSearchResult]
    top_k: int | None = None


class RerankResponse(BaseModel):
    """POST /rag/rerank — response body."""
    results: list[SemanticSearchResult]


class SymbolLookupRequest(BaseModel):
    """POST /rag/symbol-lookup — request body."""
    query: str
    include_callers: bool = True
    include_callees: bool = False


class SymbolLookupResponse(BaseModel):
    """POST /rag/symbol-lookup — response body."""
    results: list[SemanticSearchResult]