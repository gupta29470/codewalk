"use client";

import { useEffect, useState } from "react";
import { api, ModuleResponse } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { RiskBadge } from "@/components/RiskBadge";
import { Separator } from "@/components/ui/separator";
import { Loader2, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";

export default function ModuleDetailPage() {
    const [modules, setModules] = useState<string[]>([]);
    const [modulesLoading, setModulesLoading] = useState(true);
    const [selectedModule, setSelectedModule] = useState("");
    const [data, setData] = useState<ModuleResponse | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState("");

    useEffect(() => {
        setModulesLoading(true);
        api.getModules()
            .then((res) => setModules(res.modules))
            .catch((err) => setError(err.message))
            .finally(() => setModulesLoading(false));
    }, []);

    function fetchModule(name: string) {
        if (!name) return;
        setLoading(true);
        setError("");
        api
            .getModule(name)
            .then(setData)
            .catch((err) => setError(err.message))
            .finally(() => setLoading(false));
    }

    useEffect(() => {
        if (selectedModule) fetchModule(selectedModule);
    }, [selectedModule]);

    return (
        <div className="p-6 space-y-6 max-w-6xl">
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-2xl font-bold text-kinetic-on-surface">Module Detail</h1>
                    <p className="text-kinetic-on-surface-variant mt-1">
                        Deep dive into a single module
                    </p>
                </div>
                {data && (
                    <Button
                        variant="outline"
                        size="sm"
                        onClick={() => fetchModule(selectedModule)}
                        disabled={loading}
                        className="border-kinetic-border bg-kinetic-surface-container text-kinetic-on-surface hover:bg-kinetic-surface-container-high hover:text-kinetic-on-surface"
                    >
                        <RefreshCw className={`h-4 w-4 mr-1 ${loading ? "animate-spin" : ""}`} />
                        Refresh
                    </Button>
                )}
            </div>

            {/* Module Selector */}
            <div>
                <label className="text-sm font-medium text-kinetic-on-surface-variant">
                    Select Module
                </label>
                <select
                    className="mt-1 block w-full rounded-md border border-kinetic-border bg-kinetic-surface-container px-3 py-2 text-sm text-kinetic-on-surface outline-none focus-visible:ring-1 focus-visible:ring-kinetic-primary disabled:opacity-50"
                    value={selectedModule}
                    onChange={(e) => setSelectedModule(e.target.value)}
                    disabled={modulesLoading}
                >
                    <option value="">
                        {modulesLoading ? "Loading modules..." : "— Choose a module —"}
                    </option>
                    {modules.map((name) => (
                        <option key={name} value={name}>
                            {name}
                        </option>
                    ))}
                </select>
            </div>

            {loading && (
                <div className="flex items-center justify-center min-h-[40vh]">
                    <Loader2 className="h-8 w-8 animate-spin text-kinetic-primary" />
                </div>
            )}

            {error && (
                <div className="p-4 bg-kinetic-error/10 text-kinetic-error rounded-md border border-kinetic-error/20">
                    {error}
                </div>
            )}

            {data && !loading && (
                <>
                    {/* Module info cards */}
                    <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                        <Card className="border-kinetic-border bg-kinetic-surface-container-low">
                            <CardHeader className="pb-2">
                                <CardTitle className="text-sm text-kinetic-on-surface-variant">Files</CardTitle>
                            </CardHeader>
                            <CardContent>
                                <p className="text-2xl font-bold text-kinetic-on-surface">{data.file_count}</p>
                            </CardContent>
                        </Card>
                        <Card className="border-kinetic-border bg-kinetic-surface-container-low">
                            <CardHeader className="pb-2">
                                <CardTitle className="text-sm text-kinetic-on-surface-variant">
                                    Depends On
                                </CardTitle>
                            </CardHeader>
                            <CardContent className="flex flex-wrap gap-1">
                                {data.depends_on.length > 0 ? (
                                    data.depends_on.map((dep) => (
                                        <Badge key={dep} variant="outline" className="border-kinetic-border text-kinetic-on-surface-variant">
                                            {dep}
                                        </Badge>
                                    ))
                                ) : (
                                    <span className="text-sm text-kinetic-on-surface-variant">None</span>
                                )}
                            </CardContent>
                        </Card>
                        <Card className="border-kinetic-border bg-kinetic-surface-container-low">
                            <CardHeader className="pb-2">
                                <CardTitle className="text-sm text-kinetic-on-surface-variant">
                                    Depended By
                                </CardTitle>
                            </CardHeader>
                            <CardContent className="flex flex-wrap gap-1">
                                {data.depended_by.length > 0 ? (
                                    data.depended_by.map((dep) => (
                                        <Badge key={dep} variant="outline" className="border-kinetic-border text-kinetic-on-surface-variant">
                                            {dep}
                                        </Badge>
                                    ))
                                ) : (
                                    <span className="text-sm text-kinetic-on-surface-variant">None</span>
                                )}
                            </CardContent>
                        </Card>
                    </div>

                    {/* Files & Blast Radius */}
                    <Card className="border-kinetic-border bg-kinetic-surface-container-low">
                        <CardHeader>
                            <CardTitle className="flex items-center gap-2 text-kinetic-on-surface">
                                Files &amp; Blast Radius
                                <RiskBadge level={data.module_risk} />
                            </CardTitle>
                        </CardHeader>
                        <CardContent className="space-y-1">
                            {data.blast_radius.length > 0
                                ? data.blast_radius.map((file, idx) => (
                                    <div key={file.file}>
                                        <div className="flex items-center justify-between py-3">
                                            <div className="space-y-1">
                                                <div className="flex items-center gap-2">
                                                    <RiskBadge level={file.risk_level} />
                                                    <span className="font-mono text-sm text-kinetic-on-surface">{file.file}</span>
                                                </div>
                                                {file.direct.length > 0 && (
                                                    <p className="text-xs text-kinetic-on-surface-variant ml-1">
                                                        breaks: {file.direct.join(", ")}
                                                    </p>
                                                )}
                                                {file.transitive.length > 0 && (
                                                    <p className="text-xs text-kinetic-on-surface-variant ml-1">
                                                        → then: {file.transitive.join(", ")}
                                                    </p>
                                                )}
                                            </div>
                                            <span className="text-sm text-kinetic-on-surface-variant whitespace-nowrap">
                                                {file.affected_files} affected
                                            </span>
                                        </div>
                                        {idx < data.blast_radius.length - 1 && <Separator className="bg-kinetic-border" />}
                                    </div>
                                ))
                                : data.files.map((file) => (
                                    <div key={file} className="py-2">
                                        <span className="font-mono text-sm text-kinetic-on-surface">{file}</span>
                                    </div>
                                ))}
                        </CardContent>
                    </Card>
                </>
            )}
        </div>
    );
}
