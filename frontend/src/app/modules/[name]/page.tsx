"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { api, ModuleResponse } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { RiskBadge } from "@/components/RiskBadge";
import { Separator } from "@/components/ui/separator";
import { Loader2 } from "lucide-react";

export default function ModuleDetailPage() {
    const params = useParams();
    const moduleName = decodeURIComponent(params.name as string);
    const [data, setData] = useState<ModuleResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");

    useEffect(() => {
        api
            .getModule(moduleName)
            .then(setData)
            .catch((err) => setError(err.message))
            .finally(() => setLoading(false));
    }, [moduleName]);

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
            <div className="flex items-center gap-3">
                <h1 className="text-2xl font-bold text-kinetic-on-surface">Module: {data.name}</h1>
                <RiskBadge level={data.module_risk} />
            </div>

            {/* Module info */}
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
                    <CardTitle className="text-kinetic-on-surface">Files &amp; Blast Radius</CardTitle>
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
        </div>
    );
}
