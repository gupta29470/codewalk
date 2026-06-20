"use client";

import { useEffect, useState, Suspense, lazy, useRef } from "react";
import { useSearchParams } from "next/navigation";
import { loadKnowledgeGraph } from "@/lib/kg/api";
import { useKgStore } from "@/lib/kg/store";
import { DashboardShell } from "@/components/kg/DashboardShell";
import GraphView from "@/components/kg/GraphView";
import KnowledgeGraphView from "@/components/kg/KnowledgeGraphView";
import { NodeTooltip } from "@/components/kg/NodeTooltip";
import { Loader2 } from "lucide-react";

const CodeViewer = lazy(() => import("@/components/kg/CodeViewer"));

async function loadDiffOverlay(): Promise<{ changedNodeIds: string[]; affectedNodeIds: string[] } | null> {
  try {
    const res = await fetch("/api/diff-overlay");
    if (!res.ok) return null;
    return (await res.json()) as { changedNodeIds: string[]; affectedNodeIds: string[] };
  } catch {
    return null;
  }
}

export default function KnowledgeGraphClient() {
  const graph = useKgStore((s) => s.graph);
  const setGraph = useKgStore((s) => s.setGraph);
  const setDiffOverlay = useKgStore((s) => s.setDiffOverlay);
  const viewMode = useKgStore((s) => s.viewMode);
  const codeViewerOpen = useKgStore((s) => s.codeViewerOpen);
  const [loading, setLoading] = useState(!graph);
  const [error, setError] = useState<string | null>(null);
  const loadStartedRef = useRef(false);
  const searchParams = useSearchParams();
  const repoPath = searchParams.get("repoPath") || undefined;

  useEffect(() => {
    // If the user navigated to a different repo, clear the cached graph and reload.
    if (graph && graph.project.repoPath !== repoPath) {
      setGraph(null);
      setLoading(true);
      loadStartedRef.current = false;
      return;
    }
    if (graph) {
      setLoading(false);
      return;
    }
    if (loadStartedRef.current) {
      return;
    }
    loadStartedRef.current = true;

    let done = false;
    const timeoutId = window.setTimeout(() => {
      if (done) return;
      setError("Loading timed out. Please refresh or check browser console for runtime errors.");
      setLoading(false);
    }, 10000);

    Promise.all([loadKnowledgeGraph(repoPath), loadDiffOverlay()])
      .then(([graphResult, diff]) => {
        done = true;
        if (graphResult.error) {
          setError(graphResult.error);
        } else if (graphResult.graph) {
          setGraph(graphResult.graph);
          if (diff) {
            setDiffOverlay(diff.changedNodeIds, diff.affectedNodeIds);
          }
        } else {
          setError("Knowledge graph response was empty.");
        }
      })
      .catch((err) => {
        done = true;
        setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        window.clearTimeout(timeoutId);
        setLoading(false);
      });

    return () => {
      done = true;
      window.clearTimeout(timeoutId);
    };
  }, [graph, repoPath, setGraph, setDiffOverlay]);

  if (loading) {
    return (
      <div className="h-screen w-screen flex flex-col items-center justify-center gap-3 bg-kg-root text-kg-text-muted">
        <Loader2 className="w-8 h-8 animate-spin text-kg-accent" />
        <span className="text-sm">Loading knowledge graph...</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="h-screen w-screen flex items-center justify-center p-6 bg-kg-root">
        <div className="max-w-md w-full kg-glass p-6 rounded-lg border border-kg-diff-changed/30">
          <h1 className="font-heading text-lg text-kg-diff-changed mb-2">Failed to load graph</h1>
          <p className="text-sm text-kg-text-secondary whitespace-pre-wrap">{error}</p>
        </div>
      </div>
    );
  }

  return (
    <DashboardShell>
      {viewMode === "knowledge" ? (
        <KnowledgeGraphView key="knowledge-view" />
      ) : (
        <GraphView key="structural-view" />
      )}
      <NodeTooltip />
      {codeViewerOpen && (
        <Suspense fallback={null}>
          <CodeViewer />
        </Suspense>
      )}
    </DashboardShell>
  );
}
