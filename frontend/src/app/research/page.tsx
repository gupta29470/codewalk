"use client";

import { useState } from "react";
import { api, ResearchResponse } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { Loader2, Search, AlertCircle } from "lucide-react";
import ReactMarkdown from "react-markdown";

const DEPTH_OPTIONS = [
    { value: "quick", label: "Quick", description: "Fast, shallow exploration" },
    { value: "standard", label: "Standard", description: "Balanced depth and speed" },
    { value: "deep", label: "Deep", description: "Thorough, multi-step research" },
];

export default function ResearchPage() {
    const [question, setQuestion] = useState("");
    const [depth, setDepth] = useState("standard");
    const [loading, setLoading] = useState(false);
    const [report, setReport] = useState("");
    const [sources, setSources] = useState<string[]>([]);
    const [error, setError] = useState("");

    async function handleResearch() {
        if (!question.trim()) return;
        setLoading(true);
        setError("");
        setReport("");
        setSources([]);
        try {
            const res: ResearchResponse = await api.research(question.trim(), depth);
            setReport(res.report);
            setSources(res.sources);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Research failed");
        } finally {
            setLoading(false);
        }
    }

    const depthButtonClass = (active: boolean) =>
        active
            ? "bg-kinetic-primary text-kinetic-on-primary hover:bg-kinetic-primary/90"
            : "border-kinetic-border bg-kinetic-surface-container text-kinetic-on-surface hover:bg-kinetic-surface-container-high hover:text-kinetic-on-surface";

    return (
        <div className="p-6 space-y-6 max-w-6xl">
            <div>
                <h1 className="text-2xl font-bold text-kinetic-on-surface">Deep Research</h1>
                <p className="text-sm text-kinetic-on-surface-variant">
                    Ask a complex question about your codebase and get a thorough researched report.
                </p>
            </div>

            <Card className="border-kinetic-border bg-kinetic-surface-container-low">
                <CardHeader>
                    <CardTitle className="flex items-center gap-2 text-kinetic-on-surface">
                        <Search className="h-5 w-5 text-kinetic-primary" />
                        Research Question
                    </CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                    <Input
                        placeholder="e.g. How does the authentication flow work across all modules?"
                        value={question}
                        onChange={(e) => setQuestion(e.target.value)}
                        onKeyDown={(e) => e.key === "Enter" && handleResearch()}
                        className="border-kinetic-border bg-kinetic-surface-container text-kinetic-on-surface placeholder:text-kinetic-on-surface-variant focus-visible:ring-kinetic-primary"
                    />

                    <div className="flex gap-2 flex-wrap">
                        {DEPTH_OPTIONS.map((d) => (
                            <Button
                                key={d.value}
                                variant="outline"
                                size="sm"
                                onClick={() => setDepth(d.value)}
                                className={depthButtonClass(depth === d.value)}
                            >
                                {d.label}
                            </Button>
                        ))}
                    </div>

                    <Button
                        onClick={handleResearch}
                        disabled={loading || !question.trim()}
                        className="w-full sm:w-auto bg-kinetic-primary text-kinetic-on-primary hover:bg-kinetic-primary/90"
                    >
                        {loading ? (
                            <>
                                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                Researching...
                            </>
                        ) : (
                            "Run Research"
                        )}
                    </Button>

                    {error && (
                        <div className="p-3 bg-kinetic-error/10 text-kinetic-error rounded-md text-sm flex items-center gap-2 border border-kinetic-error/20">
                            <AlertCircle className="h-4 w-4" />
                            {error}
                        </div>
                    )}
                </CardContent>
            </Card>

            {report && (
                <Card className="border-kinetic-border bg-kinetic-surface-container-low">
                    <CardHeader>
                        <CardTitle className="text-kinetic-on-surface">Research Report</CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        <div className="prose prose-sm dark:prose-invert max-w-none research-report">
                            <ReactMarkdown>{report}</ReactMarkdown>
                        </div>
                        {sources.length > 0 && (
                            <>
                                <Separator className="bg-kinetic-border" />
                                <div>
                                    <p className="text-sm font-medium mb-2 text-kinetic-on-surface">Sources</p>
                                    <div className="flex flex-wrap gap-2">
                                        {sources.map((s, i) => (
                                            <Badge key={i} variant="outline" className="text-xs font-mono border-kinetic-border text-kinetic-on-surface-variant">
                                                {s.split("/").pop() || s}
                                            </Badge>
                                        ))}
                                    </div>
                                </div>
                            </>
                        )}
                    </CardContent>
                </Card>
            )}

        </div>
    );
}
