"use client";

import { useState } from "react";
import { api, ReviewIssue, EditPreview, ApplyEditsResponse } from "@/lib/api";
import { useAnalyze } from "@/lib/analyze-context";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { Loader2, ShieldCheck, FileSearch, BookOpen, AlertCircle, Check, X, ChevronDown, ChevronUp } from "lucide-react";
import ReactMarkdown from "react-markdown";

const SEVERITY_STYLES: Record<string, string> = {
    critical: "bg-kinetic-error text-kinetic-on-error",
    warning: "bg-kinetic-tertiary text-kinetic-on-tertiary",
    suggestion: "bg-kinetic-node-config text-kinetic-on-primary",
};

const SEVERITY_ICONS: Record<string, string> = {
    critical: "🔴",
    warning: "🟡",
    suggestion: "🟢",
};

// ─── Line diff helpers (LCS-based, no dependencies) ──────────────────

type DiffLine = { type: "same" | "add" | "del"; text: string };

function computeLineDiff(original: string, modified: string): DiffLine[] {
    const a = original.split("\n");
    const b = modified.split("\n");
    const m = a.length, n = b.length;
    const dp: number[][] = Array.from({ length: m + 1 }, () => new Array(n + 1).fill(0));
    for (let i = m - 1; i >= 0; i--) {
        for (let j = n - 1; j >= 0; j--) {
            dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
        }
    }
    const out: DiffLine[] = [];
    let i = 0, j = 0;
    while (i < m && j < n) {
        if (a[i] === b[j]) { out.push({ type: "same", text: a[i] }); i++; j++; }
        else if (dp[i + 1][j] >= dp[i][j + 1]) { out.push({ type: "del", text: a[i] }); i++; }
        else { out.push({ type: "add", text: b[j] }); j++; }
    }
    while (i < m) { out.push({ type: "del", text: a[i] }); i++; }
    while (j < n) { out.push({ type: "add", text: b[j] }); j++; }
    return out;
}

const MAX_DIFF_LINES = 2000; // LCS is O(m×n) — beyond this, skip diffing

function DiffView({ original, modified }: { original: string; modified: string }) {
    const origLines = original.split("\n").length;
    const modLines = modified.split("\n").length;

    if (origLines > MAX_DIFF_LINES || modLines > MAX_DIFF_LINES) {
        return (
            <div className="rounded-md border border-kinetic-border bg-kinetic-surface-container-low font-mono text-xs">
                <div className="px-3 py-1 text-kinetic-on-surface-variant italic border-b border-kinetic-border/50">
                    File too large to diff ({origLines} → {modLines} lines) — showing modified content
                </div>
                <ScrollArea className="max-h-[40vh]">
                    <div className="px-3 py-1 whitespace-pre-wrap text-kinetic-on-surface">{modified}</div>
                </ScrollArea>
            </div>
        );
    }

    const lines = computeLineDiff(original, modified);
    // Collapse runs of >6 unchanged lines into a fold marker.
    const rendered: (DiffLine | { type: "fold"; count: number })[] = [];
    let runStart = -1, runLen = 0;
    const flush = () => {
        if (runLen > 6) {
            rendered.push(lines[runStart], lines[runStart + 1], lines[runStart + 2]);
            rendered.push({ type: "fold", count: runLen - 4 });
            rendered.push(lines[runStart + runLen - 1]);
        } else {
            for (let k = 0; k < runLen; k++) rendered.push(lines[runStart + k]);
        }
        runLen = 0;
    };
    lines.forEach((line, idx) => {
        if (line.type === "same") {
            if (runLen === 0) runStart = idx;
            runLen++;
        } else {
            if (runLen > 0) flush();
            rendered.push(line);
        }
    });
    if (runLen > 0) flush();

    return (
        <div className="rounded-md border border-kinetic-border bg-kinetic-surface-container-low font-mono text-xs overflow-x-auto">
            {rendered.map((line, idx) => {
                if (line.type === "fold") {
                    return (
                        <div key={idx} className="px-3 py-1 text-kinetic-on-surface-variant italic border-y border-kinetic-border/50">
                            ⋯ {line.count} unchanged lines ⋯
                        </div>
                    );
                }
                const cls =
                    line.type === "add"
                        ? "bg-green-500/15 text-green-400"
                        : line.type === "del"
                          ? "bg-red-500/15 text-red-400 line-through"
                          : "text-kinetic-on-surface-variant";
                const prefix = line.type === "add" ? "+ " : line.type === "del" ? "- " : "  ";
                return (
                    <div key={idx} className={`px-3 py-0.5 whitespace-pre-wrap ${cls}`}>
                        {prefix}{line.text}
                    </div>
                );
            })}
        </div>
    );
}

