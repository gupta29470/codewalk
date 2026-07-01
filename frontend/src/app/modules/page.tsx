"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api, ModuleResponse } from "@/lib/api";
import { useAnalyze } from "@/lib/analyze-context";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { RiskBadge } from "@/components/RiskBadge";
import { Loader2, ArrowRight, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";

export default function ModulesPage() {
    const { cache, setCache } = useAnalyze();
    const [modules, setModules] = useState<ModuleResponse[]>(cache.modules || []);
    const [loading, setLoading] = useState(!cache.modules);
    const [error, setError] = useState("");

    function fetchData() {
        setLoading(true);
        setError("");
        api
            .getModules()
            .then(async (list) => {
                const details = await Promise.all(
                    list.modules.map((name) => api.getModule(name))
                );
                setModules(details);
                setCache("modules", details);
            })
            .catch((err) => setError(err.message))
            .finally(() => setLoading(false));
    }

    useEffect(() => {
        if (cache.modules) return;
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

    return (
        <div className="p-6 space-y-6 max-w-6xl">
            <div className="flex items-center justify-between">
                <h1 className="text-2xl font-bold text-kinetic-on-surface">Modules</h1>
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

            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                {modules.map((mod) => (
                    <Link key={mod.name} href={`/modules/${encodeURIComponent(mod.name)}`}>
                        <Card className="border-kinetic-border bg-kinetic-surface-container-low hover:border-kinetic-primary/50 transition-colors cursor-pointer h-full">
                            <CardHeader className="pb-2">
                                <CardTitle className="text-lg flex items-center justify-between text-kinetic-on-surface">
                                    {mod.name}
                                    <ArrowRight className="h-4 w-4 text-kinetic-on-surface-variant" />
                                </CardTitle>
                            </CardHeader>
                            <CardContent className="space-y-2">
                                <div className="flex items-center justify-between text-sm">
                                    <span className="text-kinetic-on-surface-variant">
                                        {mod.file_count} files
                                    </span>
                                    <RiskBadge level={mod.module_risk} />
                                </div>
                                <div className="flex flex-wrap gap-1">
                                    {Object.keys(mod.languages).map((lang) => (
                                        <span
                                            key={lang}
                                            className="text-xs px-2 py-0.5 rounded bg-kinetic-surface-container-high text-kinetic-on-surface-variant border border-kinetic-border"
                                        >
                                            {lang}
                                        </span>
                                    ))}
                                </div>
                                {mod.depends_on.length > 0 && (
                                    <p className="text-xs text-kinetic-on-surface-variant">
                                        Depends on: {mod.depends_on.join(", ")}
                                    </p>
                                )}
                            </CardContent>
                        </Card>
                    </Link>
                ))}
            </div>
        </div>
    );
}
