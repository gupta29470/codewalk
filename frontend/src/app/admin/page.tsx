"use client";

import { useState, useEffect, useCallback } from "react";
import { api, AdminRepo } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Loader2, Shield, Plus, KeyRound, RefreshCw, CheckCircle2, XCircle, Clock } from "lucide-react";

export default function AdminPage() {
    const [adminKey, setAdminKey] = useState("");
    const [repos, setRepos] = useState<AdminRepo[]>([]);
    const [error, setError] = useState("");

    // Register form state
    const [form, setForm] = useState({
        name: "",
        github_url: "",
        branch: "main",
        installation_id: "",
    });
    const [registering, setRegistering] = useState(false);
    const [registerResult, setRegisterResult] = useState("");

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

    // Poll every 5 seconds
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
            setRegisterResult(`Registered! Repo token: ${result.repo_token}`);
            setForm({ name: "", github_url: "", branch: "main", installation_id: "" });
            fetchRepos();
        } catch (err) {
            setError(err instanceof Error ? err.message : "Registration failed");
        } finally {
            setRegistering(false);
        }
    }

    function statusBadge(status: string | null) {
        if (!status) return <span className="text-muted-foreground text-xs">—</span>;
        const map: Record<string, { icon: React.ReactNode; className: string }> = {
            queued: { icon: <Clock className="h-3 w-3" />, className: "bg-yellow-100 text-yellow-700" },
            running: { icon: <Loader2 className="h-3 w-3 animate-spin" />, className: "bg-blue-100 text-blue-700" },
            done: { icon: <CheckCircle2 className="h-3 w-3" />, className: "bg-green-100 text-green-700" },
            failed: { icon: <XCircle className="h-3 w-3" />, className: "bg-red-100 text-red-700" },
        };
        const style = map[status] || { icon: null, className: "bg-gray-100 text-gray-700" };
        return (
            <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${style.className}`}>
                {style.icon}
                {status}
            </span>
        );
    }

    return (
        <div className="max-w-5xl mx-auto space-y-6">
            <div className="flex items-center gap-3">
                <Shield className="h-6 w-6 text-primary" />
                <h1 className="text-2xl font-bold tracking-tight">Cloud Admin</h1>
            </div>

            {/* Admin Key Input */}
            <Card>
                <CardHeader>
                    <CardTitle className="text-sm flex items-center gap-2">
                        <KeyRound className="h-4 w-4" />
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
                            className="flex-1"
                        />
                        <Button variant="outline" onClick={fetchRepos} disabled={!adminKey.trim()}>
                            <RefreshCw className="h-4 w-4 mr-1" />
                            Refresh
                        </Button>
                    </div>
                    <p className="text-xs text-muted-foreground mt-2">
                        Set the ADMIN_API_KEY env var on the server, then paste it here.
                    </p>
                </CardContent>
            </Card>

            {/* Register Repo Form */}
            <Card>
                <CardHeader>
                    <CardTitle className="text-sm flex items-center gap-2">
                        <Plus className="h-4 w-4" />
                        Register Repository
                    </CardTitle>
                </CardHeader>
                <CardContent>
                    <form onSubmit={handleRegister} className="space-y-3">
                        <div className="grid grid-cols-2 gap-3">
                            <div>
                                <label className="text-xs font-medium">Repo Name</label>
                                <Input
                                    placeholder="my-project"
                                    value={form.name}
                                    onChange={(e) => setForm({ ...form, name: e.target.value })}
                                    required
                                />
                            </div>
                            <div>
                                <label className="text-xs font-medium">GitHub URL</label>
                                <Input
                                    placeholder="https://github.com/org/repo"
                                    value={form.github_url}
                                    onChange={(e) => setForm({ ...form, github_url: e.target.value })}
                                    required
                                />
                            </div>
                            <div>
                                <label className="text-xs font-medium">Branch</label>
                                <Input
                                    placeholder="main"
                                    value={form.branch}
                                    onChange={(e) => setForm({ ...form, branch: e.target.value })}
                                />
                            </div>
                            <div>
                                <label className="text-xs font-medium">Installation ID</label>
                                <Input
                                    placeholder="12345678"
                                    value={form.installation_id}
                                    onChange={(e) => setForm({ ...form, installation_id: e.target.value })}
                                />
                            </div>
                        </div>
                        <Button type="submit" disabled={registering || !adminKey.trim()}>
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
                            <div className="text-xs text-green-600 bg-green-50 p-2 rounded-md">
                                {registerResult}
                            </div>
                        )}
                    </form>
                </CardContent>
            </Card>

            {/* Repos Table */}
            <Card>
                <CardHeader>
                    <CardTitle className="text-sm">Registered Repositories</CardTitle>
                </CardHeader>
                <CardContent>
                    {error && (
                        <div className="mb-3 p-3 bg-destructive/10 text-destructive rounded-md text-sm">
                            {error}
                        </div>
                    )}

                    {repos.length === 0 ? (
                        <p className="text-sm text-muted-foreground">
                            {adminKey.trim() ? "No repos registered yet." : "Enter admin key to view repos."}
                        </p>
                    ) : (
                        <div className="overflow-x-auto">
                            <table className="w-full text-sm">
                                <thead>
                                    <tr className="border-b text-muted-foreground">
                                        <th className="text-left py-2 px-3 font-medium">Name</th>
                                        <th className="text-left py-2 px-3 font-medium">Branch</th>
                                        <th className="text-left py-2 px-3 font-medium">Status</th>
                                        <th className="text-left py-2 px-3 font-medium">Commit</th>
                                        <th className="text-left py-2 px-3 font-medium">Finished</th>
                                        <th className="text-left py-2 px-3 font-medium">Error</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {repos.map((repo, idx) => (
                                        <tr key={idx} className="border-b last:border-0 hover:bg-muted/50">
                                            <td className="py-2 px-3 font-medium">{repo.name}</td>
                                            <td className="py-2 px-3 text-muted-foreground">{repo.branch}</td>
                                            <td className="py-2 px-3">{statusBadge(repo.status)}</td>
                                            <td className="py-2 px-3 font-mono text-xs text-muted-foreground">
                                                {repo.commit_sha ? repo.commit_sha.slice(0, 7) : "—"}
                                            </td>
                                            <td className="py-2 px-3 text-muted-foreground text-xs">
                                                {repo.finished_at
                                                    ? new Date(repo.finished_at).toLocaleString()
                                                    : "—"}
                                            </td>
                                            <td className="py-2 px-3 text-red-600 text-xs max-w-xs truncate">
                                                {repo.error || "—"}
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
