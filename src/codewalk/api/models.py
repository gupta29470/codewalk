"""
=============================================================================
 models.py - Pydantic Request/Response Models for FastAPI
=============================================================================

WHAT THIS FILE DOES:
    Defines all the request and response shapes for the REST API.
    FastAPI uses these for:
    - Automatic request validation
    - Response serialization
    - OpenAPI/Swagger docs generation

=============================================================================
"""

from pydantic import BaseModel


# =============================================================================
# Request Models
# =============================================================================

class AnalyzeRequest(BaseModel):
    """POST /analyze - request body.

    EXAMPLE:
        {"repo_path": "data/repos/fatih/color", "collection_name": "color", "index_mode": "auto"}
        {"repo_path": "", "collection_name": "", "index_mode": "full"}  # uses settings.repo_path
    """
    repo_path: str = ""
    collection_name: str = ""
    index_mode: str = "auto"  # "auto" | "reindex" | "full"


class ChatRequest(BaseModel):
    """POST /chat - request body."""
    message: str
    thread_id: str = "default"


class ReviewRequest(BaseModel):
    """POST /review - request body."""
    staged: bool = False
    target_branch: str | None = None


class ReviewFileRequest(BaseModel):
    """POST /review/file - request body."""
    file_path: str


class GuidelinesRequest(BaseModel):
    """POST /review/guidelines - request body."""
    docs_path: str | None = None


# =============================================================================
# Response Models
# =============================================================================

class AnalyzeResponse(BaseModel):
    """POST /analyze - response body.

    EXAMPLE:
        {"status": "complete", "repo_path": "data/repos/fatih/color",
         "files_scanned": 9, "chunks_created": 348, "modules": ["color"]}
    """
    status: str
    repo_path: str
    files_scanned: int
    chunks_created: int
    modules: list[str]


class ChatResponse(BaseModel):
    """POST /chat - response body."""
    answer: str
    thread_id: str


class ModuleResponse(BaseModel):
    """GET /modules/{name} - response body."""
    name: str
    file_count: int
    files: list[str]
    languages: dict[str, int]
    depends_on: list[str]
    depended_by: list[str]
    blast_radius: list[dict] = []
    module_risk: str = "low"


class OverviewResponse(BaseModel):
    """GET /overview - response body.

    EXAMPLE:
        {"tech_stack": ["Go (9 files)"], "total_files": 9, "total_modules": 1,
         "modules": ["color"], "diagram": "graph TD\n  color",
         "overview_text": "A Go color library...",
         "riskiest_files": [{"file": "color.go", "risk": "high", "dependents": 5}]}
    """
    tech_stack: list[str]
    total_files: int
    total_modules: int
    modules: list[str]
    diagram: str
    overview_text: str
    riskiest_files: list[dict] = []


class BlastRadiusResponse(BaseModel):
    """GET /blast-radius/{module_name} - response body."""
    module: str
    module_risk: str
    total_files: int
    files: list[dict]


class ErrorResponse(BaseModel):
    """Error response for any endpoint."""
    error: str