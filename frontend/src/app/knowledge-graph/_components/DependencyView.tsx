"use client";

import { ArrowLeft, ArrowRight, GitCommit } from "lucide-react";
import type { GraphNode, KnowledgeGraph } from "@/lib/kg/types";
import { useKgStore } from "@/lib/kg/store";
import { StatusBadge } from "@/components/kinetic/StatusBadge";

interface DependencyViewProps {
  graph: KnowledgeGraph;
  selectedNodeId: string | null;
  onSelectNode: (id: string | null) => void;
}

export function DependencyView({ graph, selectedNodeId, onSelectNode }: DependencyViewProps) {
  const changedNodeIds = useKgStore((s) => s.changedNodeIds);
  const affectedNodeIds = useKgStore((s) => s.affectedNodeIds);

  const statusOf = (id: string) =>
    changedNodeIds.has(id) ? "changed" : affectedNodeIds.has(id) ? "unchanged" : "analyzed";

  if (!selectedNodeId) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 text-kinetic-on-surface-variant">
        <GitCommit size={24} />
        <p className="text-sm">Select a node to see its dependencies</p>
      </div>
    );
  }

  const node = graph.nodes.find((n) => n.id === selectedNodeId);
  if (!node) {
    return (
      <div className="flex h-full items-center justify-center text-kinetic-on-surface-variant">
        Selected node not found.
      </div>
    );
  }

  const outgoing = graph.edges
    .filter((e) => e.source === selectedNodeId)
    .map((e) => graph.nodes.find((n) => n.id === e.target))
    .filter(Boolean) as GraphNode[];

  const incoming = graph.edges
    .filter((e) => e.target === selectedNodeId)
    .map((e) => graph.nodes.find((n) => n.id === e.source))
    .filter(Boolean) as GraphNode[];

  return (
    <div className="h-full overflow-y-auto bg-kinetic-root p-4">
      <div className="mb-4 rounded-md border border-kinetic-border bg-kinetic-surface-container-low p-4">
        <div className="text-xs uppercase tracking-wider text-kinetic-on-surface-variant">Selected</div>
        <div className="mt-1 text-sm font-semibold text-kinetic-on-surface kinetic-font-mono">
          {node.name}
        </div>
        <div className="mt-2">
          <StatusBadge status={statusOf(node.id)} />
        </div>
      </div>

      <DependencySection
        title="Depends on"
        icon={<ArrowRight size={14} />}
        nodes={outgoing}
        statusOf={statusOf}
        onSelect={onSelectNode}
      />

      <DependencySection
        title="Used by"
        icon={<ArrowLeft size={14} />}
        nodes={incoming}
        statusOf={statusOf}
        onSelect={onSelectNode}
      />
    </div>
  );
}

function DependencySection({
  title,
  icon,
  nodes,
  statusOf,
  onSelect,
}: {
  title: string;
  icon: React.ReactNode;
  nodes: GraphNode[];
  statusOf: (id: string) => "analyzed" | "changed" | "unchanged";
  onSelect: (id: string) => void;
}) {
  return (
    <div className="mb-4">
      <h3 className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-kinetic-on-surface-variant">
        {icon}
        {title}
        <span className="ml-1 rounded-full bg-kinetic-surface-container-high px-1.5 py-0.5 text-[10px] text-kinetic-on-surface">
          {nodes.length}
        </span>
      </h3>
      {nodes.length === 0 ? (
        <p className="text-xs text-kinetic-on-surface-variant">No {title.toLowerCase()} nodes.</p>
      ) : (
        <ul className="space-y-1">
          {nodes.map((n) => (
            <li
              key={n.id}
              onClick={() => onSelect(n.id)}
              className="flex cursor-pointer items-center justify-between rounded-md border border-kinetic-border bg-kinetic-surface-container-low px-3 py-2 hover:bg-kinetic-surface-container-high"
            >
              <span className="truncate text-xs kinetic-font-mono text-kinetic-on-surface">{n.name}</span>
              <StatusBadge status={statusOf(n.id)} />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
