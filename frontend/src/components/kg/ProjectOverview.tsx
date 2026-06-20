"use client";

import { useKgStore } from "@/lib/kg/store";
import { FileCode, GitBranch, Layers, Boxes, CircleDot } from "lucide-react";

export function ProjectOverview() {
  const graph = useKgStore((s) => s.graph);
  const startTour = useKgStore((s) => s.startTour);

  if (!graph) {
    return (
      <div className="p-4 text-sm text-kg-text-muted">
        Loading knowledge graph...
      </div>
    );
  }

  const stats = graph.stats ?? {
    files: graph.nodes.filter((n) => n.type === "file").length,
    imports: graph.edges.filter((e) => e.type === "imports").length,
    symbols: graph.nodes.filter((n) => ["function", "class", "method"].includes(n.type)).length,
    symbol_calls: graph.edges.filter((e) => e.type === "calls").length,
    chunks: 0,
    modules: graph.layers.length,
    nodeCount: graph.nodes.length,
    edgeCount: graph.edges.length,
    layerCount: graph.layers.length,
    moduleDepCount: graph.edges.filter((e) => e.type === "module_dep").length,
  };

  return (
    <div className="p-4 space-y-5 animate-kg-fade-slide-in">
      <div>
        <h2 className="font-heading text-lg text-kg-text-primary">{graph.project.name}</h2>
        <p className="text-xs text-kg-text-muted mt-1">
          {graph.project.languages.filter(Boolean).join(", ")}
          {graph.project.frameworks?.length ? ` · ${graph.project.frameworks.join(", ")}` : ""}
        </p>
        {graph.project.description && (
          <p className="text-sm text-kg-text-secondary mt-3 leading-relaxed">
            {graph.project.description}
          </p>
        )}
        <p className="text-xs text-kg-text-muted mt-2">
          Use the search and filters to navigate dependencies, layers, and symbols. Click any layer to explore its files and paths.
        </p>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <StatCard icon={FileCode} label="Files" value={stats.files ?? graph.nodes.filter((n) => n.type === "file").length} />
        <StatCard icon={GitBranch} label="Edges" value={stats.edgeCount ?? graph.edges.length} />
        <StatCard icon={Layers} label="Layers" value={stats.layerCount ?? graph.layers.length} />
        <StatCard icon={Boxes} label="Nodes" value={stats.nodeCount ?? graph.nodes.length} />
      </div>

      <div>
        <h3 className="text-[10px] uppercase tracking-wider text-kg-text-muted mb-2">Layers</h3>
        <div className="space-y-1.5">
          {graph.layers.map((layer) => (
            <button
              key={layer.id}
              onClick={() => useKgStore.getState().drillIntoLayer(layer.id)}
              className="w-full text-left px-3 py-2 rounded-lg bg-kg-elevated hover:bg-kg-panel border border-kg-border-subtle transition-colors"
            >
              <div className="flex items-center justify-between">
                <span className="text-sm text-kg-text-primary">{layer.name}</span>
                <span className="text-[10px] text-kg-text-muted">{layer.nodeIds.length}</span>
              </div>
              <p className="text-[10px] text-kg-text-secondary truncate mt-0.5">{layer.description}</p>
            </button>
          ))}
        </div>
      </div>

      {graph.tour && graph.tour.length > 0 && (
        <button
          onClick={() => startTour(0)}
          className="w-full flex items-center justify-center gap-2 px-4 py-2 rounded-lg bg-kg-accent/15 text-kg-accent hover:bg-kg-accent/25 transition-colors text-sm font-medium"
        >
          <CircleDot className="w-4 h-4" />
          Start guided tour
        </button>
      )}
    </div>
  );
}

function StatCard({
  icon: Icon,
  label,
  value,
}: {
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  value: number;
}) {
  return (
    <div className="px-3 py-3 rounded-lg bg-kg-elevated border border-kg-border-subtle">
      <Icon className="w-4 h-4 text-kg-accent mb-2" />
      <div className="text-lg font-heading text-kg-text-primary">{value.toLocaleString()}</div>
      <div className="text-[10px] uppercase tracking-wider text-kg-text-muted">{label}</div>
    </div>
  );
}
