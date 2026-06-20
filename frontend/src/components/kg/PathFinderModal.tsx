"use client";

import { useMemo, useState } from "react";
import { useKgStore } from "@/lib/kg/store";
import { buildAdjacencyList } from "@/lib/kg/utils/edgeAggregation";
import { X, Search, ArrowRightLeft } from "lucide-react";

type PathMode = "shortest" | "all";

export default function PathFinderModal({ onClose }: { onClose: () => void }) {
  const graph = useKgStore((s) => s.graph);
  const nodesById = useKgStore((s) => s.nodesById);
  const selectNode = useKgStore((s) => s.selectNode);
  const navigateToNodeInLayer = useKgStore((s) => s.navigateToNodeInLayer);

  const [mode, setMode] = useState<PathMode>("shortest");
  const [source, setSource] = useState("");
  const [target, setTarget] = useState("");
  const [sourceQuery, setSourceQuery] = useState("");
  const [targetQuery, setTargetQuery] = useState("");
  const [paths, setPaths] = useState<string[][] | null>(null);

  const nodeOptions = useMemo(() => {
    if (!graph) return [];
    return graph.nodes
      .filter((n) => n.type === "file" || n.type === "module" || n.type === "class" || n.type === "function")
      .sort((a, b) => a.name.localeCompare(b.name));
  }, [graph]);

  const sourceCandidates = useMemo(() => {
    const q = sourceQuery.trim().toLowerCase();
    if (!q) return nodeOptions;
    return nodeOptions.filter(
      (n) => n.name.toLowerCase().includes(q) || n.id.toLowerCase().includes(q),
    );
  }, [nodeOptions, sourceQuery]);

  const targetCandidates = useMemo(() => {
    const q = targetQuery.trim().toLowerCase();
    if (!q) return nodeOptions;
    return nodeOptions.filter(
      (n) => n.name.toLowerCase().includes(q) || n.id.toLowerCase().includes(q),
    );
  }, [nodeOptions, targetQuery]);

  const selectedSource = nodesById.get(source);
  const selectedTarget = nodesById.get(target);

  const findPaths = () => {
    if (!graph || !source || !target) return;

    if (mode === "shortest") {
      const path = findShortestPath(graph, source, target);
      setPaths(path ? [path] : []);
    } else {
      const found = findAllSimplePaths(graph, source, target, { maxDepth: 6, maxPaths: 20 });
      setPaths(found);
    }
  };

  const clear = () => {
    setPaths(null);
    setSource("");
    setTarget("");
    setSourceQuery("");
    setTargetQuery("");
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/65 backdrop-blur-sm p-4">
      <div className="w-full max-w-lg kg-glass-heavy rounded-lg border border-kg-border-medium shadow-2xl p-5 animate-kg-fade-slide-in">
        <div className="flex items-center justify-between mb-4">
          <h2 className="font-heading text-lg">Path Finder</h2>
          <div className="flex items-center gap-2">
            <button
              onClick={clear}
              className="text-[10px] text-kg-text-muted hover:text-kg-text-secondary uppercase tracking-wider"
            >
              Clear
            </button>
            <button onClick={onClose} className="text-kg-text-muted hover:text-kg-text-primary">
              <X className="w-5 h-5" />
            </button>
          </div>
        </div>

        <div className="space-y-3">
          <div>
            <label className="text-[10px] uppercase tracking-wider text-kg-text-muted">Mode</label>
            <select
              value={mode}
              onChange={(e) => {
                setMode(e.target.value as PathMode);
                setPaths(null);
              }}
              className="w-full mt-1 px-3 py-2 bg-kg-elevated border border-kg-border-subtle rounded-lg text-sm text-kg-text-primary"
            >
              <option value="shortest">Shortest path</option>
              <option value="all">All simple paths</option>
            </select>
          </div>

          <NodePicker
            label="Source"
            query={sourceQuery}
            onQueryChange={setSourceQuery}
            selected={selectedSource ?? null}
            onSelect={(id) => {
              setSource(id);
              setSourceQuery("");
            }}
            candidates={sourceCandidates}
          />

          <NodePicker
            label="Target"
            query={targetQuery}
            onQueryChange={setTargetQuery}
            selected={selectedTarget ?? null}
            onSelect={(id) => {
              setTarget(id);
              setTargetQuery("");
            }}
            candidates={targetCandidates}
          />

          <button
            onClick={findPaths}
            disabled={!source || !target}
            className="w-full flex items-center justify-center gap-2 px-4 py-2 rounded-lg bg-kg-accent/15 text-kg-accent hover:bg-kg-accent/25 disabled:opacity-50 disabled:cursor-not-allowed transition-colors text-sm font-medium"
          >
            <Search className="w-4 h-4" />
            Find {mode === "shortest" ? "shortest path" : "all simple paths"}
          </button>
        </div>

        {paths !== null && (
          <div className="mt-4">
            {paths.length === 0 ? (
              <p className="text-sm text-kg-text-muted">No path found.</p>
            ) : (
              <>
                <p className="text-[10px] uppercase tracking-wider text-kg-text-muted mb-2">
                  {paths.length} path{paths.length !== 1 ? "s" : ""} found
                </p>
                <div className="space-y-3 max-h-72 overflow-auto">
                  {paths.map((path, pathIdx) => (
                    <div key={pathIdx} className="rounded-lg bg-kg-elevated border border-kg-border-subtle p-2">
                      <div className="text-[10px] text-kg-text-muted mb-1.5">
                        Path {pathIdx + 1} · {path.length - 1} hop{path.length - 1 !== 1 ? "s" : ""}
                      </div>
                      <div className="space-y-1">
                        {path.map((id, i) => {
                          const n = nodesById.get(id);
                          if (!n) return null;
                          return (
                            <button
                              key={`${pathIdx}-${id}-${i}`}
                              onClick={() => {
                                selectNode(id);
                                navigateToNodeInLayer(id);
                                onClose();
                              }}
                              className="w-full text-left px-3 py-2 rounded bg-kg-panel hover:bg-kg-surface text-sm text-kg-text-secondary hover:text-kg-text-primary transition-colors flex items-center gap-2"
                            >
                              <span className="text-kg-text-muted w-5 shrink-0">{i + 1}.</span>
                              <span className="truncate">{n.name}</span>
                              {i < path.length - 1 && (
                                <ArrowRightLeft className="w-3 h-3 text-kg-text-muted ml-auto shrink-0" />
                              )}
                            </button>
                          );
                        })}
                      </div>
                    </div>
                  ))}
                </div>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function NodePicker({
  label,
  query,
  onQueryChange,
  selected,
  onSelect,
  candidates,
}: {
  label: string;
  query: string;
  onQueryChange: (q: string) => void;
  selected: { id: string; name: string; type: string } | null;
  onSelect: (id: string) => void;
  candidates: { id: string; name: string; type: string }[];
}) {
  return (
    <div>
      <label className="text-[10px] uppercase tracking-wider text-kg-text-muted">{label}</label>
      {selected ? (
        <div className="mt-1 flex items-center justify-between px-3 py-2 bg-kg-elevated border border-kg-border-subtle rounded-lg">
          <span className="text-sm text-kg-text-primary truncate">{selected.name}</span>
          <button
            onClick={() => onSelect("")}
            className="text-kg-text-muted hover:text-kg-text-primary"
          >
            <X className="w-3.5 h-3.5" />
          </button>
        </div>
      ) : (
        <div className="mt-1 space-y-1">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-kg-text-muted" />
            <input
              type="text"
              value={query}
              onChange={(e) => onQueryChange(e.target.value)}
              placeholder={`Search ${label.toLowerCase()} node...`}
              className="w-full pl-9 pr-3 py-2 bg-kg-elevated border border-kg-border-subtle rounded-lg text-sm text-kg-text-primary placeholder:text-kg-text-muted focus:outline-none focus:border-kg-accent/50"
            />
          </div>
          <select
            size={10}
            value=""
            onChange={(e) => onSelect(e.target.value)}
            className="w-full max-h-60 overflow-auto bg-kg-elevated border border-kg-border-subtle rounded-lg text-sm text-kg-text-primary focus:outline-none focus:border-kg-accent/50 p-1"
          >
            <option value="" disabled className="text-kg-text-muted">
              {candidates.length > 0
                ? `${candidates.length} node${candidates.length !== 1 ? "s" : ""} — click to select`
                : "No nodes match"}
            </option>
            {candidates.map((n) => (
              <option key={n.id} value={n.id} className="bg-kg-elevated text-kg-text-secondary py-1">
                {n.name} · {n.type}
              </option>
            ))}
          </select>
        </div>
      )}
    </div>
  );
}

function findShortestPath(
  graph: NonNullable<ReturnType<typeof useKgStore.getState>["graph"]>,
  source: string,
  target: string,
): string[] | null {
  const { outgoing } = buildAdjacencyList(graph);
  if (source === target) return [source];

  const queue = [source];
  const parent = new Map<string, string>();
  const visited = new Set<string>([source]);

  while (queue.length > 0) {
    const curr = queue.shift()!;
    for (const next of outgoing.get(curr) ?? []) {
      if (!visited.has(next)) {
        visited.add(next);
        parent.set(next, curr);
        queue.push(next);
        if (next === target) {
          const path: string[] = [target];
          let c = target;
          while (parent.has(c)) {
            c = parent.get(c)!;
            path.unshift(c);
          }
          return path;
        }
      }
    }
  }
  return null;
}

function findAllSimplePaths(
  graph: NonNullable<ReturnType<typeof useKgStore.getState>["graph"]>,
  source: string,
  target: string,
  options: { maxDepth: number; maxPaths: number },
): string[][] {
  const { outgoing } = buildAdjacencyList(graph);
  const results: string[][] = [];
  const path: string[] = [source];
  const visited = new Set<string>([source]);

  function dfs(curr: string) {
    if (results.length >= options.maxPaths) return;
    if (path.length > options.maxDepth) return;
    if (curr === target) {
      results.push([...path]);
      return;
    }
    for (const next of outgoing.get(curr) ?? []) {
      if (visited.has(next)) continue;
      visited.add(next);
      path.push(next);
      dfs(next);
      path.pop();
      visited.delete(next);
    }
  }

  dfs(source);
  return results;
}
