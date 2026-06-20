"use client";

import { createContext, useContext, useState, useEffect, useCallback, ReactNode } from "react";
import { AnalyzeResponse, OverviewResponse, ModuleResponse, BlastRadiusResponse, ReadingOrderResponse, ExecutionFlowResponse } from "@/lib/api";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

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
    indexMode: string;
    steps: string[];
    hasIndex: boolean;
    cache: PageCache;
    setLoading: (v: boolean) => void;
    setResult: (v: AnalyzeResponse | null) => void;
    setError: (v: string) => void;
    setIndexMode: (v: string) => void;
    setSteps: (v: string[]) => void;
    setHasIndex: (v: boolean) => void;
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
    const [indexMode, setIndexMode] = useState("auto");
    const [steps, setSteps] = useState<string[]>([]);
    const [hasIndex, setHasIndex] = useState(false);
    const addStep = (msg: string) => setSteps((prev) => [...prev, msg]);
    const [cache, setCacheState] = useState<PageCache>({ ...emptyCache });

    const setCache = useCallback(<K extends keyof PageCache>(key: K, value: PageCache[K]) => {
        setCacheState((prev) => ({ ...prev, [key]: value }));
    }, []);

    const clearCache = useCallback(() => {
        setCacheState({ ...emptyCache });
    }, []);

    useEffect(() => {
        let cancelled = false;
        fetch(`${API_BASE}/index-status`)
            .then((res) => (res.ok ? res.json() : { indexed: false }))
            .then((data) => {
                if (!cancelled) setHasIndex(Boolean(data.indexed));
            })
            .catch(() => {
                if (!cancelled) setHasIndex(false);
            });
        return () => {
            cancelled = true;
        };
    }, []);

    return (
        <AnalyzeContext.Provider
value={{
                loading, result, error, indexMode, steps, hasIndex, cache,
                setLoading, setResult, setError, setIndexMode, setSteps, setHasIndex, addStep,
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
