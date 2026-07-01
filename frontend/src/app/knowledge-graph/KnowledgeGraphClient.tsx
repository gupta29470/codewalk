"use client";

import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Loader2, PanelRight, X } from "lucide-react";

import { loadKnowledgeGraph } from "@/lib/kg/api";
import { useKgStore } from "@/lib/kg/store";
import { useIsMobile } from "@/components/kg/useIsMobile";
import { cn } from "@/lib/utils";
import { NetworkGraph } from "./_components/NetworkGraph";
import { NodeDetailPanel } from "./_components/NodeDetailPanel";
import { SettingsPanel } from "./_components/SettingsPanel";

async function loadDiffOverlay(): Promise<{ changedNodeIds: string[]; affectedNodeIds: string[] } | null> {
  try {
    const res = await fetch("/api/diff-overlay");
    if (!res.ok) return null;
    return (await res.json()) as { changedNodeIds: string[]; affectedNodeIds: string[] };
  } catch {
    return null;
  }
}

type AccentTheme = "blue" | "purple" | "gold" | "green";

export default function KnowledgeGraphClient() {
  const graph = useKgStore((s) => s.graph);
  const setGraph = useKgStore((s) => s.setGraph);
  const setDiffOverlay = useKgStore((s) => s.setDiffOverlay);
  const selectedNodeId = useKgStore((s) => s.selectedNodeId);
  const selectNode = useKgStore((s) => s.selectNode);

  const [loading, setLoading] = useState(!graph);
  const [error, setError] = useState<string | null>(null);
  const loadStartedRef = useRef(false);
  const searchParams = useSearchParams();
  const repoPath = searchParams.get("repoPath") || undefined;

  const isMobile = useIsMobile();
  const [detailOpen, setDetailOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [theme, setTheme] = useState<AccentTheme>("blue");

  useEffect(() => {
    if (repoPath && graph && graph.project.repoPath !== repoPath) {
      setGraph(null);
      setLoading(true);
      loadStartedRef.current = false;
      return;
    }
    if (graph) {
      setLoading(false);
      return;
    }
    if (loadStartedRef.current) return;
    loadStartedRef.current = true;

    let done = false;
    const timeoutId = window.setTimeout(() => {
      if (done) return;
      setError("Loading timed out. Please refresh or check the browser console.");
      setLoading(false);
    }, 10000);

    Promise.all([loadKnowledgeGraph(repoPath), loadDiffOverlay()])
      .then(([result, diff]) => {
        done = true;
        if (result.error) {
          setError(result.error);
        } else if (result.graph) {
          setGraph(result.graph);
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
      <div className="flex h-full w-full flex-col items-center justify-center gap-3 bg-kinetic-root text-kinetic-on-surface-variant">
        <Loader2 className="h-8 w-8 animate-spin text-kinetic-primary" />
        <span className="text-sm">Loading knowledge graph...</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex h-full w-full items-center justify-center bg-kinetic-root p-6">
        <div className="w-full max-w-md rounded-md border border-kinetic-error/30 bg-kinetic-surface-container-low p-6">
          <h1 className="mb-2 font-semibold text-kinetic-error">Failed to load graph</h1>
          <p className="whitespace-pre-wrap text-sm text-kinetic-on-surface-variant">{error}</p>
        </div>
      </div>
    );
  }

  if (!graph) {
    return (
      <div className="flex h-full w-full items-center justify-center bg-kinetic-root text-kinetic-on-surface-variant">
        No graph data.
      </div>
    );
  }

  const selectedNode = selectedNodeId ? graph.nodes.find((n) => n.id === selectedNodeId) ?? null : null;
  const showMobileDetail = isMobile && detailOpen;

  return (
    <div className={cn("relative flex h-full w-full", `kinetic-accent-${theme}`)}>
      {/* Main graph area */}
      <div className="relative flex-1 min-w-0">
        {isMobile && (
          <button
            onClick={() => setDetailOpen(true)}
            className="absolute right-4 top-16 z-10 rounded-md border border-kinetic-border bg-kinetic-surface-container p-1.5 text-kinetic-on-surface-variant hover:bg-kinetic-surface-container-high hover:text-kinetic-on-surface"
            title="Show details"
          >
            <PanelRight size={16} />
          </button>
        )}

        <NetworkGraph
          graph={graph}
          selectedNodeId={selectedNodeId}
          onSelectNode={selectNode}
          onOpenSettings={() => setSettingsOpen(true)}
        />

        {/* Mobile detail drawer */}
        {showMobileDetail && (
          <>
            <div className="fixed inset-0 z-40 bg-black/50" onClick={() => setDetailOpen(false)} />
            <div className="fixed inset-y-0 right-0 z-50 w-[320px] border-l border-kinetic-border bg-kinetic-surface-container-low shadow-2xl">
              <div className="flex h-12 items-center justify-end border-b border-kinetic-border px-3">
                <button
                  onClick={() => setDetailOpen(false)}
                  className="rounded p-1 text-kinetic-on-surface-variant hover:bg-kinetic-surface-container-high hover:text-kinetic-on-surface"
                >
                  <X size={18} />
                </button>
              </div>
              <div className="h-[calc(100%-3rem)] overflow-y-auto">
                <NodeDetailPanel node={selectedNode} graph={graph} onClose={() => selectNode(null)} />
              </div>
            </div>
          </>
        )}

        {/* Settings panel */}
        {settingsOpen && (
          <>
            <div className="fixed inset-0 z-40 bg-black/50" onClick={() => setSettingsOpen(false)} />
            <div className="fixed inset-y-0 right-0 z-50 w-[320px] border-l border-kinetic-border bg-kinetic-surface-container-low shadow-2xl">
              <SettingsPanel theme={theme} onChangeTheme={setTheme} onClose={() => setSettingsOpen(false)} />
            </div>
          </>
        )}
      </div>

      {/* Desktop detail panel */}
      {selectedNode && !isMobile && (
        <aside className="hidden lg:flex w-[380px] flex-shrink-0 overflow-y-auto border-l border-kinetic-border bg-kinetic-surface-container-low">
          <NodeDetailPanel node={selectedNode} graph={graph} onClose={() => selectNode(null)} />
        </aside>
      )}
    </div>
  );
}
