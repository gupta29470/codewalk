"use client";

import { useState } from "react";
import { api, ReviewIssue, FixItem } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { Loader2, ShieldCheck, FileSearch, BookOpen, AlertCircle, Wrench, Plus, Trash2 } from "lucide-react";
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

type Tab = "diff" | "file" | "guidelines" | "apply";

interface FixRow {
    id: number;
    file_path: string;
    old_code: string;
    new_code: string;
}

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

    // Apply fixes state
    const [fixes, setFixes] = useState<FixRow[]>([]);
    const [fixNextId, setFixNextId] = useState(1);
    const [applyLoading, setApplyLoading] = useState(false);
    const [applyResult, setApplyResult] = useState("");
    const [applyError, setApplyError] = useState("");

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
            const issueLines = res.issues.length
                ? res.issues
                      .map(
                          (issue) =>
                              `- **[${issue.severity}]** ${issue.file_path}${issue.line_number !== null ? `:${issue.line_number}` : ""} — ${issue.title}\n  ${issue.explanation}`
                      )
                      .join("\n")
                : "✅ No issues found";
            const md = [
                `## File Review: ${res.file_path}`,
                `**Verdict:** ${res.verdict}`,
                "",
                res.verdict_reason,
                "",
                "### Summary",
                res.summary,
                "",
                "### Issues",
                issueLines,
            ].join("\n");
            setFileReview(md);
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

    function addFix() {
        setFixes((prev) => [...prev, { id: fixNextId, file_path: "", old_code: "", new_code: "" }]);
        setFixNextId((id) => id + 1);
    }

    function removeFix(id: number) {
        setFixes((prev) => prev.filter((f) => f.id !== id));
    }

    function updateFix(id: number, field: keyof FixRow, value: string) {
        setFixes((prev) => prev.map((f) => (f.id === id ? { ...f, [field]: value } : f)));
    }

    async function handleApplyFixes() {
        const validFixes = fixes.filter((f) => f.file_path.trim() && f.old_code.trim());
        if (validFixes.length === 0) return;

        setApplyLoading(true);
        setApplyError("");
        setApplyResult("");

        const payload: FixItem[] = validFixes.map((f) => ({
            file_path: f.file_path.trim(),
            old_code: f.old_code,
            new_code: f.new_code,
        }));

        try {
            const res = await api.applyFixes(payload);
            if (res.failed && res.failed.length > 0) {
                const first = res.failed[0];
                setApplyError(
                    `Fix ${first.index + 1} failed: ${first.error}` +
                    (res.failed.length > 1 ? ` (${res.failed.length} total failures)` : "")
                );
            } else {
                setApplyResult(`Applied ${res.applied.length}/${res.total} fixes successfully.`);
                if (res.applied.length === res.total) {
                    setFixes([]);
                }
            }
        } catch (err) {
            setApplyError(err instanceof Error ? err.message : "Apply fixes failed");
        } finally {
            setApplyLoading(false);
        }
    }

    return (
        <div className="space-y-6">
            <h1 className="text-2xl font-bold">Code Review</h1>

            {/* Tab bar */}
            <div className="flex gap-2 flex-wrap">
                {[
                    { id: "diff" as Tab, label: "Review Diff", icon: ShieldCheck },
                    { id: "file" as Tab, label: "Review File", icon: FileSearch },
                    { id: "guidelines" as Tab, label: "Guidelines", icon: BookOpen },
                    { id: "apply" as Tab, label: "Apply Fixes", icon: Wrench },
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
                            Load your team&apos;s coding standards (.md, .txt, .rst, .pdf) so reviews check against them.
                            Reviews automatically use guidelines configured in codewalk.yaml; use this only to load an explicit directory.
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

            {/* ── Apply Fixes Tab ── */}
            {tab === "apply" && (
                <Card>
                    <CardHeader>
                        <CardTitle className="flex items-center gap-2">
                            <Wrench className="h-5 w-5" />
                            Apply Code Fixes
                        </CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        <p className="text-sm text-muted-foreground">
                            Manually specify exact text replacements. Each fix searches for
                            <code className="bg-muted px-1 rounded text-xs">old_code</code>
                            exactly once in the file and replaces it with
                            <code className="bg-muted px-1 rounded text-xs">new_code</code>.
                            Include surrounding context (2–3 lines) in old_code to avoid ambiguous matches.
                        </p>

                        <div className="space-y-3">
                            {fixes.map((fix) => (
                                <Card key={fix.id} className="p-3 space-y-2">
                                    <div className="flex items-center gap-2">
                                        <Input
                                            placeholder="File path (e.g. src/main.py)"
                                            value={fix.file_path}
                                            onChange={(e) => updateFix(fix.id, "file_path", e.target.value)}
                                            className="flex-1"
                                        />
                                        <Button
                                            variant="ghost"
                                            size="sm"
                                            onClick={() => removeFix(fix.id)}
                                            className="text-destructive"
                                        >
                                            <Trash2 className="h-4 w-4" />
                                        </Button>
                                    </div>
                                    <Textarea
                                        placeholder="Old code (exact text to find — include surrounding lines for uniqueness)"
                                        value={fix.old_code}
                                        onChange={(e) => updateFix(fix.id, "old_code", e.target.value)}
                                        rows={3}
                                        className="font-mono text-xs"
                                    />
                                    <Textarea
                                        placeholder="New code (replacement text)"
                                        value={fix.new_code}
                                        onChange={(e) => updateFix(fix.id, "new_code", e.target.value)}
                                        rows={3}
                                        className="font-mono text-xs"
                                    />
                                </Card>
                            ))}
                        </div>

                        <div className="flex gap-2">
                            <Button variant="outline" size="sm" onClick={addFix}>
                                <Plus className="h-4 w-4 mr-1" />
                                Add Fix
                            </Button>
                            <Button
                                onClick={handleApplyFixes}
                                disabled={applyLoading || fixes.filter((f) => f.file_path.trim() && f.old_code.trim()).length === 0}
                            >
                                {applyLoading ? (
                                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                ) : (
                                    "Apply Fixes"
                                )}
                            </Button>
                        </div>

                        {applyError && (
                            <div className="p-3 bg-destructive/10 text-destructive rounded-md text-sm flex items-center gap-2">
                                <AlertCircle className="h-4 w-4" />
                                {applyError}
                            </div>
                        )}

                        {applyResult && (
                            <div className="p-3 bg-green-50 dark:bg-green-950 rounded-md text-green-700 dark:text-green-300 text-sm">
                                ✅ {applyResult}
                            </div>
                        )}
                    </CardContent>
                </Card>
            )}
        </div>
    );
}
