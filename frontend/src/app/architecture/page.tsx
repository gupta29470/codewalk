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

    const { stats, centrality, cycles } = data;

    return (
        <div className="space-y-6">
            <div className="flex items-center justify-between">
                <h1 className="text-2xl font-bold">Architecture Health</h1>
                <Button variant="outline" size="sm" onClick={fetchData} disabled={loading}>
                    <RefreshCw className={`h-4 w-4 mr-1 ${loading ? "animate-spin" : ""}`} />
                    Refresh
                </Button>
            </div>

            {/* Stats row */}
            <div className="grid grid-cols-4 gap-4">
                <Card>
                    <CardContent className="pt-6">
                        <div className="flex items-center gap-3">
                            <FileBarChart className="h-8 w-8 text-muted-foreground" />
                            <div>
                                <p className="text-2xl font-bold">{stats.file_graph.vertices}</p>
                                <p className="text-sm text-muted-foreground">Files</p>
                            </div>
                        </div>
                    </CardContent>
                </Card>
                <Card>
                    <CardContent className="pt-6">
                        <div className="flex items-center gap-3">
                            <GitBranch className="h-8 w-8 text-muted-foreground" />
                            <div>
                                <p className="text-2xl font-bold">{stats.file_graph.edges}</p>
                                <p className="text-sm text-muted-foreground">Import Edges</p>
                            </div>
                        </div>
                    </CardContent>
                </Card>
                <Card>
                    <CardContent className="pt-6">
                        <div className="flex items-center gap-3">
                            <Network className="h-8 w-8 text-muted-foreground" />
                            <div>
                                <p className="text-2xl font-bold">{stats.module_graph.vertices}</p>
                                <p className="text-sm text-muted-foreground">Modules</p>
                            </div>
                        </div>
                    </CardContent>
                </Card>
                <Card>
                    <CardContent className="pt-6">
                        <div className="flex items-center gap-3">
                            <AlertTriangle className={`h-8 w-8 ${stats.file_graph.is_dag ? "text-green-500" : "text-destructive"}`} />
                            <div>
                                <p className="text-2xl font-bold">{stats.file_graph.is_dag ? "Clean" : "Cycles"}</p>
                                <p className="text-sm text-muted-foreground">DAG Status</p>
                            </div>
                        </div>
                    </CardContent>
                </Card>
            </div>

            {/* Bottleneck Files */}
            <Card>
                <CardHeader>
                    <CardTitle>Bottleneck Files (Betweenness Centrality)</CardTitle>
                    <p className="text-sm text-muted-foreground">
                        These files sit on the most import paths. Changes here ripple widely.
                    </p>
                </CardHeader>
                <CardContent className="space-y-2">
                    {centrality.betweenness.filter((b) => b.score > 0).length === 0 ? (
                        <p className="text-muted-foreground text-sm">No significant bottlenecks found.</p>
                    ) : (
                        centrality.betweenness
                            .filter((b) => b.score > 0)
                            .map((item) => (
                                <div
                                    key={item.file}
                                    className="flex items-center justify-between p-3 border rounded-md"
                                >
                                    <span className="font-mono text-sm">{item.file}</span>
                                    <Badge variant="secondary">{item.score.toFixed(4)}</Badge>
                                </div>
                            ))
                    )}
                </CardContent>
            </Card>

            {/* Most Important Files */}
            <Card>
                <CardHeader>
                    <CardTitle>Most Important Files (PageRank)</CardTitle>
                    <p className="text-sm text-muted-foreground">
                        Transitively depended on by the most code.
                    </p>
                </CardHeader>
                <CardContent className="space-y-2">
                    {centrality.pagerank.length === 0 ? (
                        <p className="text-muted-foreground text-sm">No PageRank data available.</p>
                    ) : (
                        centrality.pagerank.map((item) => (
                            <div
                                key={item.file}
                                className="flex items-center justify-between p-3 border rounded-md"
                            >
                                <span className="font-mono text-sm">{item.file}</span>
                                <Badge variant="secondary">{item.score.toFixed(4)}</Badge>
                            </div>
                        ))
                    )}
                </CardContent>
            </Card>

            {/* Circular Dependencies */}
            <Card>
                <CardHeader>
                    <CardTitle>Circular Dependencies</CardTitle>
                </CardHeader>
                <CardContent>
                    {!cycles.has_cycles ? (
                        <div className="flex items-center gap-2 text-green-600">
                            <Badge variant="outline" className="border-green-600 text-green-600">Clean DAG</Badge>
                            <span className="text-sm">No circular dependencies found.</span>
                        </div>
                    ) : (
                        <div className="space-y-4">
                            <p className="text-sm text-muted-foreground">
                                {cycles.cycle_groups.length} cycle group(s) found:
                            </p>
                            {cycles.cycle_groups.map((group, i) => (
                                <div key={i} className="p-3 border rounded-md">
                                    <p className="text-sm font-medium mb-2">Cycle {i + 1}</p>
                                    <div className="flex flex-wrap gap-1">
                                        {group.map((file) => (
                                            <Badge key={file} variant="outline" className="font-mono text-xs">
                                                {file.split("/").pop()}
                                            </Badge>
                                        ))}
                                    </div>
                                </div>
                            ))}
                            {cycles.edges_to_break.length > 0 && (
                                <div className="p-3 bg-accent/50 rounded-md">
                                    <p className="text-sm font-medium mb-2">Suggested Fixes</p>
                                    <p className="text-xs text-muted-foreground mb-2">
                                        Remove these imports to break the cycles:
                                    </p>
                                    {cycles.edges_to_break.map(([source, target], i) => (
                                        <div key={i} className="text-sm font-mono">
                                            <span className="text-destructive">-</span> {source.split("/").pop()} → {target.split("/").pop()}
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
