"use client";

import { useState } from "react";
import { api, ReviewIssue } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { Loader2, ShieldCheck, FileSearch, BookOpen, AlertCircle } from "lucide-react";
import ReactMarkdown from "react-markdown";

const SEVERITY_STYLES: Record<string, string> = {
    critical: "bg-red-500 text-white",
    warning: "bg-yellow-500 text-white",
    suggestion: "bg-green-500 text-white",
};

const SEVERITY_ICONS: Record<string, string> = {
    critical: "🔴",
    warning: "🟡",
    suggestion: "🟢",
};

type Tab = "diff" | "file" | "guidelines";

export default function ReviewPage() {
    const [tab, setTab] = useState<Tab>("diff");

    // Diff review state
    const [staged, setStaged] = useState(false);
    const [targetBranch, setTargetBranch] = useState("");
    const [issues, setIssues] = useState<ReviewIssue[]>([]);
    const [summary, setSummary] = useState("");
    const [stats, setStats] = useState({ files: 0, added: 0, removed: 0 });
    const [diffLoading, setDiffLoading] = useState(false);
    const [diffError, setDiffError] = useState("");

    // File review state
    const [filePath, setFilePath] = useState("");
    const [fileReview, setFileReview] = useState("");
    const [fileLoading, setFileLoading] = useState(false);
    const [fileError, setFileError] = useState("");

    // Guidelines state
    const [guidelinesPath, setGuidelinesPath] = useState("");
    const [guidelinesResult, setGuidelinesResult] = useState("");
    const [guidelinesLoading, setGuidelinesLoading] = useState(false);
    const [guidelinesError, setGuidelinesError] = useState("");

    async function handleReviewDiff() {
        setDiffLoading(true);
        setDiffError("");
        setIssues([]);
        setSummary("");
        try {
            const res = await api.reviewDiff(staged, targetBranch || undefined);
            setIssues(res.issues);
            setSummary(res.summary);
            setStats({
                files: res.files_reviewed,
                added: res.lines_added,
                removed: res.lines_removed,
            });
        } catch (err) {
            setDiffError(err instanceof Error ? err.message : "Review failed");
        } finally {
            setDiffLoading(false);
        }
    }

    async function handleReviewFile() {
        if (!filePath.trim()) return;
        setFileLoading(true);
        setFileError("");
        setFileReview("");
        try {
            const res = await api.reviewFile(filePath.trim());
            setFileReview(res.review);
        } catch (err) {
            setFileError(err instanceof Error ? err.message : "Review failed");
        } finally {
            setFileLoading(false);
        }
    }

    async function handleLoadGuidelines() {
        setGuidelinesLoading(true);
        setGuidelinesError("");
        setGuidelinesResult("");
        try {
            const res = await api.loadGuidelines(guidelinesPath || undefined);
            setGuidelinesResult(`Loaded ${res.chunks} guideline chunks from ${res.path}`);
        } catch (err) {
            setGuidelinesError(err instanceof Error ? err.message : "Failed to load guidelines");
        } finally {
            setGuidelinesLoading(false);
        }
    }

    return (
        <div className="space-y-6">
            <h1 className="text-2xl font-bold">Code Review</h1>

            {/* Tab bar */}
            <div className="flex gap-2">
                {[
                    { id: "diff" as Tab, label: "Review Diff", icon: ShieldCheck },
                    { id: "file" as Tab, label: "Review File", icon: FileSearch },
                    { id: "guidelines" as Tab, label: "Guidelines", icon: BookOpen },
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

            {/* ── Diff Review Tab ── */}
            {tab === "diff" && (
                <Card>
                    <CardHeader>
                        <CardTitle className="flex items-center gap-2">
                            <ShieldCheck className="h-5 w-5" />
                            Review Git Diff
                        </CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        <div className="flex flex-wrap gap-4 items-end">
                            <div className="space-y-1">
                                <label className="text-sm font-medium">Target Branch</label>
                                <Input
                                    placeholder="e.g. main (optional)"
                                    value={targetBranch}
                                    onChange={(e) => setTargetBranch(e.target.value)}
                                    className="w-48"
                                />
                            </div>
                            <label className="flex items-center gap-2 text-sm cursor-pointer">
                                <input
                                    type="checkbox"
                                    checked={staged}
                                    onChange={(e) => setStaged(e.target.checked)}
                                    className="accent-primary"
                                />
                                Staged only
                            </label>
                            <Button onClick={handleReviewDiff} disabled={diffLoading}>
                                {diffLoading ? (
                                    <>
                                        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                        Reviewing...
                                    </>
                                ) : (
                                    "Run Review"
                                )}
                            </Button>
                        </div>

                        {diffError && (
                            <div className="p-3 bg-destructive/10 text-destructive rounded-md text-sm flex items-center gap-2">
                                <AlertCircle className="h-4 w-4" />
                                {diffError}
                            </div>
                        )}

                        {(issues.length > 0 || summary) && (
                            <div className="space-y-4">
                                <div className="flex gap-4 text-sm text-muted-foreground">
                                    <span>{stats.files} files reviewed</span>
                                    <span className="text-green-600">+{stats.added}</span>
                                    <span className="text-red-600">-{stats.removed}</span>
                                </div>

                                <Separator />

                                {issues.length === 0 ? (
                                    <div className="p-4 bg-green-50 dark:bg-green-950 rounded-md text-green-700 dark:text-green-300 text-sm">
                                        ✅ No issues found
                                    </div>
                                ) : (
                                    <ScrollArea className="max-h-[60vh]">
                                        <div className="space-y-3">
                                            {issues
                                                .sort((a, b) => {
                                                    const order = { critical: 0, warning: 1, suggestion: 2 };
                                                    return (order[a.severity as keyof typeof order] ?? 3) -
                                                        (order[b.severity as keyof typeof order] ?? 3);
                                                })
                                                .map((issue, idx) => (
                                                    <Card key={idx} className="p-3">
                                                        <div className="flex items-start gap-2">
                                                            <span className="text-lg">{SEVERITY_ICONS[issue.severity] || "⚪"}</span>
                                                            <div className="flex-1 space-y-1">
                                                                <div className="flex items-center gap-2 flex-wrap">
                                                                    <span className="font-medium text-sm">{issue.title}</span>
                                                                    <Badge className={SEVERITY_STYLES[issue.severity] || ""} variant="secondary">
                                                                        {issue.severity}
                                                                    </Badge>
                                                                    <Badge variant="outline">{issue.category}</Badge>
                                                                </div>
                                                                <p className="text-xs text-muted-foreground">
                                                                    {issue.file_path}
                                                                    {issue.line_number ? `:${issue.line_number}` : ""}
                                                                </p>
                                                                <p className="text-sm">{issue.explanation}</p>
                                                                {issue.suggestion && (
                                                                    <p className="text-sm text-blue-600 dark:text-blue-400">
                                                                        💡 {issue.suggestion}
                                                                    </p>
                                                                )}
                                                                {issue.code_snippet && (
                                                                    <pre className="text-xs bg-muted p-2 rounded mt-1 overflow-x-auto">
                                                                        {issue.code_snippet}
                                                                    </pre>
                                                                )}
                                                            </div>
                                                        </div>
                                                    </Card>
                                                ))}
                                        </div>
                                    </ScrollArea>
                                )}

                                {summary && (
                                    <>
                                        <Separator />
                                        <div className="text-sm">
                                            <span className="font-medium">Summary: </span>
                                            {summary}
                                        </div>
                                    </>
                                )}
                            </div>
                        )}
                    </CardContent>
                </Card>
            )}

            {/* ── File Review Tab ── */}
            {tab === "file" && (
                <Card>
                    <CardHeader>
                        <CardTitle className="flex items-center gap-2">
                            <FileSearch className="h-5 w-5" />
                            Review Single File
                        </CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        <p className="text-sm text-muted-foreground">
                            Review a file against codebase conventions and patterns.
                            Requires the codebase to be indexed first.
                        </p>
                        <div className="flex gap-2">
                            <Input
                                placeholder="Path to file (e.g. src/codewalk/pipeline.py)"
                                value={filePath}
                                onChange={(e) => setFilePath(e.target.value)}
                                onKeyDown={(e) => e.key === "Enter" && handleReviewFile()}
                            />
                            <Button onClick={handleReviewFile} disabled={fileLoading || !filePath.trim()}>
                                {fileLoading ? (
                                    <Loader2 className="h-4 w-4 animate-spin" />
                                ) : (
                                    "Review"
                                )}
                            </Button>
                        </div>

                        {fileError && (
                            <div className="p-3 bg-destructive/10 text-destructive rounded-md text-sm">
                                {fileError}
                            </div>
                        )}

                        {fileReview && (
                            <Card className="p-4">
                                <div className="prose prose-sm dark:prose-invert max-w-none">
                                    <ReactMarkdown>{fileReview}</ReactMarkdown>
                                </div>
                            </Card>
                        )}
                    </CardContent>
                </Card>
            )}

            {/* ── Guidelines Tab ── */}
            {tab === "guidelines" && (
                <Card>
                    <CardHeader>
                        <CardTitle className="flex items-center gap-2">
                            <BookOpen className="h-5 w-5" />
                            Load Coding Guidelines
                        </CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        <p className="text-sm text-muted-foreground">
                            Load your team&apos;s coding standards (.md, .txt, .rst) so reviews check against them.
                            Leave empty to use the configured REVIEW_GUIDELINES_PATH.
                        </p>
                        <div className="flex gap-2">
                            <Input
                                placeholder="Path to guidelines directory (optional)"
                                value={guidelinesPath}
                                onChange={(e) => setGuidelinesPath(e.target.value)}
                            />
                            <Button onClick={handleLoadGuidelines} disabled={guidelinesLoading}>
                                {guidelinesLoading ? (
                                    <Loader2 className="h-4 w-4 animate-spin" />
                                ) : (
                                    "Load"
                                )}
                            </Button>
                        </div>

                        {guidelinesError && (
                            <div className="p-3 bg-destructive/10 text-destructive rounded-md text-sm">
                                {guidelinesError}
                            </div>
                        )}

                        {guidelinesResult && (
                            <div className="p-3 bg-green-50 dark:bg-green-950 rounded-md text-green-700 dark:text-green-300 text-sm">
                                ✅ {guidelinesResult}
                            </div>
                        )}
                    </CardContent>
                </Card>
            )}
        </div>
    );
}
