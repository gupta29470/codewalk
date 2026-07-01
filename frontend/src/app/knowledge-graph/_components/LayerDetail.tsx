"use client";

import { useState } from "react";
import type { KnowledgeGraph } from "@/lib/kg/types";
import { SegmentedControl } from "@/components/kinetic/SegmentedControl";
import { LayerTree } from "./LayerTree";
import { FolderGraph } from "./FolderGraph";

interface LayerDetailProps {
  graph: KnowledgeGraph;
  selectedNodeId: string | null;
  onSelectNode: (id: string | null) => void;
}

type LayerView = "tree" | "folder-graph";

export function LayerDetail({ graph, selectedNodeId, onSelectNode }: LayerDetailProps) {
  const [view, setView] = useState<LayerView>("tree");

  return (
    <div className="flex h-full w-full flex-col overflow-hidden">
      <div className="flex h-9 items-center justify-between border-b border-kinetic-border bg-kinetic-surface-container-low px-4">
        <SegmentedControl<LayerView>
          options={[
            { value: "tree", label: "Tree View" },
            { value: "folder-graph", label: "Folder Graph" },
          ]}
          value={view}
          onChange={setView}
        />
        <span className="text-xs text-kinetic-on-surface-variant">
          {graph.layers?.length ?? 0} layers
        </span>
      </div>
      <div className="flex-1 min-h-0">
        {view === "tree" ? (
          <LayerTree
            graph={graph}
            selectedNodeId={selectedNodeId}
            onSelectNode={onSelectNode}
          />
        ) : (
          <FolderGraph
            graph={graph}
            selectedNodeId={selectedNodeId}
            onSelectNode={onSelectNode}
          />
        )}
      </div>
    </div>
  );
}
