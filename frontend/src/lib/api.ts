const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

// ─── Types ──────────────────────────────────────────────────────────

export interface AnalyzeResponse {
    status: string;
    repo_path: string;
    files_scanned: number;
    chunks_created: number;
    modules: string[];
}

export interface ChatResponse {
    answer: string;
    thread_id: string;
}

export interface OverviewResponse {
    tech_stack: string[];
    total_files: number;
    total_modules: number;
    modules: string[];
    diagram: string;
    overview_text: string;
    riskiest_files: FileRisk[];
}

export interface ModuleResponse {
    name: string;
    file_count: number;
    files: string[];
    languages: Record<string, number>;
    depends_on: string[];
    depended_by: string[];
    blast_radius: FileRisk[];
    module_risk: string;
}

export interface ModulesListResponse {
    modules: string[];
    total: number;
}

export interface BlastRadiusResponse {
    module: string;
    module_risk: string;
    total_files: number;
    files: FileRisk[];
}

export interface FileRisk {
    file: string;
    risk_level: string;
    affected_files: number;
    direct: string[];
    transitive: string[];
}

export interface ReadingOrderItem {
    file: string;
    priority: string;
    reason: string;
    risk_level?: string;
    affected_files?: number;
    direct?: string[];
    transitive?: string[];
}

export interface ReadingOrderResponse {
    order: ReadingOrderItem[];
    total_files: number;
}

export interface ExecutionFlowResponse {
    flow: string;
}

export interface ArchitectureStats {
    file_graph: {
        vertices: number;
        edges: number;
        is_dag: boolean;
    };
    module_graph: {
        vertices: number;
        edges: number;
    };
}

export interface CentralityItem {
    file: string;
    score: number;
}

export interface ArchitectureCentrality {
    betweenness: CentralityItem[];
    pagerank: CentralityItem[];
}

export interface CycleGroup {
    cycle_groups: string[][];
    has_cycles: boolean;
    edges_to_break: [string, string][];
}

export interface ArchitectureResponse {
    stats: ArchitectureStats;
    centrality: ArchitectureCentrality;
    cycles: CycleGroup;
}

export interface FixItem {
    file_path: string;
    old_code: string;
    new_code: string;
}

export interface AppliedFix {
    file_path: string;
    old_code: string;
    new_code: string;
    message: string;
}

export interface ApplyFixesResponse {
    applied: AppliedFix[];
    failed: { index: number; error: string }[] | null;
    total: number;
}

export interface DocsIndexResponse {
    status: string;
    files_indexed: number;
    chunks_created: number;
}

export interface DocsSearchResult {
    text: string;
    metadata: Record<string, unknown>;
    distance: number;
}

export interface DocsSearchResponse {
    query: string;
    results: DocsSearchResult[];
}

export interface DocsAskResponse {
    answer: string;
    sources: { doc_path: string; section: string }[];
}

export interface ResearchResponse {
    question: string;
    report: string;
    sources: string[];
}

export interface ReviewIssue {
    severity: string;
    category: string;
    file_path: string;
    line_number: number | null;
    title: string;
    explanation: string;
    suggestion: string | null;
    code_snippet: string | null;
}

export interface ReviewResponse {
    issues: ReviewIssue[];
    summary: string;
    files_reviewed: number;
    lines_added: number;
    lines_removed: number;
    verdict: string;
    verdict_reason: string;
}

export interface ReviewFileResponse {
    verdict: string;
    verdict_reason: string;
    issues: ReviewIssue[];
    summary: string;
    file_path: string;
}

export interface IncrementalReindexResponse {
    repo_path: string;
    files_on_disk: number;
    files_skipped: number;
    files_reindexed: number;
    files_deleted: number;
    chunks_embedded: number;
    total_time: string;
}

export interface AdminRepo {
    full_name: string;
    name: string;
    owner: string;
    branch: string;
    last_indexed_sha: string | null;
    index_status: string | null;
    created_at: string | null;
    updated_at: string | null;
    job_status: string | null;
    job_commit: string | null;
    job_finished: string | null;
    job_error: string | null;
}

export interface ProgressEvent {
    step: string;
    message: string;
    result?: AnalyzeResponse;
}

