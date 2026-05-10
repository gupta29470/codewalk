"use client";

import { createContext, useContext, useState, useCallback, ReactNode } from "react";
import { AnalyzeResponse, OverviewResponse, ModuleResponse, BlastRadiusResponse, ReadingOrderResponse, ExecutionFlowResponse } from "@/lib/api";

interface PageCache {
    overview: OverviewResponse | null;
    modules: ModuleResponse[] | null;
    blastRadius: Record<string, BlastRadiusResponse>;
    readingOrder: ReadingOrderResponse | null;
    executionFlow: ExecutionFlowResponse | null;
}

interface AnalyzeState {
    loading: boolean;
    result: AnalyzeResponse | null;
    error: string;
    repoPath: string;
    indexMode: string;
    steps: string[];
    cache: PageCache;
    setLoading: (v: boolean) => void;
    setResult: (v: AnalyzeResponse | null) => void;
    setError: (v: string) => void;
    setRepoPath: (v: string) => void;
    setIndexMode: (v: string) => void;
    setSteps: (v: string[]) => void;
    addStep: (msg: string) => void;
    setCache: <K extends keyof PageCache>(key: K, value: PageCache[K]) => void;
    clearCache: () => void;
}

const emptyCache: PageCache = {
    overview: null,
    modules: null,
    blastRadius: {},
    readingOrder: null,
    executionFlow: null,
};

const AnalyzeContext = createContext<AnalyzeState | null>(null);

export function AnalyzeProvider({ children }: { children: ReactNode }) {
    const [loading, setLoading] = useState(false);
    const [result, setResult] = useState<AnalyzeResponse | null>(null);
    const [error, setError] = useState("");
    const [repoPath, setRepoPath] = useState("");
    const [indexMode, setIndexMode] = useState("auto");
    const [steps, setSteps] = useState<string[]>([]);
    const addStep = (msg: string) => setSteps((prev) => [...prev, msg]);
    const [cache, setCacheState] = useState<PageCache>({ ...emptyCache });

    const setCache = useCallback(<K extends keyof PageCache>(key: K, value: PageCache[K]) => {
        setCacheState((prev) => ({ ...prev, [key]: value }));
    }, []);

    const clearCache = useCallback(() => {
        setCacheState({ ...emptyCache });
    }, []);

    return (
        <AnalyzeContext.Provider
            value={{
                loading, result, error, repoPath, indexMode, steps, cache,
                setLoading, setResult, setError, setRepoPath, setIndexMode, setSteps, addStep,
                setCache, clearCache,
            }}
        >
            {children}
        </AnalyzeContext.Provider>
    );
}

export function useAnalyze() {
    const ctx = useContext(AnalyzeContext);
    if (!ctx) throw new Error("useAnalyze must be used within AnalyzeProvider");
    return ctx;
}
