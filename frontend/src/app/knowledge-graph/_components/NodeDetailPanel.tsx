"use client";

import { X, FileCode, GitCommit, Link2 } from "lucide-react";
import type { GraphNode, KnowledgeGraph } from "@/lib/kg/types";
import { useKgStore } from "@/lib/kg/store";
import { StatusBadge } from "@/components/kinetic/StatusBadge";
import { CodePreview } from "./CodePreview";
import { cn } from "@/lib/utils";

interface NodeDetailPanelProps {
  node: GraphNode | null;
  graph: KnowledgeGraph;
  onClose: () => void;
}

const typeBorderColors: Record<string, string> = {
  file: "border-kinetic-node-file",
  function: "border-kinetic-node-function",
  class: "border-kinetic-node-class",
  module: "border-kinetic-node-module",
  config: "border-kinetic-node-config",
  document: "border-kinetic-node-document",
  service: "border-kinetic-node-service",
  endpoint: "border-kinetic-node-endpoint",
  table: "border-kinetic-node-endpoint",
  pipeline: "border-kinetic-node-service",
  concept: "border-kinetic-node-class",
};

export function NodeDetailPanel({ node, graph, onClose }: NodeDetailPanelProps) {
  const changedNodeIds = useKgStore((s) => s.changedNodeIds);
  const affectedNodeIds = useKgStore((s) => s.affectedNodeIds);

  function nodeStatus(id: string): "analyzed" | "changed" | "unchanged" | "error" {
    if (changedNodeIds.has(id)) return "changed";
    if (affectedNodeIds.has(id)) return "unchanged"; // affected shown as impacted/unchanged
    return "analyzed";
  }

  if (!node) {
    return (
      <div className="flex h-full w-full flex-col items-center justify-center gap-3 p-6 text-kinetic-on-surface-variant">
        <div className="flex h-10 w-10 items-center justify-center rounded-full bg-kinetic-surface-container-high">
          <GitCommit size={20} className="opacity-60" />
        </div>
        <p className="text-center text-sm">Select a node to inspect</p>
      </div>
    );
  }

  const incoming = graph.edges.filter((e) => e.target === node.id).length;
  const outgoing = graph.edges.filter((e) => e.source === node.id).length;
  const stronglyConnected = graph.edges
    .filter((e) => e.source === node.id)
    .map((e) => graph.nodes.find((n) => n.id === e.target))
    .filter(Boolean) as GraphNode[];

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <div className="flex items-start justify-between border-b border-kinetic-border p-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <FileCode size={16} className="text-kinetic-primary" />
            <h2 className="truncate text-sm font-semibold text-kinetic-on-surface kinetic-font-mono">
              {node.name}
            </h2>
          </div>
          <p className="mt-1 text-xs text-kinetic-on-surface-variant">{node.type}</p>
        </div>
        <button
          onClick={onClose}
          className="rounded p-1 text-kinetic-on-surface-variant hover:bg-kinetic-surface-container-high hover:text-kinetic-on-surface"
        >
          <X size={16} />
        </button>
      </div>

      {/* Status row */}
      <div className="flex gap-2 border-b border-kinetic-border p-4">
        <StatusBadge status={nodeStatus(node.id)} />
        {node.complexity === "complex" && (
          <span className="inline-flex items-center rounded-full bg-kinetic-status-error px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider text-kinetic-status-error-text kinetic-font-mono">
            High Complexity
          </span>
        )}
      </div>

      {/* Metrics */}
      <div className="grid grid-cols-2 gap-3 border-b border-kinetic-border p-4">
        <MetricBox label="Complexity" value={node.complexity} highlight={node.complexity === "complex"} />
        <MetricBox label="Dependencies" value={`${incoming} in / ${outgoing} out`} />
      </div>

      {/* Summary */}
      <div className="border-b border-kinetic-border p-4">
        <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-kinetic-on-surface-variant">
          Automated Insights
        </h3>
        <p className="text-sm leading-relaxed text-kinetic-on-surface-variant">
          {node.summary || "No summary available."}
        </p>
      </div>

      {/* Code preview */}
      <div className="border-b border-kinetic-border p-4">
        <div className="mb-2 flex items-center justify-between">
          <h3 className="text-xs font-semibold uppercase tracking-wider text-kinetic-on-surface-variant">
            Code Preview
          </h3>
          {node.lineRange && (
            <span className="text-[10px] text-kinetic-on-surface-variant kinetic-font-mono">
              lines {node.lineRange[0]}–{node.lineRange[1]}
            </span>
          )}
        </div>
        <CodePreview filePath={node.filePath} lineRange={node.lineRange} />
      </div>

      {/* Strongly connected */}
      <div className="flex-1 overflow-y-auto p-4">
        <h3 className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-kinetic-on-surface-variant">
          <Link2 size={12} />
          Strongly Connected
        </h3>
        {stronglyConnected.length === 0 ? (
          <p className="text-xs text-kinetic-on-surface-variant">No direct connections.</p>
        ) : (
          <ul className="space-y-1">
            {stronglyConnected.slice(0, 8).map((n) => (
              <li
                key={n.id}
                className={cn(
                  "flex items-center gap-2 rounded border-l-2 bg-kinetic-surface-container-low px-2 py-1.5 text-xs kinetic-font-mono",
                  typeBorderColors[n.type] || "border-kinetic-outline",
                )}
              >
                <span className="truncate text-kinetic-on-surface">{n.name}</span>
                <span className="ml-auto text-[10px] uppercase text-kinetic-on-surface-variant">{n.type}</span>
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* Footer */}
      <div className="border-t border-kinetic-border p-3">
        <button className="w-full rounded-md bg-kinetic-primary px-3 py-2 text-xs font-medium text-kinetic-on-primary hover:brightness-110">
          Open in Editor
        </button>
      </div>
    </div>
  );
}

function MetricBox({
  label,
  value,
  highlight,
}: {
  label: string;
  value: React.ReactNode;
  highlight?: boolean;
}) {
  return (
    <div className="rounded-md border border-kinetic-border bg-kinetic-surface-container-low p-3">
      <div className="mb-1 text-[10px] uppercase tracking-wider text-kinetic-on-surface-variant">{label}</div>
      <div
        className={cn(
          "text-base font-semibold kinetic-font-mono",
          highlight ? "text-kinetic-tertiary" : "text-kinetic-on-surface",
        )}
      >
        {value}
      </div>
    </div>
  );
}
