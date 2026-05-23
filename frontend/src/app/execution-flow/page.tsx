"use client";

import { useEffect, useState } from "react";
import { api, ExecutionFlowResponse } from "@/lib/api";
import { useAnalyze } from "@/lib/analyze-context";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { MermaidDiagram } from "@/components/MermaidDiagram";
import { Loader2, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";

export default function ExecutionFlowPage() {
    const { cache, setCache } = useAnalyze();
    const [flow, setFlow] = useState<ExecutionFlowResponse | null>(cache.executionFlow);
    const [loading, setLoading] = useState(!cache.executionFlow);
    const [error, setError] = useState("");

    function fetchData() {
        setLoading(true);
        setError("");
        api
            .getExecutionFlow()
            .then((res) => {
                setFlow(res);
                setCache("executionFlow", res);
            })
            .catch((err) => setError(err.message))
            .finally(() => setLoading(false));
    }

    useEffect(() => {
        if (cache.executionFlow) return;
        fetchData();
    }, []);

    if (loading) {
        return (
            <div className="flex items-center justify-center min-h-[60vh]">
                <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
            </div>
        );
    }

    if (error) {
        return (
            <div className="p-4 bg-destructive/10 text-destructive rounded-md">
                {error}
            </div>
        );
    }

    // Extract mermaid block and narration from LLM response
    const mermaidMatch = flow?.flow?.match(/```mermaid\s*\n([\s\S]*?)\n```/);
    const mermaidChart = mermaidMatch ? mermaidMatch[1] : null;
    const narration = flow?.flow?.replace(/```mermaid[\s\S]*?```/, "").trim() || "";

    return (
        <div className="space-y-6">
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-2xl font-bold">Execution Flow</h1>
                    <p className="text-muted-foreground mt-1">
                        How the code runs — entry points to outputs
                    </p>
                </div>
                <Button variant="outline" size="sm" onClick={fetchData} disabled={loading}>
                    <RefreshCw className={`h-4 w-4 mr-1 ${loading ? "animate-spin" : ""}`} />
                    Refresh
                </Button>
            </div>

            {mermaidChart && (
                <Card>
                    <CardHeader>
                        <CardTitle>Execution Flow Diagram</CardTitle>
                    </CardHeader>
                    <CardContent>
                        <MermaidDiagram chart={mermaidChart} />
                    </CardContent>
                </Card>
            )}

            {narration && (
                <Card>
                    <CardHeader>
                        <CardTitle>How This Code Runs</CardTitle>
                    </CardHeader>
                    <CardContent>
                        <div className="prose prose-sm max-w-none whitespace-pre-wrap">{narration}</div>
                    </CardContent>
                </Card>
            )}
        </div>
    );
}
