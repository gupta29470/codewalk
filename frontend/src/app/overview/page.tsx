"use client";

import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import { api, OverviewResponse } from "@/lib/api";
import { useAnalyze } from "@/lib/analyze-context";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { RiskBadge } from "@/components/RiskBadge";
import { MermaidDiagram } from "@/components/MermaidDiagram";
import { Loader2, FileCode, Package, Layers, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";

export default function OverviewPage() {
    const { cache, setCache } = useAnalyze();
    const [data, setData] = useState<OverviewResponse | null>(cache.overview);
    const [loading, setLoading] = useState(!cache.overview);
    const [error, setError] = useState("");

    function fetchData() {
        setLoading(true);
        setError("");
        api
            .getOverview()
            .then((res) => {
                setData(res);
                setCache("overview", res);
            })
            .catch((err) => setError(err.message))
            .finally(() => setLoading(false));
    }

    useEffect(() => {
        if (cache.overview) return;
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

    if (!data) return null;

    return (
        <div className="space-y-6">
            <div className="flex items-center justify-between">
                <h1 className="text-2xl font-bold">Project Overview</h1>
                <Button variant="outline" size="sm" onClick={fetchData} disabled={loading}>
                    <RefreshCw className={`h-4 w-4 mr-1 ${loading ? "animate-spin" : ""}`} />
                    Refresh
                </Button>
            </div>

            {/* Stats row */}
            <div className="grid grid-cols-3 gap-4">
                <Card>
                    <CardContent className="pt-6">
                        <div className="flex items-center gap-3">
                            <FileCode className="h-8 w-8 text-muted-foreground" />
                            <div>
                                <p className="text-2xl font-bold">{data.total_files}</p>
                                <p className="text-sm text-muted-foreground">Files</p>
                            </div>
                        </div>
                    </CardContent>
                </Card>
                <Card>
                    <CardContent className="pt-6">
                        <div className="flex items-center gap-3">
                            <Package className="h-8 w-8 text-muted-foreground" />
                            <div>
                                <p className="text-2xl font-bold">{data.total_modules}</p>
                                <p className="text-sm text-muted-foreground">Modules</p>
                            </div>
                        </div>
                    </CardContent>
                </Card>
                <Card>
                    <CardContent className="pt-6">
                        <div className="flex items-center gap-3">
                            <Layers className="h-8 w-8 text-muted-foreground" />
                            <div>
                                <p className="text-2xl font-bold">{data.tech_stack.length}</p>
                                <p className="text-sm text-muted-foreground">Technologies</p>
                            </div>
                        </div>
                    </CardContent>
                </Card>
            </div>

            {/* Tech stack */}
            <Card>
                <CardHeader>
                    <CardTitle>Tech Stack</CardTitle>
                </CardHeader>
                <CardContent className="flex flex-wrap gap-2">
                    {data.tech_stack.map((tech) => (
                        <Badge key={tech} variant="secondary">
                            {tech}
                        </Badge>
                    ))}
                </CardContent>
            </Card>

            {/* Architecture Diagram */}
            {data.diagram && (
                <Card>
                    <CardHeader>
                        <CardTitle>Architecture Diagram</CardTitle>
                    </CardHeader>
                    <CardContent>
                        <MermaidDiagram chart={data.diagram} />
                    </CardContent>
                </Card>
            )}

            {/* AI Overview */}
            <Card>
                <CardHeader>
                    <CardTitle>AI Overview</CardTitle>
                </CardHeader>
                <CardContent className="prose prose-sm max-w-none dark:prose-invert">
                    <ReactMarkdown>{data.overview_text}</ReactMarkdown>
                </CardContent>
            </Card>

            {/* Riskiest Files */}
            {data.riskiest_files.length > 0 && (
                <Card>
                    <CardHeader>
                        <CardTitle>Riskiest Files</CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-3">
                        {data.riskiest_files.map((file) => (
                            <div
                                key={file.file}
                                className="flex items-center justify-between p-3 border rounded-md"
                            >
                                <div>
                                    <div className="flex items-center gap-2">
                                        <RiskBadge level={file.risk_level} />
                                        <span className="font-mono text-sm">{file.file}</span>
                                    </div>
                                    {file.direct.length > 0 && (
                                        <p className="text-xs text-muted-foreground mt-1">
                                            breaks: {file.direct.join(", ")}
                                        </p>
                                    )}
                                </div>
                                <span className="text-sm text-muted-foreground">
                                    {file.affected_files} affected
                                </span>
                            </div>
                        ))}
                    </CardContent>
                </Card>
            )}
        </div>
    );
}
