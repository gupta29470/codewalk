"use client";

import { useKgStore } from "@/lib/kg/store";
import { ChevronRight } from "lucide-react";

export function Breadcrumb() {
  const graph = useKgStore((s) => s.graph);
  const navigationLevel = useKgStore((s) => s.navigationLevel);
  const activeLayerId = useKgStore((s) => s.activeLayerId);
  const selectedNodeId = useKgStore((s) => s.selectedNodeId);
  const nodesById = useKgStore((s) => s.nodesById);
  const navigateToOverview = useKgStore((s) => s.navigateToOverview);
  const selectNode = useKgStore((s) => s.selectNode);

  if (!graph) return null;

  const activeLayer = graph.layers.find((l) => l.id === activeLayerId);
  const selectedNode = selectedNodeId ? nodesById.get(selectedNodeId) : null;

  return (
    <div className="absolute top-3 left-3 z-10 flex items-center gap-1.5 px-3 py-1.5 rounded-lg kg-glass text-xs">
      <button
        onClick={() => {
          navigateToOverview();
          selectNode(null);
        }}
        className="text-kg-text-secondary hover:text-kg-text-primary transition-colors"
      >
        {graph.project.name || "Project"}
      </button>
      {navigationLevel === "layer-detail" && activeLayer && (
        <>
          <ChevronRight className="w-3 h-3 text-kg-text-muted" />
          <span className="text-kg-accent">{activeLayer.name}</span>
        </>
      )}
      {selectedNode && (
        <>
          <ChevronRight className="w-3 h-3 text-kg-text-muted" />
          <span className="text-kg-text-primary truncate max-w-[200px]">{selectedNode.name}</span>
        </>
      )}
    </div>
  );
}
