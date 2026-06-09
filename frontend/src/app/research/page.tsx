"use client";

import { useState } from "react";
import { api } from "@/lib/api";
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
            const res = await api.research(question.trim(), depth);
            setReport(res.report);
            setSources(res.sources);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Research failed");
        } finally {
            setLoading(false);
        }
    }

    return (
        <div className="space-y-6">
            <h1 className="text-2xl font-bold">Deep Research</h1>
            <p className="text-sm text-muted-foreground">
                Ask a complex question about your codebase and get a thorough researched report.
            </p>

            <Card>
                <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                        <Search className="h-5 w-5" />
                        Research Question
                    </CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                    <Input
                        placeholder="e.g. How does the authentication flow work across all modules?"
                        value={question}
                        onChange={(e) => setQuestion(e.target.value)}
                        onKeyDown={(e) => e.key === "Enter" && handleResearch()}
                    />

                    <div className="flex gap-2">
                        {DEPTH_OPTIONS.map((d) => (
                            <Button
                                key={d.value}
                                variant={depth === d.value ? "default" : "outline"}
                                size="sm"
                                onClick={() => setDepth(d.value)}
                            >
                                {d.label}
                            </Button>
                        ))}
                    </div>

                    <Button
                        onClick={handleResearch}
                        disabled={loading || !question.trim()}
                        className="w-full sm:w-auto"
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
                        <div className="p-3 bg-destructive/10 text-destructive rounded-md text-sm flex items-center gap-2">
                            <AlertCircle className="h-4 w-4" />
                            {error}
                        </div>
                    )}
                </CardContent>
            </Card>

            {report && (
                <Card>
                    <CardHeader>
                        <CardTitle>Research Report</CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        <div className="prose prose-sm dark:prose-invert max-w-none">
                            <ReactMarkdown>{report}</ReactMarkdown>
                        </div>
                        {sources.length > 0 && (
                            <>
                                <Separator />
                                <div>
                                    <p className="text-sm font-medium mb-2">Sources</p>
                                    <div className="flex flex-wrap gap-2">
                                        {sources.map((s, i) => (
                                            <Badge key={i} variant="outline" className="text-xs font-mono">
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
