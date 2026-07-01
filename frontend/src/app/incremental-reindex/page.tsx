"use client";

import { useState } from "react";
import { api, IncrementalReindexResponse } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Loader2, RefreshCw, CheckCircle2, AlertCircle } from "lucide-react";

export default function IncrementalReindexPage() {
    const [loading, setLoading] = useState(false);
    const [result, setResult] = useState<IncrementalReindexResponse | null>(null);
    const [error, setError] = useState("");

    async function handleReindex() {
        setLoading(true);
        setError("");
        setResult(null);
        try {
            const res = await api.incrementalReindex();
            setResult(res);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Reindex failed");
        } finally {
            setLoading(false);
        }
    }

    return (
        <div className="flex items-center justify-center min-h-full p-6">
            <div className="w-full max-w-md space-y-6">
                <div className="text-center space-y-2">
                    <h1 className="text-2xl font-bold text-kinetic-on-surface">Smart Reindex</h1>
                    <p className="text-kinetic-on-surface-variant text-sm">
                        Re-embed only files that changed since last indexing.
                        Compares content hashes — skips unchanged files.
                    </p>
                </div>

                <Card className="border-kinetic-border bg-kinetic-surface-container-low">
                    <CardHeader>
                        <CardTitle className="flex items-center gap-2 text-kinetic-on-surface">
                            <RefreshCw className="h-5 w-5 text-kinetic-primary" />
                            Incremental Reindex
                        </CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        <Button
                            onClick={handleReindex}
                            disabled={loading}
                            className="w-full bg-kinetic-primary text-kinetic-on-primary hover:bg-kinetic-primary/90"
                        >
                            {loading ? (
                                <>
                                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                    Reindexing...
                                </>
                            ) : (
                                <>
                                    <RefreshCw className="mr-2 h-4 w-4" />
                                    Run Incremental Reindex
                                </>
                            )}
                        </Button>

                        {error && (
                            <div className="p-3 bg-kinetic-error/10 text-kinetic-error rounded-md text-sm flex items-center gap-2 border border-kinetic-error/20">
                                <AlertCircle className="h-4 w-4 flex-shrink-0" />
                                {error}
                            </div>
                        )}

                        {result && (
                            <div className="p-4 rounded-md space-y-2 border border-kinetic-node-config/30 bg-kinetic-node-config/10">
                                <div className="flex items-center gap-2 text-kinetic-node-config font-medium">
                                    <CheckCircle2 className="h-4 w-4" />
                                    Reindex Complete ({result.total_time})
                                </div>
                                <div className="grid grid-cols-2 gap-2 text-sm text-kinetic-on-surface-variant">
                                    <span>Files on disk:</span>
                                    <span className="font-medium text-kinetic-on-surface">{result.files_on_disk}</span>
                                    <span>Skipped (unchanged):</span>
                                    <span className="font-medium text-kinetic-on-surface">{result.files_skipped}</span>
                                    <span>Re-indexed:</span>
                                    <span className="font-medium text-kinetic-primary">
                                        {result.files_reindexed}
                                    </span>
                                    <span>Deleted:</span>
                                    <span className="font-medium text-kinetic-error">
                                        {result.files_deleted}
                                    </span>
                                    <span>Chunks embedded:</span>
                                    <span className="font-medium text-kinetic-on-surface">{result.chunks_embedded}</span>
                                </div>
                            </div>
                        )}
                    </CardContent>
                </Card>
            </div>
        </div>
    );
}
