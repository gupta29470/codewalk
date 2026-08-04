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

    An explicit review target is required: ``target_branch="current"`` for this
    branch's local work, ``target_branch="<base>"`` to compare against that base
    (committed + uncommitted changes, from the merge-base through the working
    tree), ``staged=True`` for narrow staged-only mode, or ``commit`` for a
    historical snapshot. Requests without any target are rejected with 400 —
    the base is never assumed.
    """
    staged: bool = False
    target_branch: str | None = None
    commit: str | None = None
    repo_path: str | None = None


class ReReviewRequest(BaseModel):
    """POST /review/re-review — re-run a review using a previous session's findings as context.

    Same target rules as POST /review: an explicit ``target_branch``
    (``"current"`` or a base branch), ``staged=True``, or ``commit`` is required.
    """
    session_id: str
    repo_path: str | None = None
    staged: bool = False
    target_branch: str | None = None
    commit: str | None = None


class CancelReviewRequest(BaseModel):
    """POST /review/cancel — request body."""
    review_id: str

class CancelReviewResponse(BaseModel):
    """POST /review/cancel — response body."""
    cancelled: bool
    message: str

class ReviewFileRequest(BaseModel):
    """POST /review/file — request body."""
    file_path: str
    repo_path: str | None = None


class ReviewFileResponse(BaseModel):
    """POST /review/file — response body."""
    file_path: str
    issues: list[dict]
    static_issues: list[dict] = []
    files_reviewed: int
    lines_added: int
    lines_removed: int
    session_id: str | None = None


class ReviewStreamRequest(BaseModel):
    """POST /review/stream — request body.

    Same target rules as POST /review: an explicit ``target_branch``
    (``"current"`` or a base branch), ``staged=True``, or ``commit`` is
    required; requests without any target are rejected with 400.
    """
    staged: bool = False
    target_branch: str | None = None
    commit: str | None = None
    repo_path: str | None = None

class ReviewResponse(BaseModel):
    """POST /review — response body."""
    issues: list[dict]
    static_issues: list[dict] = []
    files_reviewed: int
    lines_added: int
    lines_removed: int
    session_id: str | None = None
    architecture_flags: dict[str, Any] | None = None
    schema_version: str = "2.0"

class PreviewEditsRequest(BaseModel):
    """POST /review/preview-edits — batch verdicts + generate edit previews without writing."""
    session_id: str  # required: session to load findings from
    verdicts: dict[str, str] = {}  # {finding_index: "accepted"|"rejected"}, unset = null


class EditPreview(BaseModel):
    """One previewed edit (no writes performed)."""
    finding_index: int
    file_path: str
    original_content: str | None = None
    modified_content: str | None = None
    error: str | None = None


class PreviewEditsResponse(BaseModel):
    """POST /review/preview-edits — response."""
    previews: list[EditPreview]
    total_accepted: int


class ApprovedEdit(BaseModel):
    """One user-approved edit to write to disk."""
    finding_index: int
    file_path: str
    modified_content: str
    original_content: str | None = None  # preview snapshot; write refused if file changed since


class ApplyEditsRequest(BaseModel):
    """POST /review/apply-edits — write user-approved edits + verify."""
    session_id: str
    edits: list[ApprovedEdit]


class ApplyEditsResponse(BaseModel):
    """POST /review/apply-edits — response."""
    applied: list[str]
    failed: list[str]
    total: int
    static_analysis_issues: int = 0
    tests_passed: bool | None = None
    verification_passed: bool | None = None

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