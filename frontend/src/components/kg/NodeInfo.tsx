"use client";

import { useKgStore, getSelectedNode } from "@/lib/kg/store";
import { getNodeColor } from "@/lib/kg/types";
import { Code, FileCode, ArrowLeft, CornerDownRight, Activity } from "lucide-react";

export function NodeInfo() {
  const store = useKgStore();
  const node = getSelectedNode(store);
  const graph = store.graph;
  const selectNode = store.selectNode;
  const navigateToNodeInLayer = store.navigateToNodeInLayer;
  const openCodeViewer = store.openCodeViewer;
  const nodeHistory = store.nodeHistory;
  const diffMode = store.diffMode;
  const changedNodeIds = store.changedNodeIds;
  const affectedNodeIds = store.affectedNodeIds;

  if (!node || !graph) {
    return (
      <div className="p-4 text-sm text-kg-text-muted">
        Select a node to view details.
      </div>
    );
  }

  const color = getNodeColor(node.type);

  const outEdges = graph.edges.filter((e) => e.source === node.id);
  const inEdges = graph.edges.filter((e) => e.target === node.id);

  const children = graph.edges
    .filter((e) => e.source === node.id && e.type === "contains")
    .map((e) => graph.nodes.find((n) => n.id === e.target))
    .filter(Boolean);

  return (
    <div className="p-4 space-y-4 animate-kg-fade-slide-in">
      {/* Header */}
      <div>
        <div className="flex items-center gap-2 mb-2">
          <span
            className="text-[10px] uppercase tracking-wider font-semibold px-1.5 py-0.5 rounded border"
            style={{ color, borderColor: `${color}40`, backgroundColor: `${color}10` }}
          >
            {node.type}
          </span>
          <span className="text-[10px] uppercase tracking-wider text-kg-text-muted px-1.5 py-0.5 rounded bg-kg-elevated">
            {node.complexity}
          </span>
          {diffMode && (
            <span
              className={`text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded ${
                changedNodeIds.has(node.id)
                  ? "bg-kg-diff-changed/20 text-kg-diff-changed"
                  : affectedNodeIds.has(node.id)
                  ? "bg-kg-diff-affected/20 text-kg-diff-affected"
                  : ""
              }`}
            >
              {changedNodeIds.has(node.id) ? "Changed" : affectedNodeIds.has(node.id) ? "Affected" : ""}
            </span>
          )}
        </div>
        <h2 className="font-heading text-lg text-kg-text-primary break-words">{node.name}</h2>
        {node.filePath && (
          <p className="text-[11px] text-kg-text-muted font-mono mt-1 break-all">{node.filePath}</p>
        )}
      </div>

      {/* Summary */}
      {node.summary && (
        <p className="text-sm text-kg-text-secondary leading-relaxed">{node.summary}</p>
      )}

      {/* Tags */}
      {node.tags.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {node.tags.map((tag) => (
            <span
              key={tag}
              className="text-[10px] px-2 py-0.5 rounded-full bg-kg-elevated text-kg-text-secondary border border-kg-border-subtle"
            >
              {tag}
            </span>
          ))}
        </div>
      )}

      {/* Metrics */}
      {node.metrics && (
        <div className="grid grid-cols-2 gap-2">
          {node.metrics.sizeBytes ? (
            <Metric label="Size" value={`${(node.metrics.sizeBytes / 1024).toFixed(1)} KB`} />
          ) : null}
          {node.metrics.importCount !== undefined ? (
            <Metric label="Imports" value={String(node.metrics.importCount)} />
          ) : null}
          {node.metrics.importerCount !== undefined ? (
            <Metric label="Imported by" value={String(node.metrics.importerCount)} />
          ) : null}
          {node.metrics.pageRank !== undefined ? (
            <Metric label="PageRank" value={node.metrics.pageRank.toFixed(4)} />
          ) : null}
          {node.metrics.betweenness !== undefined ? (
            <Metric label="Betweenness" value={node.metrics.betweenness.toFixed(2)} />
          ) : null}
        </div>
      )}

      {/* Actions */}
      <div className="flex items-center gap-2">
        {node.filePath && (
          <button
            onClick={() => openCodeViewer(node.id)}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-kg-accent/15 text-kg-accent hover:bg-kg-accent/25 transition-colors text-xs font-medium"
          >
            <Code className="w-3.5 h-3.5" />
            View source
          </button>
        )}
        <button
          onClick={() => selectNode(null)}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-kg-elevated text-kg-text-secondary hover:text-kg-text-primary transition-colors text-xs font-medium"
        >
          <ArrowLeft className="w-3.5 h-3.5" />
          Back
        </button>
      </div>

      {/* Connections */}
      <div className="space-y-3">
        {inEdges.length > 0 && (
          <ConnectionSection
            title={`Incoming (${inEdges.length})`}
            edges={inEdges}
            direction="incoming"
            nodesById={store.nodesById}
            onSelect={(id) => {
              selectNode(id);
              navigateToNodeInLayer(id);
            }}
          />
        )}
        {outEdges.length > 0 && (
          <ConnectionSection
            title={`Outgoing (${outEdges.length})`}
            edges={outEdges}
            direction="outgoing"
            nodesById={store.nodesById}
            onSelect={(id) => {
              selectNode(id);
              navigateToNodeInLayer(id);
            }}
          />
        )}
      </div>

      {/* Children */}
      {children.length > 0 && (
        <div>
          <h3 className="text-[10px] uppercase tracking-wider text-kg-text-muted mb-2">
            Contains ({children.length})
          </h3>
          <div className="space-y-1">
            {children.slice(0, 20).map((child) => (
              <button
                key={child!.id}
                onClick={() => {
                  selectNode(child!.id);
                  navigateToNodeInLayer(child!.id);
                }}
                className="w-full text-left flex items-center gap-2 px-2 py-1.5 rounded bg-kg-elevated hover:bg-kg-panel text-xs text-kg-text-secondary hover:text-kg-text-primary transition-colors"
              >
                <CornerDownRight className="w-3 h-3 text-kg-text-muted" />
                <FileCode className="w-3 h-3" style={{ color: getNodeColor(child!.type) }} />
                <span className="truncate">{child!.name}</span>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* History */}
      {nodeHistory.length > 0 && (
        <div>
          <h3 className="text-[10px] uppercase tracking-wider text-kg-text-muted mb-2">History</h3>
          <div className="space-y-1">
            {nodeHistory.slice(0, 3).map((id) => {
              const n = store.nodesById.get(id);
              if (!n) return null;
              return (
                <button
                  key={id}
                  onClick={() => {
                    selectNode(id);
                    navigateToNodeInLayer(id);
                  }}
                  className="w-full text-left px-2 py-1.5 rounded bg-kg-elevated hover:bg-kg-panel text-xs text-kg-text-secondary hover:text-kg-text-primary transition-colors truncate"
                >
                  {n.name}
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="px-2 py-2 rounded bg-kg-elevated border border-kg-border-subtle">
      <div className="text-xs text-kg-text-primary font-medium">{value}</div>
      <div className="text-[9px] uppercase tracking-wider text-kg-text-muted">{label}</div>
    </div>
  );
}

function ConnectionSection({
  title,
  edges,
  direction,
  nodesById,
  onSelect,
}: {
  title: string;
  edges: { source: string; target: string; type: string }[];
  direction: "incoming" | "outgoing";
  nodesById: Map<string, { id: string; name: string; type: string }>;
  onSelect: (id: string) => void;
}) {
  const grouped = edges.reduce((acc, edge) => {
    const otherId = direction === "incoming" ? edge.source : edge.target;
    const key = edge.type;
    if (!acc[key]) acc[key] = [];
    acc[key].push(otherId);
    return acc;
  }, {} as Record<string, string[]>);

  return (
    <div>
      <h3 className="text-[10px] uppercase tracking-wider text-kg-text-muted mb-2 flex items-center gap-1">
        <Activity className="w-3 h-3" />
        {title}
      </h3>
      <div className="space-y-2">
        {Object.entries(grouped).map(([type, ids]) => (
          <div key={type}>
            <div className="text-[10px] text-kg-accent mb-1 capitalize">{type.replace(/_/g, " ")}</div>
            <div className="space-y-0.5">
              {ids.slice(0, 8).map((id) => {
                const n = nodesById.get(id);
                if (!n) return null;
                return (
                  <button
                    key={id}
                    onClick={() => onSelect(id)}
                    className="w-full text-left px-2 py-1 rounded text-xs text-kg-text-secondary hover:text-kg-text-primary hover:bg-kg-elevated transition-colors truncate"
                  >
                    {n.name}
                  </button>
                );
              })}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
