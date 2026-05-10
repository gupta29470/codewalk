"use client";

import { api, AnalyzeResponse } from "@/lib/api";
import { useAnalyze } from "@/lib/analyze-context";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { FolderOpen, Search, Loader2, CheckCircle2 } from "lucide-react";

export default function HomePage() {
  const {
    repoPath, setRepoPath,
    indexMode, setIndexMode,
    loading, setLoading,
    result, setResult,
    error, setError,
    steps, addStep, setSteps,
    clearCache,
  } = useAnalyze();

  async function handleAnalyze(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError("");
    setResult(null);
    setSteps([]);
    clearCache();

    // Use last folder name as collection name so multiple repos don't collide
    const pathParts = repoPath.replace(/\/+$/, "").split("/");
    const collectionName = pathParts[pathParts.length - 1] || "codebase";

    try {
      await api.analyzeStream(repoPath, indexMode, (event) => {
        if (event.step === "error") {
          setError(event.message);
          return;
        }
        addStep(event.message);
        if (event.step === "done" && event.result) {
          setResult(event.result as AnalyzeResponse);
        }
      }, collectionName);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Analysis failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex items-center justify-center min-h-[80vh]">
      <div className="w-full max-w-lg space-y-6">
        <div className="text-center space-y-2">
          <h1 className="text-4xl font-bold tracking-tight">CODEWALK</h1>
          <p className="text-muted-foreground">
            Understand any codebase in hours, not weeks
          </p>
        </div>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <FolderOpen className="h-5 w-5" />
              Analyze Codebase
            </CardTitle>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleAnalyze} className="space-y-4">
              <div className="space-y-2">
                <label className="text-sm font-medium">Repo Path</label>
                <Input
                  placeholder="/path/to/repo/src (leave empty for default)"
                  value={repoPath}
                  onChange={(e) => setRepoPath(e.target.value)}
                />
                <p className="text-xs text-muted-foreground">
                  Leave empty to use the configured REPO_PATH from the backend
                </p>
              </div>

              <div className="space-y-2">
                <label className="text-sm font-medium">Index Mode</label>
                <div className="flex flex-col gap-3">
                  {[
                    {
                      value: "auto",
                      label: "Skip if indexed",
                      speed: "Fastest",
                      desc: "Uses existing index if available. No re-scanning or re-embedding. Falls back to full index if no data exists.",
                    },
                    {
                      value: "reindex",
                      label: "Update changes",
                      speed: "Fast",
                      desc: "Compares file hashes to detect new, changed, and deleted files. Only re-embeds what changed — skips unchanged files.",
                    },
                    {
                      value: "full",
                      label: "Full rebuild",
                      speed: "Slowest",
                      desc: "Wipes the entire index and re-scans, re-chunks, and re-embeds every file from scratch. Use when something seems off.",
                    },
                  ].map((mode) => (
                    <label key={mode.value} className="flex items-start gap-2 cursor-pointer">
                      <input
                        type="radio"
                        name="indexMode"
                        value={mode.value}
                        checked={indexMode === mode.value}
                        onChange={(e) => setIndexMode(e.target.value)}
                        className="accent-primary mt-1"
                      />
                      <div>
                        <span className="text-sm font-medium">{mode.label}</span>
                        <span className="ml-2 text-xs text-muted-foreground italic">({mode.speed})</span>
                        <p className="text-xs text-muted-foreground">{mode.desc}</p>
                      </div>
                    </label>
                  ))}
                </div>
              </div>

              <Button type="submit" className="w-full" disabled={loading}>
                {loading ? (
                  <>
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    Analyzing...
                  </>
                ) : (
                  <>
                    <Search className="mr-2 h-4 w-4" />
                    Analyze Codebase
                  </>
                )}
              </Button>
            </form>

            {steps.length > 0 && (
              <div className="mt-4 space-y-1">
                {steps.map((step, idx) => (
                  <div
                    key={idx}
                    className="flex items-center gap-2 text-sm text-muted-foreground"
                  >
                    {idx === steps.length - 1 && loading ? (
                      <Loader2 className="h-3 w-3 animate-spin flex-shrink-0" />
                    ) : (
                      <CheckCircle2 className="h-3 w-3 text-green-500 flex-shrink-0" />
                    )}
                    {step}
                  </div>
                ))}
              </div>
            )}

            {error && (
              <div className="mt-4 p-3 bg-destructive/10 text-destructive rounded-md text-sm">
                {error}
              </div>
            )}

            {result && (
              <div className="mt-4 p-3 bg-green-50 dark:bg-green-950 rounded-md text-sm space-y-1">
                <div className="flex items-center gap-2 text-green-700 dark:text-green-300 font-medium">
                  <CheckCircle2 className="h-4 w-4" />
                  Analysis Complete
                </div>
                <p className="text-muted-foreground">
                  {result.files_scanned} files scanned &bull;{" "}
                  {result.chunks_created} chunks &bull;{" "}
                  {result.modules.length} modules
                </p>
                <p className="text-xs text-muted-foreground">
                  Navigate to any tab in the sidebar to explore.
                </p>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
