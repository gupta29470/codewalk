"use client";

import { useEffect, useState } from "react";
import { api, ArchitectureResponse } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Loader2, RefreshCw, Network, AlertTriangle, GitBranch, FileBarChart } from "lucide-react";

export default function ArchitecturePage() {
    const [data, setData] = useState<ArchitectureResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");

    function fetchData() {
        setLoading(true);
        setError("");
        api.getArchitecture()
            .then((res) => setData(res))
            .catch((err) => setError(err.message))
            .finally(() => setLoading(false));
    }

    useEffect(() => {
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

    const { stats, centrality, cycles } = data;

    return (
        <div className="p-6 space-y-6 max-w-6xl">
            <div className="flex items-center justify-between">
                <h1 className="text-2xl font-bold text-kinetic-on-surface">Architecture Health</h1>
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
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                <Card className="border-kinetic-border bg-kinetic-surface-container-low">
                    <CardContent className="pt-6">
                        <div className="flex items-center gap-3">
                            <FileBarChart className="h-8 w-8 text-kinetic-primary" />
                            <div>
                                <p className="text-2xl font-bold text-kinetic-on-surface">{stats.file_graph.vertices}</p>
                                <p className="text-sm text-kinetic-on-surface-variant">Files</p>
                            </div>
                        </div>
                    </CardContent>
                </Card>
                <Card className="border-kinetic-border bg-kinetic-surface-container-low">
                    <CardContent className="pt-6">
                        <div className="flex items-center gap-3">
                            <GitBranch className="h-8 w-8 text-kinetic-secondary" />
                            <div>
                                <p className="text-2xl font-bold text-kinetic-on-surface">{stats.file_graph.edges}</p>
                                <p className="text-sm text-kinetic-on-surface-variant">Import Edges</p>
                            </div>
                        </div>
                    </CardContent>
                </Card>
                <Card className="border-kinetic-border bg-kinetic-surface-container-low">
                    <CardContent className="pt-6">
                        <div className="flex items-center gap-3">
                            <Network className="h-8 w-8 text-kinetic-tertiary" />
                            <div>
                                <p className="text-2xl font-bold text-kinetic-on-surface">{stats.module_graph.vertices}</p>
                                <p className="text-sm text-kinetic-on-surface-variant">Modules</p>
                            </div>
                        </div>
                    </CardContent>
                </Card>
                <Card className="border-kinetic-border bg-kinetic-surface-container-low">
                    <CardContent className="pt-6">
                        <div className="flex items-center gap-3">
                            <AlertTriangle className={`h-8 w-8 ${stats.file_graph.is_dag ? "text-kinetic-node-config" : "text-kinetic-error"}`} />
                            <div>
                                <p className="text-2xl font-bold text-kinetic-on-surface">{stats.file_graph.is_dag ? "Clean" : "Cycles"}</p>
                                <p className="text-sm text-kinetic-on-surface-variant">DAG Status</p>
                            </div>
                        </div>
                    </CardContent>
                </Card>
            </div>

            {/* Bottleneck Files */}
            <Card className="border-kinetic-border bg-kinetic-surface-container-low">
                <CardHeader>
                    <CardTitle className="text-kinetic-on-surface">Bottleneck Files (Betweenness Centrality)</CardTitle>
                    <p className="text-sm text-kinetic-on-surface-variant">
                        These files sit on the most import paths. Changes here ripple widely.
                    </p>
                </CardHeader>
                <CardContent className="space-y-2">
                    {centrality.betweenness.filter((b) => b.score > 0).length === 0 ? (
                        <p className="text-kinetic-on-surface-variant text-sm">No significant bottlenecks found.</p>
                    ) : (
                        centrality.betweenness
                            .filter((b) => b.score > 0)
                            .map((item) => (
                                <div
                                    key={item.file}
                                    className="flex items-center justify-between p-3 border border-kinetic-border rounded-md bg-kinetic-surface-container"
                                >
                                    <span className="font-mono text-sm text-kinetic-on-surface">{item.file}</span>
                                    <Badge variant="secondary" className="bg-kinetic-surface-container-high text-kinetic-on-surface-variant border border-kinetic-border">
                                        {item.score.toFixed(4)}
                                    </Badge>
                                </div>
                            ))
                    )}
                </CardContent>
            </Card>

            {/* Most Important Files */}
            <Card className="border-kinetic-border bg-kinetic-surface-container-low">
                <CardHeader>
                    <CardTitle className="text-kinetic-on-surface">Most Important Files (PageRank)</CardTitle>
                    <p className="text-sm text-kinetic-on-surface-variant">
                        Transitively depended on by the most code.
                    </p>
                </CardHeader>
                <CardContent className="space-y-2">
                    {centrality.pagerank.length === 0 ? (
                        <p className="text-kinetic-on-surface-variant text-sm">No PageRank data available.</p>
                    ) : (
                        centrality.pagerank.map((item) => (
                            <div
                                key={item.file}
                                className="flex items-center justify-between p-3 border border-kinetic-border rounded-md bg-kinetic-surface-container"
                            >
                                <span className="font-mono text-sm text-kinetic-on-surface">{item.file}</span>
                                <Badge variant="secondary" className="bg-kinetic-surface-container-high text-kinetic-on-surface-variant border border-kinetic-border">
                                    {item.score.toFixed(4)}
                                </Badge>
                            </div>
                        ))
                    )}
                </CardContent>
            </Card>

            {/* Circular Dependencies */}
            <Card className="border-kinetic-border bg-kinetic-surface-container-low">
                <CardHeader>
                    <CardTitle className="text-kinetic-on-surface">Circular Dependencies</CardTitle>
                </CardHeader>
                <CardContent>
                    {!cycles.has_cycles ? (
                        <div className="flex items-center gap-2 text-kinetic-node-config">
                            <Badge variant="outline" className="border-kinetic-node-config text-kinetic-node-config">Clean DAG</Badge>
                            <span className="text-sm">No circular dependencies found.</span>
                        </div>
                    ) : (
                        <div className="space-y-4">
                            <p className="text-sm text-kinetic-on-surface-variant">
                                {cycles.cycle_groups.length} cycle group(s) found:
                            </p>
                            {cycles.cycle_groups.map((group, i) => (
                                <div key={i} className="p-3 border border-kinetic-border rounded-md bg-kinetic-surface-container">
                                    <p className="text-sm font-medium mb-2 text-kinetic-on-surface">Cycle {i + 1}</p>
                                    <div className="flex flex-wrap gap-1">
                                        {group.map((file) => (
                                            <Badge key={file} variant="outline" className="font-mono text-xs border-kinetic-border text-kinetic-on-surface-variant">
                                                {file.split("/").pop()}
                                            </Badge>
                                        ))}
                                    </div>
                                </div>
                            ))}
                            {cycles.edges_to_break.length > 0 && (
                                <div className="p-3 rounded-md border border-kinetic-border bg-kinetic-surface-container">
                                    <p className="text-sm font-medium mb-2 text-kinetic-on-surface">Suggested Fixes</p>
                                    <p className="text-xs text-kinetic-on-surface-variant mb-2">
                                        Remove these imports to break the cycles:
                                    </p>
                                    {cycles.edges_to_break.map(([source, target], i) => (
                                        <div key={i} className="text-sm font-mono text-kinetic-on-surface">
                                            <span className="text-kinetic-error">-</span> {source.split("/").pop()} → {target.split("/").pop()}
                                        </div>
                                    ))}
                                </div>
                            )}
                        </div>
                    )}
                </CardContent>
            </Card>
        </div>
    );
}