export interface StreamEvent {
    type: "token" | "tool_start" | "tool_end" | "done" | "error" | "interrupted";
    content?: string;           // present for type="token"
    name?: string;              // present for type="tool_start" | "tool_end"
    message?: string;           // present for type="error"
    proposed_action?: string;   // present for type="interrupted"
}

export interface StalenessAlert {
    kind: "index" | "software" | "index_build";
    stale: boolean;
    context?: "cloud" | "local";
    title: string;
    message: string;
    action_mcp: string;
    action_api: string;
    release_notes_url?: string;
}

export interface StalenessStatus {
    has_updates: boolean;
    index_stale: boolean;
    software_stale: boolean;
    index_build_stale: boolean;
    alerts: StalenessAlert[];
    version: {
        codewalk_version: string;
        commit_sha_short: string;
    };
    cloud_configured: boolean;
}

// ─── API Client ─────────────────────────────────────────────────────

async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
    const res = await fetch(`${API_BASE}${path}`, {
        ...options,
        headers: {
            "Content-Type": "application/json",
            ...options?.headers,
        },
    });

    if (!res.ok) {
        const error = await res.json().catch(() => ({ detail: res.statusText }));
        const detail =
            typeof error.detail === "string"
                ? error.detail
                : JSON.stringify(error.detail ?? error);
        throw new Error(detail || `API error: ${res.status}`);
    }

    return res.json();
}