function StaticIssuesCard({ issues }: { issues: ReviewIssue[] }) {
    const [expanded, setExpanded] = useState(false);
    if (issues.length === 0) return null;

    const sorted = [...issues].sort((a, b) => {
        const order = { critical: 0, warning: 1, suggestion: 2 };
        return (order[a.severity as keyof typeof order] ?? 3) -
            (order[b.severity as keyof typeof order] ?? 3);
    });

    return (
        <Card className="border-kinetic-border bg-kinetic-surface-container">
            <button
                onClick={() => setExpanded((v) => !v)}
                className="w-full flex items-center justify-between p-4 text-left"
            >
                <div className="flex items-center gap-2">
                    <span className="text-lg">📊</span>
                    <span className="font-medium text-kinetic-on-surface">
                        Static findings
                    </span>
                    <Badge variant="outline" className="border-kinetic-border text-kinetic-on-surface-variant">
                        {issues.length}
                    </Badge>
                </div>
                {expanded ? (
                    <ChevronUp className="h-4 w-4 text-kinetic-on-surface-variant" />
                ) : (
                    <ChevronDown className="h-4 w-4 text-kinetic-on-surface-variant" />
                )}
            </button>
            {expanded && (
                <CardContent className="pt-0">
                    <ScrollArea className="max-h-[40vh]">
                        <div className="space-y-3">
                            {sorted.map((issue, idx) => (
                                <div
                                    key={idx}
                                    className="p-3 rounded-md border border-kinetic-border bg-kinetic-surface-container-low"
                                >
                                    <div className="flex items-start gap-2">
                                        <span className="text-lg">{SEVERITY_ICONS[issue.severity] || "⚪"}</span>
                                        <div className="flex-1 space-y-1">
                                            <div className="flex items-center gap-2 flex-wrap">
                                                <span className="font-medium text-sm text-kinetic-on-surface">{issue.title}</span>
                                                <Badge className={SEVERITY_STYLES[issue.severity] || ""} variant="secondary">
                                                    {issue.severity}
                                                </Badge>
                                                <Badge variant="outline" className="border-kinetic-border text-kinetic-on-surface-variant">{issue.category}</Badge>
                                            </div>
                                            <p className="text-xs text-kinetic-on-surface-variant">
                                                {issue.file_path}
                                                {issue.line_number ? `:${issue.line_number}` : ""}
                                            </p>
                                            <p className="text-sm text-kinetic-on-surface">{issue.explanation}</p>
                                            {issue.suggestion && (
                                                <p className="text-sm text-kinetic-primary">
                                                    💡 {issue.suggestion}
                                                </p>
                                            )}
                                            {issue.code_snippet && (
                                                <pre className="text-xs p-2 rounded mt-1 overflow-x-auto bg-kinetic-surface-container-high text-kinetic-on-surface border border-kinetic-border">
                                                    {issue.code_snippet}
                                                </pre>
                                            )}
                                        </div>
                                    </div>
                                </div>
                            ))}
                        </div>
                    </ScrollArea>
                </CardContent>
            )}
        </Card>
    );
}

type Tab = "diff" | "file" | "guidelines";

