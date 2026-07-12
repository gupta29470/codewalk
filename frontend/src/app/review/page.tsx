"use client";

import { useState } from "react";
import { api, ReviewIssue, FixItem, ApplyAndVerifyResponse } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { Loader2, ShieldCheck, FileSearch, BookOpen, AlertCircle, Wrench, Plus, Trash2, Check, X } from "lucide-react";
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
    const [verdicts, setVerdicts] = useState<Record<number, string>>({});
    const [verifyLoading, setVerifyLoading] = useState(false);
    const [verifyResult, setVerifyResult] = useState<ApplyAndVerifyResponse | null>(null);
    const [verifyError, setVerifyError] = useState("");

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
        setVerdicts({});
        setVerifyResult(null);
        setVerifyError("");
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

    async function handleApplyAndVerify() {
        const accepted = Object.entries(verdicts).filter(([, v]) => v === "accepted");
        if (accepted.length === 0) return;

        setVerifyLoading(true);
        setVerifyError("");
        setVerifyResult(null);
        try {
            const verdictPayload: Record<string, string> = {};
            for (const [idx, v] of Object.entries(verdicts)) {
                verdictPayload[idx] = v;
            }
            const res = await api.applyAndVerify("", verdictPayload);
            setVerifyResult(res);
        } catch (err) {
            setVerifyError(err instanceof Error ? err.message : "Apply & verify failed");
        } finally {
            setVerifyLoading(false);
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
                    { id: "apply" as Tab, label: "Apply Fixes", icon: Wrench },
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
                            <div className="p-3 bg-kinetic-error/10 text-kinetic-error rounded-md text-sm flex items-center gap-2 border border-kinetic-error/20">
                                <AlertCircle className="h-4 w-4" />
                                {diffError}
                            </div>
                        )}

                        {(issues.length > 0 || summary) && (
                            <div className="space-y-4">
                                <div className="flex gap-4 text-sm text-kinetic-on-surface-variant">
                                    <span>{stats.files} files reviewed</span>
                                    <span className="text-kinetic-node-config">+{stats.added}</span>
                                    <span className="text-kinetic-error">-{stats.removed}</span>
                                </div>

                                <Separator className="bg-kinetic-border" />

                                {issues.length === 0 ? (
                                    <div className="p-4 rounded-md text-sm border border-kinetic-node-config/30 bg-kinetic-node-config/10 text-kinetic-node-config">
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

                                {summary && (
                                    <>
                                        <Separator className="bg-kinetic-border" />
                                        <div className="text-sm text-kinetic-on-surface">
                                            <span className="font-medium">Summary: </span>
                                            {summary}
                                        </div>
                                    </>
                                )}

                                {issues.length > 0 && (
                                    <>
                                        <Separator className="bg-kinetic-border" />
                                        <div className="flex items-center gap-4">
                                            <Button
                                                onClick={handleApplyAndVerify}
                                                disabled={verifyLoading || Object.values(verdicts).filter(v => v === "accepted").length === 0}
                                                className="bg-kinetic-primary text-kinetic-on-primary hover:bg-kinetic-primary/90"
                                            >
                                                {verifyLoading ? (
                                                    <>
                                                        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                                        Applying & Verifying...
                                                    </>
                                                ) : (
                                                    `Apply & Verify (${Object.values(verdicts).filter(v => v === "accepted").length} accepted)`
                                                )}
                                            </Button>
                                            <span className="text-xs text-kinetic-on-surface-variant">
                                                {Object.values(verdicts).filter(v => v === "rejected").length} rejected,{" "}
                                                {issues.length - Object.keys(verdicts).length} skipped
                                            </span>
                                        </div>

                                        {verifyError && (
                                            <div className="p-3 bg-kinetic-error/10 text-kinetic-error rounded-md text-sm flex items-center gap-2 border border-kinetic-error/20">
                                                <AlertCircle className="h-4 w-4" />
                                                {verifyError}
                                            </div>
                                        )}

                                        {verifyResult && (
                                            <Card className="p-4 border-kinetic-border bg-kinetic-surface-container">
                                                <div className="space-y-2 text-sm">
                                                    <div className="font-medium text-kinetic-on-surface">
                                                        {verifyResult.verification_passed ? "✅ Verification Passed" : "⚠️ Verification Issues"}
                                                    </div>
                                                    <div className="text-kinetic-on-surface-variant">
                                                        Applied: {verifyResult.applied.length} | Failed: {verifyResult.failed.length} | SA issues: {verifyResult.static_analysis_issues} | Tests: {verifyResult.tests_passed ? "✅" : "❌"}
                                                    </div>
                                                    {verifyResult.applied.length > 0 && (
                                                        <ul className="list-disc pl-4 text-kinetic-node-config">
                                                            {verifyResult.applied.map((a, i) => <li key={i}>{a}</li>)}
                                                        </ul>
                                                    )}
                                                    {verifyResult.failed.length > 0 && (
                                                        <ul className="list-disc pl-4 text-kinetic-error">
                                                            {verifyResult.failed.map((f, i) => <li key={i}>{f}</li>)}
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
                            Requires the codebase to be indexed first.
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
                                {fileError}
                            </div>
                        )}

                        {fileReview && (
                            <Card className="p-4 border-kinetic-border bg-kinetic-surface-container">
                                <div className="prose prose-sm dark:prose-invert max-w-none text-kinetic-on-surface-variant">
                                    <ReactMarkdown>{fileReview}</ReactMarkdown>
                                </div>
                            </Card>
                        )}
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
                                {guidelinesError}
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

            {/* ── Apply Fixes Tab ── */}
            {tab === "apply" && (
                <Card className="border-kinetic-border bg-kinetic-surface-container-low">
                    <CardHeader>
                        <CardTitle className="flex items-center gap-2 text-kinetic-on-surface">
                            <Wrench className="h-5 w-5 text-kinetic-primary" />
                            Apply Code Fixes
                        </CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        <p className="text-sm text-kinetic-on-surface-variant">
                            Manually specify exact text replacements. Each fix searches for
                            <code className="bg-kinetic-surface-container px-1 rounded text-xs text-kinetic-on-surface mx-1 border border-kinetic-border">old_code</code>
                            exactly once in the file and replaces it with
                            <code className="bg-kinetic-surface-container px-1 rounded text-xs text-kinetic-on-surface mx-1 border border-kinetic-border">new_code</code>.
                            Include surrounding context (2–3 lines) in old_code to avoid ambiguous matches.
                        </p>

                        <div className="space-y-3">
                            {fixes.map((fix) => (
                                <Card key={fix.id} className="p-3 space-y-2 border-kinetic-border bg-kinetic-surface-container">
                                    <div className="flex items-center gap-2">
                                        <Input
                                            placeholder="File path (e.g. src/main.py)"
                                            value={fix.file_path}
                                            onChange={(e) => updateFix(fix.id, "file_path", e.target.value)}
                                            className={`${inputClass} flex-1`}
                                        />
                                        <Button
                                            variant="ghost"
                                            size="sm"
                                            onClick={() => removeFix(fix.id)}
                                            className="text-kinetic-error hover:bg-kinetic-error/10 hover:text-kinetic-error"
                                        >
                                            <Trash2 className="h-4 w-4" />
                                        </Button>
                                    </div>
                                    <Textarea
                                        placeholder="Old code (exact text to find — include surrounding lines for uniqueness)"
                                        value={fix.old_code}
                                        onChange={(e) => updateFix(fix.id, "old_code", e.target.value)}
                                        rows={3}
                                        className="font-mono text-xs border-kinetic-border bg-kinetic-surface-container text-kinetic-on-surface placeholder:text-kinetic-on-surface-variant focus-visible:ring-kinetic-primary"
                                    />
                                    <Textarea
                                        placeholder="New code (replacement text)"
                                        value={fix.new_code}
                                        onChange={(e) => updateFix(fix.id, "new_code", e.target.value)}
                                        rows={3}
                                        className="font-mono text-xs border-kinetic-border bg-kinetic-surface-container text-kinetic-on-surface placeholder:text-kinetic-on-surface-variant focus-visible:ring-kinetic-primary"
                                    />
                                </Card>
                            ))}
                        </div>

                        <div className="flex gap-2">
                            <Button
                                variant="outline"
                                size="sm"
                                onClick={addFix}
                                className="border-kinetic-border bg-kinetic-surface-container text-kinetic-on-surface hover:bg-kinetic-surface-container-high hover:text-kinetic-on-surface"
                            >
                                <Plus className="h-4 w-4 mr-1" />
                                Add Fix
                            </Button>
                            <Button
                                onClick={handleApplyFixes}
                                disabled={applyLoading || fixes.filter((f) => f.file_path.trim() && f.old_code.trim()).length === 0}
                                className="bg-kinetic-primary text-kinetic-on-primary hover:bg-kinetic-primary/90"
                            >
                                {applyLoading ? (
                                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                ) : (
                                    "Apply Fixes"
                                )}
                            </Button>
                        </div>

                        {applyError && (
                            <div className="p-3 bg-kinetic-error/10 text-kinetic-error rounded-md text-sm flex items-center gap-2 border border-kinetic-error/20">
                                <AlertCircle className="h-4 w-4" />
                                {applyError}
                            </div>
                        )}

                        {applyResult && (
                            <div className="p-3 rounded-md text-sm border border-kinetic-node-config/30 bg-kinetic-node-config/10 text-kinetic-node-config">
                                ✅ {applyResult}
                            </div>
                        )}
                    </CardContent>
                </Card>
            )}
        </div>
    );
}
