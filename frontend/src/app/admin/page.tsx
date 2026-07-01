"use client";

import { useState, useEffect, useCallback } from "react";
import { api, AdminRepo } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
    Loader2,
    Shield,
    Plus,
    KeyRound,
    RefreshCw,
    CheckCircle2,
    XCircle,
    Clock,
    Play,
    Server,
    Copy,
    Check,
} from "lucide-react";

export default function AdminPage() {
    const [adminKey, setAdminKey] = useState("");
    const [repos, setRepos] = useState<AdminRepo[]>([]);
    const [error, setError] = useState("");
    const [indexingRepo, setIndexingRepo] = useState<string | null>(null);
    const [indexResult, setIndexResult] = useState("");
    const [copied, setCopied] = useState(false);
    const [server, setServer] = useState<{ health: string; version: string; commit: string } | null>(null);

    const [form, setForm] = useState({
        name: "",
        github_url: "",
        branch: "main",
        installation_id: "",
    });
    const [registering, setRegistering] = useState(false);
    const [registerResult, setRegisterResult] = useState("");

    const fetchServerInfo = useCallback(async () => {
        try {
            const [health, version] = await Promise.all([api.health(), api.version()]);
            setServer({
                health: health.status,
                version: version.codewalk_version,
                commit: version.commit_sha,
            });
        } catch {
            setServer(null);
        }
    }, []);

    const fetchRepos = useCallback(async () => {
        if (!adminKey.trim()) return;
        try {
            const data = await api.adminRepos(adminKey);
            setRepos(data.repos);
            setError("");
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to fetch repos");
        }
    }, [adminKey]);

    useEffect(() => {
        fetchServerInfo();
    }, [fetchServerInfo]);

    useEffect(() => {
        if (!adminKey.trim()) return;
        fetchRepos();
        const interval = setInterval(fetchRepos, 5000);
        return () => clearInterval(interval);
    }, [adminKey, fetchRepos]);

    async function handleRegister(e: React.FormEvent) {
        e.preventDefault();
        if (!adminKey.trim()) {
            setError("Admin key is required");
            return;
        }
        setRegistering(true);
        setRegisterResult("");
        setError("");
        try {
            const result = await api.registerRepo(
                adminKey,
                form.name,
                form.github_url,
                form.branch,
                form.installation_id
            );
            setRegisterResult(`Registered ${result.full_name || form.name}. Repo token: ${result.repo_token}`);
            setForm({ name: "", github_url: "", branch: "main", installation_id: "" });
            fetchRepos();
        } catch (err) {
            setError(err instanceof Error ? err.message : "Registration failed");
        } finally {
            setRegistering(false);
        }
    }

    async function handleIndex(repo: AdminRepo) {
        if (!adminKey.trim()) return;
        setIndexingRepo(repo.full_name);
        setIndexResult("");
        setError("");
        try {
            const result = await api.adminIndex(adminKey, repo.full_name, repo.branch);
            setIndexResult(
                `Indexed ${result.repo}: ${result.status}${result.files_scanned ? ` • ${result.files_scanned} files` : ""
                }${result.total_chunks ? ` • ${result.total_chunks} chunks` : ""}`
            );
            fetchRepos();
        } catch (err) {
            setError(err instanceof Error ? err.message : `Index failed for ${repo.full_name}`);
        } finally {
            setIndexingRepo(null);
        }
    }

    function copyToken(token: string) {
        navigator.clipboard.writeText(token);
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
    }

    function statusBadge(status: string | null) {
        if (!status) return <span className="text-kinetic-on-surface-variant text-xs">—</span>;
        const map: Record<string, { icon: React.ReactNode; className: string }> = {
            queued: { icon: <Clock className="h-3 w-3" />, className: "bg-kinetic-tertiary/15 text-kinetic-tertiary border border-kinetic-tertiary/30" },
            running: { icon: <Loader2 className="h-3 w-3 animate-spin" />, className: "bg-kinetic-primary/15 text-kinetic-primary border border-kinetic-primary/30" },
            indexing: { icon: <Loader2 className="h-3 w-3 animate-spin" />, className: "bg-kinetic-primary/15 text-kinetic-primary border border-kinetic-primary/30" },
            done: { icon: <CheckCircle2 className="h-3 w-3" />, className: "bg-kinetic-node-config/15 text-kinetic-node-config border border-kinetic-node-config/30" },
            ready: { icon: <CheckCircle2 className="h-3 w-3" />, className: "bg-kinetic-node-config/15 text-kinetic-node-config border border-kinetic-node-config/30" },
            failed: { icon: <XCircle className="h-3 w-3" />, className: "bg-kinetic-error/15 text-kinetic-error border border-kinetic-error/30" },
        };
        const style = map[status] || { icon: null, className: "bg-kinetic-surface-container-high text-kinetic-on-surface-variant border border-kinetic-border" };
        return (
            <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${style.className}`}>
                {style.icon}
                {status}
            </span>
        );
    }

    const inputClass = "border-kinetic-border bg-kinetic-surface-container text-kinetic-on-surface placeholder:text-kinetic-on-surface-variant focus-visible:ring-kinetic-primary";

    return (
        <div className="p-6 space-y-6 max-w-5xl mx-auto">
            <div className="flex items-center gap-3">
                <Shield className="h-6 w-6 text-kinetic-primary" />
                <h1 className="text-2xl font-bold tracking-tight text-kinetic-on-surface">Cloud Admin</h1>
            </div>

            {server && (
                <Card className="border-kinetic-border bg-kinetic-surface-container-low">
                    <CardHeader>
                        <CardTitle className="text-sm flex items-center gap-2 text-kinetic-on-surface">
                            <Server className="h-4 w-4 text-kinetic-primary" />
                            Server
                        </CardTitle>
                    </CardHeader>
                    <CardContent>
                        <div className="flex flex-wrap gap-4 text-sm">
                            <span className="inline-flex items-center gap-1 text-kinetic-node-config">
                                <CheckCircle2 className="h-3.5 w-3.5" />
                                Health: {server.health}
                            </span>
                            <span className="text-kinetic-on-surface-variant">Version: {server.version}</span>
                            <span className="text-kinetic-on-surface-variant font-mono">Commit: {server.commit}</span>
                        </div>
                    </CardContent>
                </Card>
            )}

            <Card className="border-kinetic-border bg-kinetic-surface-container-low">
                <CardHeader>
                    <CardTitle className="text-sm flex items-center gap-2 text-kinetic-on-surface">
                        <KeyRound className="h-4 w-4 text-kinetic-primary" />
                        Admin Authentication
                    </CardTitle>
                </CardHeader>
                <CardContent>
                    <div className="flex gap-3">
                        <Input
                            type="password"
                            placeholder="Enter X-Admin-Key"
                            value={adminKey}
                            onChange={(e) => setAdminKey(e.target.value)}
                            className={`${inputClass} flex-1`}
                        />
                        <Button
                            variant="outline"
                            onClick={fetchRepos}
                            disabled={!adminKey.trim()}
                            className="border-kinetic-border bg-kinetic-surface-container text-kinetic-on-surface hover:bg-kinetic-surface-container-high hover:text-kinetic-on-surface"
                        >
                            <RefreshCw className="h-4 w-4 mr-1" />
                            Refresh
                        </Button>
                    </div>
                    <p className="text-xs text-kinetic-on-surface-variant mt-2">
                        Set the ADMIN_API_KEY env var on the server, then paste it here.
                    </p>
                </CardContent>
            </Card>

            <Card className="border-kinetic-border bg-kinetic-surface-container-low">
                <CardHeader>
                    <CardTitle className="text-sm flex items-center gap-2 text-kinetic-on-surface">
                        <Plus className="h-4 w-4 text-kinetic-primary" />
                        Register Repository
                    </CardTitle>
                </CardHeader>
                <CardContent>
                    <form onSubmit={handleRegister} className="space-y-3">
                        <div className="grid grid-cols-2 gap-3">
                            <div>
                                <label className="text-xs font-medium text-kinetic-on-surface-variant">Repo Name</label>
                                <Input
                                    placeholder="owner/repo"
                                    value={form.name}
                                    onChange={(e) => setForm({ ...form, name: e.target.value })}
                                    required
                                    className={inputClass}
                                />
                            </div>
                            <div>
                                <label className="text-xs font-medium text-kinetic-on-surface-variant">GitHub URL</label>
                                <Input
                                    placeholder="https://github.com/org/repo"
                                    value={form.github_url}
                                    onChange={(e) => setForm({ ...form, github_url: e.target.value })}
                                    required
                                    className={inputClass}
                                />
                            </div>
                            <div>
                                <label className="text-xs font-medium text-kinetic-on-surface-variant">Branch</label>
                                <Input
                                    placeholder="main"
                                    value={form.branch}
                                    onChange={(e) => setForm({ ...form, branch: e.target.value })}
                                    className={inputClass}
                                />
                            </div>
                            <div>
                                <label className="text-xs font-medium text-kinetic-on-surface-variant">Installation ID</label>
                                <Input
                                    placeholder="12345678"
                                    value={form.installation_id}
                                    onChange={(e) => setForm({ ...form, installation_id: e.target.value })}
                                    className={inputClass}
                                />
                            </div>
                        </div>
                        <Button
                            type="submit"
                            disabled={registering || !adminKey.trim()}
                            className="bg-kinetic-primary text-kinetic-on-primary hover:bg-kinetic-primary/90"
                        >
                            {registering ? (
                                <>
                                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                    Registering...
                                </>
                            ) : (
                                <>
                                    <Plus className="mr-2 h-4 w-4" />
                                    Register Repo
                                </>
                            )}
                        </Button>
                        {registerResult && (
                            <div className="flex items-start gap-2 text-xs text-kinetic-node-config bg-kinetic-node-config/10 p-2 rounded-md border border-kinetic-node-config/30">
                                <span className="flex-1 break-all">{registerResult}</span>
                                <button
                                    type="button"
                                    onClick={() => copyToken(registerResult.split("Repo token: ")[1] || "")}
                                    className="shrink-0"
                                    title="Copy token"
                                >
                                    {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
                                </button>
                            </div>
                        )}
                    </form>
                </CardContent>
            </Card>

            {indexResult && (
                <div className="text-xs text-kinetic-node-config bg-kinetic-node-config/10 p-3 rounded-md border border-kinetic-node-config/30">
                    {indexResult}
                </div>
            )}

            <Card className="border-kinetic-border bg-kinetic-surface-container-low">
                <CardHeader>
                    <CardTitle className="text-sm text-kinetic-on-surface">Registered Repositories</CardTitle>
                </CardHeader>
                <CardContent>
                    {error && (
                        <div className="mb-3 p-3 bg-kinetic-error/10 text-kinetic-error rounded-md text-sm border border-kinetic-error/20">
                            {error}
                        </div>
                    )}

                    {repos.length === 0 ? (
                        <p className="text-sm text-kinetic-on-surface-variant">
                            {adminKey.trim() ? "No repos registered yet." : "Enter admin key to view repos."}
                        </p>
                    ) : (
                        <div className="overflow-x-auto">
                            <table className="w-full text-sm">
                                <thead>
                                    <tr className="border-b border-kinetic-border text-kinetic-on-surface-variant">
                                        <th className="text-left py-2 px-3 font-medium">Repository</th>
                                        <th className="text-left py-2 px-3 font-medium">Branch</th>
                                        <th className="text-left py-2 px-3 font-medium">Index Status</th>
                                        <th className="text-left py-2 px-3 font-medium">Job</th>
                                        <th className="text-left py-2 px-3 font-medium">Commit</th>
                                        <th className="text-left py-2 px-3 font-medium">Finished</th>
                                        <th className="text-left py-2 px-3 font-medium">Error</th>
                                        <th className="text-left py-2 px-3 font-medium">Action</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {repos.map((repo) => (
                                        <tr key={repo.full_name} className="border-b border-kinetic-border last:border-0 hover:bg-kinetic-surface-container/50">
                                            <td className="py-2 px-3 font-medium text-kinetic-on-surface">{repo.full_name}</td>
                                            <td className="py-2 px-3 text-kinetic-on-surface-variant">{repo.branch}</td>
                                            <td className="py-2 px-3">{statusBadge(repo.index_status)}</td>
                                            <td className="py-2 px-3">{statusBadge(repo.job_status)}</td>
                                            <td className="py-2 px-3 font-mono text-xs text-kinetic-on-surface-variant">
                                                {repo.last_indexed_sha ? repo.last_indexed_sha.slice(0, 7) : "—"}
                                            </td>
                                            <td className="py-2 px-3 text-kinetic-on-surface-variant text-xs">
                                                {repo.job_finished
                                                    ? new Date(repo.job_finished).toLocaleString()
                                                    : "—"}
                                            </td>
                                            <td className="py-2 px-3 text-kinetic-error text-xs max-w-xs truncate">
                                                {repo.job_error || "—"}
                                            </td>
                                            <td className="py-2 px-3">
                                                <Button
                                                    size="sm"
                                                    variant="outline"
                                                    onClick={() => handleIndex(repo)}
                                                    disabled={indexingRepo === repo.full_name || !adminKey.trim()}
                                                    className="border-kinetic-border bg-kinetic-surface-container text-kinetic-on-surface hover:bg-kinetic-surface-container-high hover:text-kinetic-on-surface"
                                                >
                                                    {indexingRepo === repo.full_name ? (
                                                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                                    ) : (
                                                        <Play className="h-3.5 w-3.5" />
                                                    )}
                                                    <span className="ml-1">Index</span>
                                                </Button>
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    )}
                </CardContent>
            </Card>
        </div>
    );
}