export const api = {
    health: () => apiFetch<{ status: string }>("/health"),

    version: () =>
        apiFetch<{ codewalk_version: string; commit_sha: string; released_at: string }>("/version"),

    getStaleness: () => apiFetch<StalenessStatus>("/staleness"),

    analyze: (indexMode: string = "auto") =>
        apiFetch<AnalyzeResponse>("/analyze", {
            method: "POST",
            body: JSON.stringify({
                index_mode: indexMode,
            }),
        }),

    getOverview: () => apiFetch<OverviewResponse>("/overview"),

    getModules: () => apiFetch<ModulesListResponse>("/modules"),

    getModule: (name: string) =>
        apiFetch<ModuleResponse>(`/modules/${encodeURIComponent(name)}`),

    getBlastRadius: (module?: string) =>
        apiFetch<BlastRadiusResponse>(
            module ? `/blast-radius/${encodeURIComponent(module)}` : "/blast-radius"
        ),

    getReadingOrder: () => apiFetch<ReadingOrderResponse>("/reading-order"),

    getExecutionFlow: () => apiFetch<ExecutionFlowResponse>("/execution-flow"),

    analyzeStream: async (
        indexMode: string,
        onProgress: (event: ProgressEvent) => void,
        collectionName?: string
    ) => {
        const res = await fetch(`${API_BASE}/analyze/stream`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                index_mode: indexMode,
                collection_name: collectionName || "codebase",
            }),
        });

        if (!res.ok) {
            const error = await res.json().catch(() => ({ detail: res.statusText }));
            throw new Error(error.detail || `API error: ${res.status}`);
        }

        const reader = res.body?.getReader();
        if (!reader) throw new Error("No response stream");

        const decoder = new TextDecoder();
        let buffer = "";

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split("\n");
            buffer = lines.pop() || "";

            for (const line of lines) {
                if (line.startsWith("data: ")) {
                    const event: ProgressEvent = JSON.parse(line.slice(6));
                    onProgress(event);
                }
            }
        }
    },

    chat: (message: string, threadId: string = "default") =>
        apiFetch<ChatResponse>("/chat", {
            method: "POST",
            body: JSON.stringify({ message, thread_id: threadId }),
        }),

    streamChat: async (
        message: string,
        threadId: string = "default",
        onEvent: (event: StreamEvent) => void
    ) => {
        const res = await fetch(`${API_BASE}/chat/stream`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ message, thread_id: threadId }),
        });

        if (!res.ok) {
            const error = await res.json().catch(() => ({ detail: res.statusText }));
            throw new Error(error.detail || `API error: ${res.status}`);
        }

        const reader = res.body?.getReader();
        if (!reader) throw new Error("No response stream");

        const decoder = new TextDecoder();
        let buffer = "";

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split("\n");
            buffer = lines.pop() || "";

            for (const line of lines) {
                if (line.startsWith("data: ")) {
                    try {
                        const event: StreamEvent = JSON.parse(line.slice(6));
                        onEvent(event);
                    } catch {
                        // Malformed SSE chunk — skip
                    }
                }
            }
        }
    },

    reviewDiff: (staged: boolean = false, targetBranch?: string) =>
        apiFetch<ReviewResponse>("/review", {
            method: "POST",
            body: JSON.stringify({
                staged,
                target_branch: targetBranch || null,
            }),
        }),

    reviewFile: (filePath: string) =>
        apiFetch<ReviewFileResponse>("/review/file", {
            method: "POST",
            body: JSON.stringify({ file_path: filePath }),
        }),

    loadGuidelines: (docsPath?: string) =>
        apiFetch<{ status: string; chunks: number; path: string }>("/review/guidelines", {
            method: "POST",
            body: JSON.stringify({ docs_path: docsPath || null }),
        }),

    incrementalReindex: () =>
        apiFetch<IncrementalReindexResponse>("/incremental-reindex", {
            method: "POST",
        }),

    getArchitecture: () => apiFetch<ArchitectureResponse>("/architecture"),

    refreshAnalysis: () =>
        apiFetch<{ status: string; files: number; modules: string[] }>("/refresh", {
            method: "POST",
        }),

    applyFixes: (fixes: FixItem[]) =>
        apiFetch<ApplyFixesResponse>("/review/apply", {
            method: "POST",
            body: JSON.stringify({ fixes }),
        }),

    indexDocs: (docsPath: string) =>
        apiFetch<DocsIndexResponse>("/docs/index", {
            method: "POST",
            body: JSON.stringify({ docs_path: docsPath }),
        }),

    searchDocs: (query: string, nResults: number = 5) =>
        apiFetch<DocsSearchResponse>("/docs/search", {
            method: "POST",
            body: JSON.stringify({ query, n_results: nResults }),
        }),

    askDocs: (question: string, nResults: number = 5) =>
        apiFetch<DocsAskResponse>("/docs/ask", {
            method: "POST",
            body: JSON.stringify({ question, n_results: nResults }),
        }),

    research: (question: string, depth: string = "standard") =>
        apiFetch<ResearchResponse>("/research", {
            method: "POST",
            body: JSON.stringify({ question, depth }),
        }),

    chatApprove: (threadId: string, action: "approve" | "reject") =>
        apiFetch<{ status: string; message?: string; result?: string }>("/chat/approve", {
            method: "POST",
            body: JSON.stringify({ thread_id: threadId, action }),
        }),

    adminRepos: (adminKey: string) =>
        apiFetch<{ repos: AdminRepo[] }>("/admin/repos", {
            method: "POST",
            headers: { "X-Admin-Key": adminKey },
        }),

    registerRepo: (
        adminKey: string,
        name: string,
        githubUrl: string,
        branch: string = "main",
        installationId: string = ""
    ) =>
        apiFetch<{ repo_token: string; full_name: string; status: string }>("/admin/register", {
            method: "POST",
            headers: { "X-Admin-Key": adminKey },
            body: JSON.stringify({
                name,
                github_url: githubUrl,
                branch,
                installation_id: installationId,
            }),
        }),

    adminIndex: (adminKey: string, fullName: string, branch: string = "") =>
        apiFetch<{ repo: string; status: string; files_scanned?: number; total_chunks?: number }>("/admin/index", {
            method: "POST",
            headers: { "X-Admin-Key": adminKey },
            body: JSON.stringify({ full_name: fullName, branch }),
        }),

    voiceAsk: async (audioBlob: Blob, threadId: string = "voice") => {
        const form = new FormData();
        form.append("audio", audioBlob, "recording.webm");
        form.append("thread_id", threadId);

        const res = await fetch(`${API_BASE}/voice/ask`, {
            method: "POST",
            body: form,
        });

        if (!res.ok) {
            const error = await res.json().catch(() => ({ detail: res.statusText }));
            throw new Error(error.detail || `API error: ${res.status}`);
        }

        return res.json() as Promise<{
            question: string;
            answer: string;
            speech: string;
            audio_base64: string;
            tool?: string;
        }>;
    },
};
