"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { Loader2, FileSearch, BookOpen, Upload, AlertCircle } from "lucide-react";
import ReactMarkdown from "react-markdown";

type Tab = "index" | "search" | "ask";

export default function DocsPage() {
    const [tab, setTab] = useState<Tab>("index");

    // Index state
    const [docsPath, setDocsPath] = useState("");
    const [indexLoading, setIndexLoading] = useState(false);
    const [indexResult, setIndexResult] = useState("");
    const [indexError, setIndexError] = useState("");

    // Search state
    const [searchQuery, setSearchQuery] = useState("");
    const [searchLoading, setSearchLoading] = useState(false);
    const [searchResults, setSearchResults] = useState<{ text: string; metadata: Record<string, unknown>; distance: number }[]>([]);
    const [searchError, setSearchError] = useState("");

    // Ask state
    const [askQuestion, setAskQuestion] = useState("");
    const [askLoading, setAskLoading] = useState(false);
    const [askAnswer, setAskAnswer] = useState("");
    const [askSources, setAskSources] = useState<{ doc_path: string; section: string }[]>([]);
    const [askError, setAskError] = useState("");

    async function handleIndex() {
        if (!docsPath.trim()) return;
        setIndexLoading(true);
        setIndexError("");
        setIndexResult("");
        try {
            const res = await api.indexDocs(docsPath.trim());
            setIndexResult(`Indexed ${res.files_indexed} files → ${res.chunks_created} chunks`);
        } catch (err) {
            setIndexError(err instanceof Error ? err.message : "Index failed");
        } finally {
            setIndexLoading(false);
        }
    }

    async function handleSearch() {
        if (!searchQuery.trim()) return;
        setSearchLoading(true);
        setSearchError("");
        setSearchResults([]);
        try {
            const res = await api.searchDocs(searchQuery.trim());
            setSearchResults(res.results);
        } catch (err) {
            setSearchError(err instanceof Error ? err.message : "Search failed");
        } finally {
            setSearchLoading(false);
        }
    }

    async function handleAsk() {
        if (!askQuestion.trim()) return;
        setAskLoading(true);
        setAskError("");
        setAskAnswer("");
        setAskSources([]);
        try {
            const res = await api.askDocs(askQuestion.trim());
            setAskAnswer(res.answer);
            setAskSources(res.sources);
        } catch (err) {
            setAskError(err instanceof Error ? err.message : "Ask failed");
        } finally {
            setAskLoading(false);
        }
    }

    return (
        <div className="space-y-6">
            <h1 className="text-2xl font-bold">Team Docs</h1>
            <p className="text-sm text-muted-foreground">
                Index, search, and ask questions about your team&apos;s documentation (architecture decisions, coding standards, runbooks, etc.).
            </p>

            {/* Tab bar */}
            <div className="flex gap-2">
                {[
                    { id: "index" as Tab, label: "Index Docs", icon: Upload },
                    { id: "search" as Tab, label: "Search", icon: FileSearch },
                    { id: "ask" as Tab, label: "Ask", icon: BookOpen },
                ].map((t) => (
                    <Button
                        key={t.id}
                        variant={tab === t.id ? "default" : "outline"}
                        size="sm"
                        onClick={() => setTab(t.id)}
                    >
                        <t.icon className="h-4 w-4 mr-1" />
                        {t.label}
                    </Button>
                ))}
            </div>

            {/* ── Index Tab ── */}
            {tab === "index" && (
                <Card>
                    <CardHeader>
                        <CardTitle className="flex items-center gap-2">
                            <Upload className="h-5 w-5" />
                            Index Documentation
                        </CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        <p className="text-sm text-muted-foreground">
                            Provide a path to a folder containing .md, .txt, .rst, or .pdf files.
                            They will be chunked and embedded for semantic search.
                        </p>
                        <div className="flex gap-2">
                            <Input
                                placeholder="Path to docs folder (e.g. ./docs or ./wiki)"
                                value={docsPath}
                                onChange={(e) => setDocsPath(e.target.value)}
                                onKeyDown={(e) => e.key === "Enter" && handleIndex()}
                            />
                            <Button onClick={handleIndex} disabled={indexLoading || !docsPath.trim()}>
                                {indexLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : "Index"}
                            </Button>
                        </div>
                        {indexError && (
                            <div className="p-3 bg-destructive/10 text-destructive rounded-md text-sm flex items-center gap-2">
                                <AlertCircle className="h-4 w-4" />
                                {indexError}
                            </div>
                        )}
                        {indexResult && (
                            <div className="p-3 bg-green-50 dark:bg-green-950 rounded-md text-green-700 dark:text-green-300 text-sm">
                                ✅ {indexResult}
                            </div>
                        )}
                    </CardContent>
                </Card>
            )}

            {/* ── Search Tab ── */}
            {tab === "search" && (
                <Card>
                    <CardHeader>
                        <CardTitle className="flex items-center gap-2">
                            <FileSearch className="h-5 w-5" />
                            Search Docs
                        </CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        <div className="flex gap-2">
                            <Input
                                placeholder="Search query..."
                                value={searchQuery}
                                onChange={(e) => setSearchQuery(e.target.value)}
                                onKeyDown={(e) => e.key === "Enter" && handleSearch()}
                            />
                            <Button onClick={handleSearch} disabled={searchLoading || !searchQuery.trim()}>
                                {searchLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : "Search"}
                            </Button>
                        </div>
                        {searchError && (
                            <div className="p-3 bg-destructive/10 text-destructive rounded-md text-sm flex items-center gap-2">
                                <AlertCircle className="h-4 w-4" />
                                {searchError}
                            </div>
                        )}
                        {searchResults.length > 0 && (
                            <ScrollArea className="max-h-[60vh]">
                                <div className="space-y-3">
                                    {searchResults.map((r, i) => (
                                        <Card key={i} className="p-3">
                                            <div className="flex items-center gap-2 mb-2">
                                                <Badge variant="outline">#{i + 1}</Badge>
                                                <span className="text-xs text-muted-foreground">
                                                    distance: {r.distance.toFixed(4)}
                                                </span>
                                                {r.metadata?.doc_path ? (
                                                    <span className="text-xs font-mono text-muted-foreground">
                                                        {String(r.metadata.doc_path).split("/").pop()}
                                                    </span>
                                                ) : null}
                                            </div>
                                            <pre className="text-xs bg-muted p-2 rounded overflow-x-auto whitespace-pre-wrap">
                                                {r.text}
                                            </pre>
                                        </Card>
                                    ))}
                                </div>
                            </ScrollArea>
                        )}
                    </CardContent>
                </Card>
            )}

            {/* ── Ask Tab ── */}
            {tab === "ask" && (
                <Card>
                    <CardHeader>
                        <CardTitle className="flex items-center gap-2">
                            <BookOpen className="h-5 w-5" />
                            Ask Docs
                        </CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        <div className="flex gap-2">
                            <Input
                                placeholder="Ask a question about your docs..."
                                value={askQuestion}
                                onChange={(e) => setAskQuestion(e.target.value)}
                                onKeyDown={(e) => e.key === "Enter" && handleAsk()}
                            />
                            <Button onClick={handleAsk} disabled={askLoading || !askQuestion.trim()}>
                                {askLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : "Ask"}
                            </Button>
                        </div>
                        {askError && (
                            <div className="p-3 bg-destructive/10 text-destructive rounded-md text-sm flex items-center gap-2">
                                <AlertCircle className="h-4 w-4" />
                                {askError}
                            </div>
                        )}
                        {askAnswer && (
                            <div className="space-y-4">
                                <div className="prose prose-sm dark:prose-invert max-w-none p-4 bg-muted rounded-md">
                                    <ReactMarkdown>{askAnswer}</ReactMarkdown>
                                </div>
                                {askSources.length > 0 && (
                                    <>
                                        <Separator />
                                        <div>
                                            <p className="text-sm font-medium mb-2">Sources</p>
                                            <div className="flex flex-wrap gap-2">
                                                {askSources.map((s, i) => (
                                                    <Badge key={i} variant="outline" className="text-xs">
                                                        {s.doc_path.split("/").pop()} → {s.section}
                                                    </Badge>
                                                ))}
                                            </div>
                                        </div>
                                    </>
                                )}
                            </div>
                        )}
                    </CardContent>
                </Card>
            )}
        </div>
    );
}
