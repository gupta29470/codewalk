"use client";

import { useEffect, useState } from "react";
import { api, BlastRadiusResponse } from "@/lib/api";
import { useAnalyze } from "@/lib/analyze-context";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { RiskBadge } from "@/components/RiskBadge";
import { Separator } from "@/components/ui/separator";
import { Loader2, ChevronDown, ChevronRight, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";

const RISK_ORDER = ["critical", "high", "moderate", "low", "none"];

export default function BlastRadiusPage() {
    const { cache, setCache } = useAnalyze();
    const [modules, setModules] = useState<string[]>([]);
    const [selectedModule, setSelectedModule] = useState("");
    const [error, setError] = useState("");
    const [expanded, setExpanded] = useState<Set<string>>(new Set());

    const cacheKey = selectedModule || "__all__";
    const [data, setData] = useState<BlastRadiusResponse | null>(cache.blastRadius[cacheKey] || null);
    const [loading, setLoading] = useState(!cache.blastRadius[cacheKey]);

    function fetchData() {
        const key = selectedModule || "__all__";
        setLoading(true);
        setError("");
        const fetcher = selectedModule
            ? api.getBlastRadius(selectedModule)
            : api.getBlastRadius();
        fetcher
            .then((res) => {
                setData(res);
                setCache("blastRadius", { ...cache.blastRadius, [key]: res });
            })
            .catch((err) => setError(err.message))
            .finally(() => setLoading(false));
    }

    useEffect(() => {
        api.getModules().then((res) => setModules(res.modules)).catch(() => { });
    }, []);

    useEffect(() => {
        const key = selectedModule || "__all__";
        if (cache.blastRadius[key]) {
            setData(cache.blastRadius[key]);
            setLoading(false);
            return;
        }
        fetchData();
    }, [selectedModule]);

    function toggleExpand(file: string) {
        setExpanded((prev) => {
            const next = new Set(prev);
            if (next.has(file)) next.delete(file);
            else next.add(file);
            return next;
        });
    }

    // Group files by risk level
    const grouped = RISK_ORDER.reduce(
        (acc, level) => {
            const files = data?.files.filter(
                (f) => f.risk_level.toLowerCase() === level
            );
            if (files && files.length > 0) acc[level] = files;
            return acc;
        },
        {} as Record<string, typeof data extends null ? never : NonNullable<typeof data>["files"]>
    );

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

    return (
        <div className="p-6 space-y-6 max-w-6xl">
            <div className="flex items-center justify-between">
                <h1 className="text-2xl font-bold text-kinetic-on-surface">Blast Radius Explorer</h1>
                <div className="flex items-center gap-2">
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
                    <select
                        value={selectedModule}
                        onChange={(e) => setSelectedModule(e.target.value)}
                        className="border border-kinetic-border rounded-md px-3 py-1.5 text-sm bg-kinetic-surface-container text-kinetic-on-surface outline-none focus-visible:ring-1 focus-visible:ring-kinetic-primary"
                    >
                        <option value="">All modules</option>
                        {modules.map((m) => (
                            <option key={m} value={m}>
                                {m}
                            </option>
                        ))}
                    </select>
                </div>
            </div>

            {Object.entries(grouped).map(([level, files]) => (
                <Card key={level} className="border-kinetic-border bg-kinetic-surface-container-low">
                    <CardHeader className="pb-2">
                        <CardTitle className="flex items-center gap-2 text-base text-kinetic-on-surface">
                            <RiskBadge level={level} />
                            <span>{files.length} files</span>
                        </CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-0">
                        {files.map((file, idx) => (
                            <div key={file.file}>
                                <button
                                    onClick={() => toggleExpand(file.file)}
                                    className="w-full flex items-center justify-between py-2.5 text-left hover:bg-kinetic-surface-container rounded-md px-2 -mx-2 transition-colors"
                                >
                                    <div className="flex items-center gap-2">
                                        {expanded.has(file.file) ? (
                                            <ChevronDown className="h-4 w-4 text-kinetic-on-surface-variant" />
                                        ) : (
                                            <ChevronRight className="h-4 w-4 text-kinetic-on-surface-variant" />
                                        )}
                                        <span className="font-mono text-sm text-kinetic-on-surface">{file.file}</span>
                                    </div>
                                    <span className="text-sm text-kinetic-on-surface-variant">
                                        {file.affected_files} affected
                                    </span>
                                </button>

                                {expanded.has(file.file) && (
                                    <div className="ml-8 pb-2 space-y-1">
                                        {file.direct.length > 0 && (
                                            <p className="text-xs text-kinetic-on-surface-variant">
                                                <span className="font-medium text-kinetic-on-surface">Direct:</span>{" "}
                                                {file.direct.join(", ")}
                                            </p>
                                        )}
                                        {file.transitive.length > 0 && (
                                            <p className="text-xs text-kinetic-on-surface-variant">
                                                <span className="font-medium text-kinetic-on-surface">Transitive:</span>{" "}
                                                {file.transitive.join(", ")}
                                            </p>
                                        )}
                                        {file.direct.length === 0 && file.transitive.length === 0 && (
                                            <p className="text-xs text-kinetic-on-surface-variant">
                                                No dependents — safe to change
                                            </p>
                                        )}
                                    </div>
                                )}

                                {idx < files.length - 1 && <Separator className="bg-kinetic-border" />}
                            </div>
                        ))}
                    </CardContent>
                </Card>
            ))}
        </div>
    );
}
