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

    const tabButtonClass = (active: boolean) =>
        active
            ? "bg-kinetic-primary text-kinetic-on-primary hover:bg-kinetic-primary/90"
            : "border-kinetic-border bg-kinetic-surface-container text-kinetic-on-surface hover:bg-kinetic-surface-container-high hover:text-kinetic-on-surface";

    return (
        <div className="p-6 space-y-6 max-w-6xl">
            <div>
                <h1 className="text-2xl font-bold text-kinetic-on-surface">Team Docs</h1>
                <p className="text-sm text-kinetic-on-surface-variant">
                    Index, search, and ask questions about your team&apos;s documentation (architecture decisions, coding standards, runbooks, etc.).
                </p>
            </div>

            {/* Tab bar */}
            <div className="flex gap-2 flex-wrap">
                {[
                    { id: "index" as Tab, label: "Index Docs", icon: Upload },
                    { id: "search" as Tab, label: "Search", icon: FileSearch },
                    { id: "ask" as Tab, label: "Ask", icon: BookOpen },
                ].map((t) => (
                    <Button
                        key={t.id}
                        variant="outline"
                        size="sm"
                        onClick={() => setTab(t.id)}
                        className={tabButtonClass(tab === t.id)}
                    >
                        <t.icon className="h-4 w-4 mr-1" />
                        {t.label}
                    </Button>
                ))}
            </div>

            {/* ── Index Tab ── */}
            {tab === "index" && (
                <Card className="border-kinetic-border bg-kinetic-surface-container-low">
                    <CardHeader>
                        <CardTitle className="flex items-center gap-2 text-kinetic-on-surface">
                            <Upload className="h-5 w-5 text-kinetic-primary" />
                            Index Documentation
                        </CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        <p className="text-sm text-kinetic-on-surface-variant">
                            Provide a path to a folder containing .md, .txt, .rst, or .pdf files.
                            They will be chunked and embedded for semantic search.
                        </p>
                        <div className="flex gap-2">
                            <Input
                                placeholder="Path to docs folder (e.g. ./docs or ./wiki)"
                                value={docsPath}
                                onChange={(e) => setDocsPath(e.target.value)}
                                onKeyDown={(e) => e.key === "Enter" && handleIndex()}
                                className="border-kinetic-border bg-kinetic-surface-container text-kinetic-on-surface placeholder:text-kinetic-on-surface-variant focus-visible:ring-kinetic-primary"
                            />
                            <Button
                                onClick={handleIndex}
                                disabled={indexLoading || !docsPath.trim()}
                                className="bg-kinetic-primary text-kinetic-on-primary hover:bg-kinetic-primary/90"
                            >
                                {indexLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : "Index"}
                            </Button>
                        </div>
                        {indexError && (
                            <div className="p-3 bg-kinetic-error/10 text-kinetic-error rounded-md text-sm flex items-center gap-2 border border-kinetic-error/20">
                                <AlertCircle className="h-4 w-4" />
                                {indexError}
                            </div>
                        )}
                        {indexResult && (
                            <div className="p-3 rounded-md text-sm border border-kinetic-node-config/30 bg-kinetic-node-config/10 text-kinetic-node-config">
                                ✅ {indexResult}
                            </div>
                        )}
                    </CardContent>
                </Card>
            )}

            {/* ── Search Tab ── */}
            {tab === "search" && (
                <Card className="border-kinetic-border bg-kinetic-surface-container-low">
                    <CardHeader>
                        <CardTitle className="flex items-center gap-2 text-kinetic-on-surface">
                            <FileSearch className="h-5 w-5 text-kinetic-primary" />
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
                                className="border-kinetic-border bg-kinetic-surface-container text-kinetic-on-surface placeholder:text-kinetic-on-surface-variant focus-visible:ring-kinetic-primary"
                            />
                            <Button
                                onClick={handleSearch}
                                disabled={searchLoading || !searchQuery.trim()}
                                className="bg-kinetic-primary text-kinetic-on-primary hover:bg-kinetic-primary/90"
                            >
                                {searchLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : "Search"}
                            </Button>
                        </div>
                        {searchError && (
                            <div className="p-3 bg-kinetic-error/10 text-kinetic-error rounded-md text-sm flex items-center gap-2 border border-kinetic-error/20">
                                <AlertCircle className="h-4 w-4" />
                                {searchError}
                            </div>
                        )}
                        {searchResults.length > 0 && (
                            <ScrollArea className="max-h-[60vh]">
                                <div className="space-y-3">
                                    {searchResults.map((r, i) => (
                                        <Card key={i} className="p-3 border-kinetic-border bg-kinetic-surface-container">
                                            <div className="flex items-center gap-2 mb-2">
                                                <Badge variant="outline" className="border-kinetic-border text-kinetic-on-surface-variant">#{i + 1}</Badge>
                                                <span className="text-xs text-kinetic-on-surface-variant">
                                                    distance: {r.distance.toFixed(4)}
                                                </span>
                                                {r.metadata?.doc_path ? (
                                                    <span className="text-xs font-mono text-kinetic-on-surface-variant">
                                                        {String(r.metadata.doc_path).split("/").pop()}
                                                    </span>
                                                ) : null}
                                            </div>
                                            <pre className="text-xs p-2 rounded overflow-x-auto whitespace-pre-wrap bg-kinetic-surface-container-high text-kinetic-on-surface border border-kinetic-border">
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
                <Card className="border-kinetic-border bg-kinetic-surface-container-low">
                    <CardHeader>
                        <CardTitle className="flex items-center gap-2 text-kinetic-on-surface">
                            <BookOpen className="h-5 w-5 text-kinetic-primary" />
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
                                className="border-kinetic-border bg-kinetic-surface-container text-kinetic-on-surface placeholder:text-kinetic-on-surface-variant focus-visible:ring-kinetic-primary"
                            />
                            <Button
                                onClick={handleAsk}
                                disabled={askLoading || !askQuestion.trim()}
                                className="bg-kinetic-primary text-kinetic-on-primary hover:bg-kinetic-primary/90"
                            >
                                {askLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : "Ask"}
                            </Button>
                        </div>
                        {askError && (
                            <div className="p-3 bg-kinetic-error/10 text-kinetic-error rounded-md text-sm flex items-center gap-2 border border-kinetic-error/20">
                                <AlertCircle className="h-4 w-4" />
                                {askError}
                            </div>
                        )}
                        {askAnswer && (
                            <div className="space-y-4">
                                <div className="prose prose-sm dark:prose-invert max-w-none p-4 rounded-md border border-kinetic-border bg-kinetic-surface-container text-kinetic-on-surface-variant">
                                    <ReactMarkdown>{askAnswer}</ReactMarkdown>
                                </div>
                                {askSources.length > 0 && (
                                    <>
                                        <Separator className="bg-kinetic-border" />
                                        <div>
                                            <p className="text-sm font-medium mb-2 text-kinetic-on-surface">Sources</p>
                                            <div className="flex flex-wrap gap-2">
                                                {askSources.map((s, i) => (
                                                    <Badge key={i} variant="outline" className="text-xs border-kinetic-border text-kinetic-on-surface-variant">
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
