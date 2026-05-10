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

export interface ProgressEvent {
    step: string;
    message: string;
    result?: AnalyzeResponse;
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
        throw new Error(error.detail || `API error: ${res.status}`);
    }

    return res.json();
}

export const api = {
    health: () => apiFetch<{ status: string }>("/health"),

    analyze: (repoPath: string, indexMode: string = "auto") =>
        apiFetch<AnalyzeResponse>("/analyze", {
            method: "POST",
            body: JSON.stringify({
                repo_path: repoPath,
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
        repoPath: string,
        indexMode: string,
        onProgress: (event: ProgressEvent) => void,
        collectionName?: string
    ) => {
        const res = await fetch(`${API_BASE}/analyze/stream`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                repo_path: repoPath,
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
};