export default function ReviewPage() {
    const { result } = useAnalyze();
    const [tab, setTab] = useState<Tab>("diff");

    // Diff review state
    const [staged, setStaged] = useState(false);
    const [targetBranch, setTargetBranch] = useState("");
    const [issues, setIssues] = useState<ReviewIssue[]>([]);
    const [staticIssues, setStaticIssues] = useState<ReviewIssue[]>([]);
    const [sessionId, setSessionId] = useState<string | null>(null);
    const [stats, setStats] = useState({ files: 0, added: 0, removed: 0 });
    const [diffLoading, setDiffLoading] = useState(false);
    const [diffError, setDiffError] = useState("");
    const [verdicts, setVerdicts] = useState<Record<number, string>>({});
    const [previews, setPreviews] = useState<EditPreview[] | null>(null);
    const [previewsLoading, setPreviewsLoading] = useState(false);
    const [previewsError, setPreviewsError] = useState("");
    const [approved, setApproved] = useState<Record<number, boolean>>({});
    const [applyLoading, setApplyLoading] = useState(false);
    const [applyResult, setApplyResult] = useState<ApplyEditsResponse | null>(null);
    const [applyError, setApplyError] = useState("");

    // File review state
    const [filePath, setFilePath] = useState("");
    const [fileReview, setFileReview] = useState("");
    const [fileStaticIssues, setFileStaticIssues] = useState<ReviewIssue[]>([]);
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
        setStaticIssues([]);
        setSessionId(null);
        setVerdicts({});
        setPreviews(null);
        setPreviewsError("");
        setApproved({});
        setApplyResult(null);
        setApplyError("");
        try {
            const res = await api.reviewDiff(staged, targetBranch || undefined, result?.repo_path);
            setIssues(res.issues);
            setStaticIssues(res.static_issues ?? []);
            setSessionId(res.session_id);
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

    function toggleVerdict(idx: number, verdict: string) {
        setVerdicts((prev) => {
            if (prev[idx] === verdict) {
                const next = { ...prev };
                delete next[idx];
                return next;
            }
            return { ...prev, [idx]: verdict };
        });
    }

    async function handlePreviewEdits() {
        const accepted = Object.entries(verdicts).filter(([, v]) => v === "accepted");
        if (accepted.length === 0 || !sessionId) return;

        setPreviewsLoading(true);
        setPreviewsError("");
        setPreviews(null);
        setApplyResult(null);
        setApplyError("");
        try {
            const verdictPayload: Record<string, string> = {};
            for (const [idx, v] of Object.entries(verdicts)) {
                verdictPayload[idx] = v;
            }
            const res = await api.previewEdits(sessionId, verdictPayload);
            setPreviews(res.previews);
            const initial: Record<number, boolean> = {};
            for (const p of res.previews) {
                if (!p.error && p.modified_content !== null) {
                    initial[p.finding_index] = true;
                }
            }
            setApproved(initial);
        } catch (err) {
            setPreviewsError(err instanceof Error ? err.message : "Preview failed");
        } finally {
            setPreviewsLoading(false);
        }
    }

    async function handleApplyEdits() {
        if (!sessionId || !previews) return;
        const edits = previews
            .filter((p) => approved[p.finding_index] && p.modified_content !== null)
            .map((p) => ({
                finding_index: p.finding_index,
                file_path: p.file_path,
                modified_content: p.modified_content as string,
                original_content: p.original_content,
            }));
        if (edits.length === 0) return;

        setApplyLoading(true);
        setApplyError("");
        setApplyResult(null);
        try {
            const res = await api.applyEdits(sessionId, edits);
            setApplyResult(res);
        } catch (err) {
            setApplyError(err instanceof Error ? err.message : "Apply failed");
        } finally {
            setApplyLoading(false);
        }
    }

    async function handleReviewFile() {
        if (!filePath.trim()) return;
        setFileLoading(true);
        setFileError("");
        setFileReview("");
        setFileStaticIssues([]);
        try {
            const res = await api.reviewFile(filePath.trim(), result?.repo_path);
            setFileStaticIssues(res.static_issues ?? []);
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
                `**Files reviewed:** ${res.files_reviewed} | **Added:** +${res.lines_added} | **Removed:** -${res.lines_removed}`,
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

    const tabButtonClass = (active: boolean) =>
        active
            ? "bg-kinetic-primary text-kinetic-on-primary hover:bg-kinetic-primary/90"
            : "border-kinetic-border bg-kinetic-surface-container text-kinetic-on-surface hover:bg-kinetic-surface-container-high hover:text-kinetic-on-surface";

    const inputClass = "border-kinetic-border bg-kinetic-surface-container text-kinetic-on-surface placeholder:text-kinetic-on-surface-variant focus-visible:ring-kinetic-primary";

    return (
        <div className="p-6 space-y-6 max-w-6xl">
            <h1 className="text-2xl font-bold text-kinetic-on-surface">Code Review</h1>

            {/* Tab bar */}
            <div className="flex gap-2 flex-wrap">
                {[
                    { id: "diff" as Tab, label: "Review Diff", icon: ShieldCheck },
                    { id: "file" as Tab, label: "Review File", icon: FileSearch },
                    { id: "guidelines" as Tab, label: "Guidelines", icon: BookOpen },
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

            {/* ── Diff Review Tab ── */}
            {tab === "diff" && (
                <Card className="border-kinetic-border bg-kinetic-surface-container-low">
                    <CardHeader>
                        <CardTitle className="flex items-center gap-2 text-kinetic-on-surface">
                            <ShieldCheck className="h-5 w-5 text-kinetic-primary" />
                            Review Git Diff
                        </CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        <div className="flex flex-wrap gap-4 items-end">
                            <div className="space-y-1">
                                <label className="text-sm font-medium text-kinetic-on-surface">Target Branch</label>
                                <Input
                                    placeholder="e.g. main (optional)"
                                    value={targetBranch}
                                    onChange={(e) => setTargetBranch(e.target.value)}
                                    className={`${inputClass} w-48`}
                                />
                            </div>
                            <label className="flex items-center gap-2 text-sm cursor-pointer text-kinetic-on-surface-variant">
                                <input
                                    type="checkbox"
                                    checked={staged}
                                    onChange={(e) => setStaged(e.target.checked)}
                                    className="accent-kinetic-primary"
                                />
                                Staged only
                            </label>
                            <Button
                                onClick={handleReviewDiff}
                                disabled={diffLoading}
                                className="bg-kinetic-primary text-kinetic-on-primary hover:bg-kinetic-primary/90"
                            >
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
                            <div className="p-3 bg-kinetic-error/10 text-kinetic-error rounded-md text-sm flex items-start gap-2 border border-kinetic-error/20">
                                <AlertCircle className="h-4 w-4 mt-0.5 shrink-0" />
                                <span className="whitespace-pre-wrap">{diffError}</span>
                            </div>
                        )}

                        {(issues.length > 0 || staticIssues.length > 0 || stats.files > 0) && (
                            <div className="space-y-4">
                                <div className="flex gap-4 text-sm text-kinetic-on-surface-variant">
                                    <span>{stats.files} files reviewed</span>
                                    <span className="text-kinetic-node-config">+{stats.added}</span>
                                    <span className="text-kinetic-error">-{stats.removed}</span>
                                </div>

                                <Separator className="bg-kinetic-border" />

                                <StaticIssuesCard issues={staticIssues} />

                                {issues.length === 0 ? (
                                    <div className="p-4 rounded-md text-sm border border-kinetic-node-config/30 bg-kinetic-node-config/10 text-kinetic-node-config">
                                        ✅ No LLM findings
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
                                                    <Card key={idx} className={`p-3 border-kinetic-border bg-kinetic-surface-container ${verdicts[idx] === "accepted" ? "ring-1 ring-green-500/50" : verdicts[idx] === "rejected" ? "ring-1 ring-red-500/50 opacity-60" : ""}`}>
                                                        <div className="flex items-start gap-2">
                                                            <span className="text-lg">{SEVERITY_ICONS[issue.severity] || "⚪"}</span>
                                                            <div className="flex-1 space-y-1">
                                                                <div className="flex items-center gap-2 flex-wrap">
                                                                    <span className="font-medium text-sm text-kinetic-on-surface">{issue.title}</span>
                                                                    <Badge className={SEVERITY_STYLES[issue.severity] || ""} variant="secondary">
                                                                        {issue.severity}
                                                                    </Badge>
                                                                    <Badge variant="outline" className="border-kinetic-border text-kinetic-on-surface-variant">{issue.category}</Badge>
                                                                </div>
                                                                <p className="text-xs text-kinetic-on-surface-variant">
                                                                    {issue.file_path}
                                                                    {issue.line_number ? `:${issue.line_number}` : ""}
                                                                </p>
                                                                <p className="text-sm text-kinetic-on-surface">{issue.explanation}</p>
                                                                {issue.suggestion && (
                                                                    <p className="text-sm text-kinetic-primary">
                                                                        💡 {issue.suggestion}
                                                                    </p>
                                                                )}
                                                                {issue.code_snippet && (
                                                                    <pre className="text-xs p-2 rounded mt-1 overflow-x-auto bg-kinetic-surface-container-high text-kinetic-on-surface border border-kinetic-border">
                                                                        {issue.code_snippet}
                                                                    </pre>
                                                                )}
                                                                <div className="flex gap-2 mt-2">
                                                                    <Button
                                                                        size="sm"
                                                                        variant={verdicts[idx] === "accepted" ? "default" : "outline"}
                                                                        className={verdicts[idx] === "accepted" ? "bg-green-600 text-white hover:bg-green-700 h-7 text-xs" : "border-green-600 text-green-600 hover:bg-green-600/10 h-7 text-xs"}
                                                                        onClick={() => toggleVerdict(idx, "accepted")}
                                                                    >
                                                                        <Check className="h-3 w-3 mr-1" /> Accept
                                                                    </Button>
                                                                    <Button
                                                                        size="sm"
                                                                        variant={verdicts[idx] === "rejected" ? "default" : "outline"}
                                                                        className={verdicts[idx] === "rejected" ? "bg-red-600 text-white hover:bg-red-700 h-7 text-xs" : "border-red-600 text-red-600 hover:bg-red-600/10 h-7 text-xs"}
                                                                        onClick={() => toggleVerdict(idx, "rejected")}
                                                                    >
                                                                        <X className="h-3 w-3 mr-1" /> Reject
                                                                    </Button>
                                                                </div>
                                                            </div>
                                                        </div>
                                                    </Card>
                                                ))}
                                        </div>
                                    </ScrollArea>
                                )}

                                {issues.length > 0 && (
                                    <>
                                        <Separator className="bg-kinetic-border" />
                                        <div className="flex items-center gap-4">
                                            <Button
                                                onClick={handlePreviewEdits}
                                                disabled={previewsLoading || Object.values(verdicts).filter(v => v === "accepted").length === 0}
                                                className="bg-kinetic-primary text-kinetic-on-primary hover:bg-kinetic-primary/90"
                                            >
                                                {previewsLoading ? (
                                                    <>
                                                        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                                        Generating previews...
                                                    </>
                                                ) : (
                                                    `Preview Changes (${Object.values(verdicts).filter(v => v === "accepted").length} accepted)`
                                                )}
                                            </Button>
                                            <span className="text-xs text-kinetic-on-surface-variant">
                                                {Object.values(verdicts).filter(v => v === "rejected").length} rejected,{" "}
                                                {issues.length - Object.keys(verdicts).length} skipped
                                            </span>
                                        </div>

                                        {previewsError && (
                                            <div className="p-3 bg-kinetic-error/10 text-kinetic-error rounded-md text-sm flex items-start gap-2 border border-kinetic-error/20">
                                                <AlertCircle className="h-4 w-4 mt-0.5 shrink-0" />
                                                <span className="whitespace-pre-wrap">{previewsError}</span>
                                            </div>
                                        )}

                                        {previews && previews.length > 0 && (
                                            <div className="space-y-3">
                                                <div className="text-sm font-medium text-kinetic-on-surface">
                                                    Review the proposed changes before applying:
                                                </div>
                                                {previews.map((p) => (
                                                    <Card key={p.finding_index} className="border-kinetic-border bg-kinetic-surface-container">
                                                        <div className="p-3 flex items-start gap-3">
                                                            {p.error ? (
                                                                <>
                                                                    <X className="h-4 w-4 text-kinetic-error mt-0.5 shrink-0" />
                                                                    <div className="flex-1 text-sm">
                                                                        <span className="font-medium text-kinetic-on-surface">#{p.finding_index} {issues[p.finding_index]?.title ?? p.file_path}</span>
                                                                        <span className="text-kinetic-on-surface-variant"> — could not generate edit: {p.error}</span>
                                                                    </div>
                                                                </>
                                                            ) : (
                                                                <>
                                                                    <input
                                                                        type="checkbox"
                                                                        checked={!!approved[p.finding_index]}
                                                                        onChange={() => setApproved((prev) => ({ ...prev, [p.finding_index]: !prev[p.finding_index] }))}
                                                                        className="mt-1 h-4 w-4 accent-kinetic-primary shrink-0"
                                                                    />
                                                                    <div className="flex-1 space-y-2">
                                                                        <div className="text-sm font-medium text-kinetic-on-surface">
                                                                            #{p.finding_index} {issues[p.finding_index]?.title ?? ""} <span className="text-kinetic-on-surface-variant font-normal">({p.file_path})</span>
                                                                        </div>
                                                                        <DiffView original={p.original_content ?? ""} modified={p.modified_content ?? ""} />
                                                                    </div>
                                                                </>
                                                            )}
                                                        </div>
                                                    </Card>
                                                ))}

                                                <div className="flex items-center gap-4">
                                                    <Button
                                                        onClick={handleApplyEdits}
                                                        disabled={applyLoading || Object.values(approved).filter(Boolean).length === 0}
                                                        className="bg-green-600 text-white hover:bg-green-700"
                                                    >
                                                        {applyLoading ? (
                                                            <>
                                                                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                                                Applying...
                                                            </>
                                                        ) : (
                                                            `Apply Selected (${Object.values(approved).filter(Boolean).length})`
                                                        )}
                                                    </Button>
                                                </div>
                                            </div>
                                        )}

                                        {applyError && (
                                            <div className="p-3 bg-kinetic-error/10 text-kinetic-error rounded-md text-sm flex items-start gap-2 border border-kinetic-error/20">
                                                <AlertCircle className="h-4 w-4 mt-0.5 shrink-0" />
                                                <span className="whitespace-pre-wrap">{applyError}</span>
                                            </div>
                                        )}

                                        {applyResult && (
                                            <Card className="p-4 border-kinetic-border bg-kinetic-surface-container">
                                                <div className="space-y-2 text-sm">
                                                    <div className="font-medium text-kinetic-on-surface">
                                                        {applyResult.verification_passed ? "✅ Verification Passed" : "⚠️ Verification Issues"}
                                                    </div>
                                                    <div className="text-kinetic-on-surface-variant">
                                                        Applied: {applyResult.applied.length} | Failed: {applyResult.failed.length} | SA issues: {applyResult.static_analysis_issues} | Tests: {applyResult.tests_passed ? "✅" : "❌"}
                                                    </div>
                                                    {applyResult.applied.length > 0 && (
                                                        <ul className="list-disc pl-4 text-kinetic-node-config">
                                                            {applyResult.applied.map((a, i) => <li key={i}>{a}</li>)}
                                                        </ul>
                                                    )}
                                                    {applyResult.failed.length > 0 && (
                                                        <ul className="list-disc pl-4 text-kinetic-error">
                                                            {applyResult.failed.map((f, i) => <li key={i}>{f}</li>)}
                                                        </ul>
                                                    )}
                                                </div>
                                            </Card>
                                        )}
                                    </>
                                )}
                            </div>
                        )}
                    </CardContent>
                </Card>
            )}

            {/* ── File Review Tab ── */}
            {tab === "file" && (
                <Card className="border-kinetic-border bg-kinetic-surface-container-low">
                    <CardHeader>
                        <CardTitle className="flex items-center gap-2 text-kinetic-on-surface">
                            <FileSearch className="h-5 w-5 text-kinetic-primary" />
                            Review Single File
                        </CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        <p className="text-sm text-kinetic-on-surface-variant">
                            Review a file against codebase conventions and patterns.
                            Works without a full index — the dependency graph is built on-the-fly if needed.
                        </p>
                        <div className="flex gap-2">
                            <Input
                                placeholder="Path to file (e.g. src/codewalk/pipeline.py)"
                                value={filePath}
                                onChange={(e) => setFilePath(e.target.value)}
                                onKeyDown={(e) => e.key === "Enter" && handleReviewFile()}
                                className={inputClass}
                            />
                            <Button
                                onClick={handleReviewFile}
                                disabled={fileLoading || !filePath.trim()}
                                className="bg-kinetic-primary text-kinetic-on-primary hover:bg-kinetic-primary/90"
                            >
                                {fileLoading ? (
                                    <Loader2 className="h-4 w-4 animate-spin" />
                                ) : (
                                    "Review"
                                )}
                            </Button>
                        </div>

                        {fileError && (
                            <div className="p-3 bg-kinetic-error/10 text-kinetic-error rounded-md text-sm border border-kinetic-error/20">
                                <span className="whitespace-pre-wrap">{fileError}</span>
                            </div>
                        )}

                        {fileReview && (
                            <Card className="p-4 border-kinetic-border bg-kinetic-surface-container">
                                <div className="prose prose-sm dark:prose-invert max-w-none text-kinetic-on-surface-variant">
                                    <ReactMarkdown>{fileReview}</ReactMarkdown>
                                </div>
                            </Card>
                        )}

                        <StaticIssuesCard issues={fileStaticIssues} />
                    </CardContent>
                </Card>
            )}

            {/* ── Guidelines Tab ── */}
            {tab === "guidelines" && (
                <Card className="border-kinetic-border bg-kinetic-surface-container-low">
                    <CardHeader>
                        <CardTitle className="flex items-center gap-2 text-kinetic-on-surface">
                            <BookOpen className="h-5 w-5 text-kinetic-primary" />
                            Load Coding Guidelines
                        </CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        <p className="text-sm text-kinetic-on-surface-variant">
                            Load your team&apos;s coding standards (.md, .txt, .rst, .pdf) so reviews check against them.
                            Reviews automatically use guidelines configured in codewalk.yaml; use this only to load an explicit directory.
                        </p>
                        <div className="flex gap-2">
                            <Input
                                placeholder="Path to guidelines directory (optional)"
                                value={guidelinesPath}
                                onChange={(e) => setGuidelinesPath(e.target.value)}
                                className={inputClass}
                            />
                            <Button
                                onClick={handleLoadGuidelines}
                                disabled={guidelinesLoading}
                                className="bg-kinetic-primary text-kinetic-on-primary hover:bg-kinetic-primary/90"
                            >
                                {guidelinesLoading ? (
                                    <Loader2 className="h-4 w-4 animate-spin" />
                                ) : (
                                    "Load"
                                )}
                            </Button>
                        </div>

                        {guidelinesError && (
                            <div className="p-3 bg-kinetic-error/10 text-kinetic-error rounded-md text-sm border border-kinetic-error/20">
                                <span className="whitespace-pre-wrap">{guidelinesError}</span>
                            </div>
                        )}

                        {guidelinesResult && (
                            <div className="p-3 rounded-md text-sm border border-kinetic-node-config/30 bg-kinetic-node-config/10 text-kinetic-node-config">
                                ✅ {guidelinesResult}
                            </div>
                        )}
                    </CardContent>
                </Card>
            )}

        </div>
    );
}
