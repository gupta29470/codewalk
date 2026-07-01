"use client";

import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import { api, OverviewResponse } from "@/lib/api";
import { useAnalyze } from "@/lib/analyze-context";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { RiskBadge } from "@/components/RiskBadge";
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
            <div className="flex items-center justify-center min-h-full p-6">
                <Loader2 className="h-8 w-8 animate-spin text-kinetic-primary" />
            </div>
        );
    }

    if (error) {
        return (
            <div className="p-6">
                <div className="p-4 bg-kinetic-error/10 text-kinetic-error rounded-md border border-kinetic-error/20">
                    {error}
                </div>
            </div>
        );
    }

    if (!data) return null;

    return (
        <div className="p-6 space-y-6 max-w-6xl">
            <div className="flex items-center justify-between">
                <h1 className="text-2xl font-bold text-kinetic-on-surface">Project Overview</h1>
                <Button
                    variant="outline"
                    size="sm"
                    onClick={fetchData}
                    disabled={loading}
                    className="border-kinetic-border bg-kinetic-surface-container text-kinetic-on-surface hover:bg-kinetic-surface-container-high hover:text-kinetic-on-surface"
                >
                    <RefreshCw className={`h-4 w-4 mr-1 ${loading ? "animate-spin" : ""}`} />
                    Refresh
                </Button>
            </div>

            {/* Stats row */}
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                <Card className="border-kinetic-border bg-kinetic-surface-container-low">
                    <CardContent className="pt-6">
                        <div className="flex items-center gap-3">
                            <FileCode className="h-8 w-8 text-kinetic-primary" />
                            <div>
                                <p className="text-2xl font-bold text-kinetic-on-surface">{data.total_files}</p>
                                <p className="text-sm text-kinetic-on-surface-variant">Files</p>
                            </div>
                        </div>
                    </CardContent>
                </Card>
                <Card className="border-kinetic-border bg-kinetic-surface-container-low">
                    <CardContent className="pt-6">
                        <div className="flex items-center gap-3">
                            <Package className="h-8 w-8 text-kinetic-secondary" />
                            <div>
                                <p className="text-2xl font-bold text-kinetic-on-surface">{data.total_modules}</p>
                                <p className="text-sm text-kinetic-on-surface-variant">Modules</p>
                            </div>
                        </div>
                    </CardContent>
                </Card>
                <Card className="border-kinetic-border bg-kinetic-surface-container-low">
                    <CardContent className="pt-6">
                        <div className="flex items-center gap-3">
                            <Layers className="h-8 w-8 text-kinetic-tertiary" />
                            <div>
                                <p className="text-2xl font-bold text-kinetic-on-surface">{data.tech_stack.length}</p>
                                <p className="text-sm text-kinetic-on-surface-variant">Technologies</p>
                            </div>
                        </div>
                    </CardContent>
                </Card>
            </div>

            {/* Tech stack */}
            <Card className="border-kinetic-border bg-kinetic-surface-container-low">
                <CardHeader>
                    <CardTitle className="text-kinetic-on-surface">Tech Stack</CardTitle>
                </CardHeader>
                <CardContent className="flex flex-wrap gap-2">
                    {data.tech_stack.map((tech) => (
                        <Badge
                            key={tech}
                            variant="secondary"
                            className="bg-kinetic-primary/15 text-kinetic-primary border border-kinetic-primary/30 hover:bg-kinetic-primary/25"
                        >
                            {tech}
                        </Badge>
                    ))}
                </CardContent>
            </Card>

            {/* AI Overview */}
            <Card className="border-kinetic-border bg-kinetic-surface-container-low">
                <CardHeader>
                    <CardTitle className="text-kinetic-on-surface">AI Overview</CardTitle>
                </CardHeader>
                <CardContent className="prose prose-sm max-w-none dark:prose-invert overview-markdown">
                    <ReactMarkdown>{data.overview_text}</ReactMarkdown>
                </CardContent>
            </Card>

            {/* Riskiest Files */}
            {data.riskiest_files.length > 0 && (
                <Card className="border-kinetic-border bg-kinetic-surface-container-low">
                    <CardHeader>
                        <CardTitle className="text-kinetic-on-surface">Riskiest Files</CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-3">
                        {data.riskiest_files.map((file) => (
                            <div
                                key={file.file}
                                className="flex items-center justify-between p-3 border border-kinetic-border rounded-md bg-kinetic-surface-container"
                            >
                                <div>
                                    <div className="flex items-center gap-2">
                                        <RiskBadge level={file.risk_level} />
                                        <span className="font-mono text-sm text-kinetic-on-surface">{file.file}</span>
                                    </div>
                                    {file.direct.length > 0 && (
                                        <p className="text-xs text-kinetic-on-surface-variant mt-1">
                                            breaks: {file.direct.join(", ")}
                                        </p>
                                    )}
                                </div>
                                <span className="text-sm text-kinetic-on-surface-variant">
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
