"use client";

import { useEffect, useRef, useState, useMemo, useCallback } from "react";
import { useKgStore } from "@/lib/kg/store";
import { fuzzySearch } from "@/lib/kg/utils/search";
import { Search, X, Sparkles, Fingerprint } from "lucide-react";
import { getNodeColor } from "@/lib/kg/types";

interface SemanticHit {
  nodeId: string;
  score: number;
}

async function semanticSearch(query: string): Promise<SemanticHit[]> {
  try {
    const res = await fetch("/api/semantic-search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, n_results: 10 }),
    });
    if (!res.ok) return [];
    const data = (await res.json()) as {
      results?: { metadata?: { file_path?: string; node_id?: string }; distance?: number }[];
    };
    const hits: SemanticHit[] = [];
    const seen = new Set<string>();
    for (const r of data.results ?? []) {
      const nodeId = r.metadata?.node_id ?? r.metadata?.file_path;
      if (!nodeId || seen.has(nodeId)) continue;
      seen.add(nodeId);
      const score = r.distance !== undefined ? 1 - Math.min(1, Math.max(0, r.distance)) : 0.8;
      hits.push({ nodeId, score });
    }
    return hits;
  } catch {
    return [];
  }
}

export function SearchBar() {
  const graph = useKgStore((s) => s.graph);
  const query = useKgStore((s) => s.searchQuery);
  const setQuery = useKgStore((s) => s.setSearchQuery);
  const setResults = useKgStore((s) => s.setSearchResults);
  const searchMode = useKgStore((s) => s.searchMode);
  const setSearchMode = useKgStore((s) => s.setSearchMode);
  const selectNode = useKgStore((s) => s.selectNode);
  const navigateToNodeInLayer = useKgStore((s) => s.navigateToNodeInLayer);
  const nodesById = useKgStore((s) => s.nodesById);

  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const [semanticLoading, setSemanticLoading] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  const fuzzyResults = useMemo(() => {
    if (!graph || !query.trim() || searchMode !== "fuzzy") return [];
    return fuzzySearch(graph, query, 8);
  }, [graph, query, searchMode]);

  const [semanticResults, setSemanticResults] = useState<SemanticHit[]>([]);

  const runSemanticSearch = useCallback(async () => {
    if (!query.trim() || searchMode !== "semantic") {
      setSemanticResults([]);
      return;
    }
    setSemanticLoading(true);
    const hits = await semanticSearch(query);
    // Try to resolve node IDs from file paths
    const resolved: SemanticHit[] = [];
    for (const hit of hits) {
      if (nodesById.has(hit.nodeId)) {
        resolved.push(hit);
        continue;
      }
      // Find node by file path
      for (const node of Array.from(nodesById.values())) {
        if (node.filePath && hit.nodeId.includes(node.filePath)) {
          resolved.push({ nodeId: node.id, score: hit.score });
          break;
        }
      }
    }
    setSemanticResults(resolved);
    setSemanticLoading(false);
  }, [query, searchMode, nodesById]);

  useEffect(() => {
    const timer = setTimeout(() => {
      runSemanticSearch();
    }, 300);
    return () => clearTimeout(timer);
  }, [runSemanticSearch]);

  const results = searchMode === "semantic" ? semanticResults : fuzzyResults;

  useEffect(() => {
    setResults(results);
    setActiveIndex(0);
  }, [results, setResults]);

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIndex((i) => Math.min(i + 1, results.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIndex((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      const selected = results[activeIndex];
      if (selected) {
        selectNode(selected.nodeId);
        navigateToNodeInLayer(selected.nodeId);
        setOpen(false);
        inputRef.current?.blur();
      }
    } else if (e.key === "Escape") {
      setOpen(false);
      inputRef.current?.blur();
    }
  };

  return (
    <div ref={containerRef} className="relative shrink-0 z-30">
      <div className="flex items-center px-4 py-2 bg-kg-surface border-b border-kg-border-subtle gap-3">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-kg-text-muted" />
          <input
            ref={inputRef}
            data-testid="search-input"
            type="text"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setOpen(true);
            }}
            onFocus={() => setOpen(true)}
            onKeyDown={handleKeyDown}
            placeholder="Search nodes, files, symbols..."
            className="w-full pl-9 pr-32 py-2 bg-kg-elevated border border-kg-border-subtle rounded-lg text-sm text-kg-text-primary placeholder:text-kg-text-muted focus:outline-none focus:border-kg-accent/50"
          />
          <div className="absolute right-2 top-1/2 -translate-y-1/2 flex items-center gap-1">
            {query && (
              <button
                onClick={() => {
                  setQuery("");
                  setOpen(false);
                  inputRef.current?.focus();
                }}
                className="p-1 text-kg-text-muted hover:text-kg-text-primary"
              >
                <X className="w-3.5 h-3.5" />
              </button>
            )}
            <div className="flex items-center bg-kg-elevated rounded-md border border-kg-border-subtle p-0.5">
              <button
                type="button"
                onClick={() => setSearchMode("fuzzy")}
                className={`flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-medium transition-colors ${
                  searchMode === "fuzzy"
                    ? "bg-kg-accent/20 text-kg-accent"
                    : "text-kg-text-muted hover:text-kg-text-secondary"
                }`}
                title="Fuzzy search"
              >
                <Fingerprint className="w-3 h-3" />
                Fuzzy
              </button>
              <button
                type="button"
                onClick={() => setSearchMode("semantic")}
                className={`flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-medium transition-colors ${
                  searchMode === "semantic"
                    ? "bg-kg-accent/20 text-kg-accent"
                    : "text-kg-text-muted hover:text-kg-text-secondary"
                }`}
                title="Semantic search"
              >
                <Sparkles className="w-3 h-3" />
                Semantic
              </button>
            </div>
          </div>
        </div>
      </div>

      {open && (query.trim() || semanticLoading) && (
        <div className="absolute left-4 right-4 top-full mt-1 kg-glass-heavy rounded-lg shadow-2xl overflow-hidden animate-kg-fade-slide-in">
          <div className="px-3 py-2 border-b border-kg-border-subtle text-[10px] uppercase tracking-wider text-kg-text-muted flex items-center justify-between">
            <span>
              {semanticLoading ? "Searching..." : `${results.length} result${results.length !== 1 ? "s" : ""}`}
            </span>
            <span className="capitalize">{searchMode}</span>
          </div>
          {results.map((result, i) => {
            const node = nodesById.get(result.nodeId);
            if (!node) return null;
            const isActive = i === activeIndex;
            return (
              <button
                key={result.nodeId}
                onClick={() => {
                  selectNode(result.nodeId);
                  navigateToNodeInLayer(result.nodeId);
                  setOpen(false);
                }}
                onMouseEnter={() => setActiveIndex(i)}
                className={`w-full text-left px-3 py-2 flex items-center gap-3 transition-colors ${
                  isActive ? "bg-kg-accent/10" : "hover:bg-kg-elevated"
                }`}
              >
                <span
                  className="w-2 h-2 rounded-full shrink-0"
                  style={{ backgroundColor: getNodeColor(node.type) }}
                />
                <div className="flex-1 min-w-0">
                  <div className="text-sm text-kg-text-primary truncate">{node.name}</div>
                  <div className="text-[10px] text-kg-text-muted capitalize">
                    {node.type} · {node.complexity}
                  </div>
                </div>
                <div className="w-16 h-1 bg-kg-elevated rounded-full overflow-hidden">
                  <div
                    className="h-full bg-kg-accent"
                    style={{ width: `${Math.round(result.score * 100)}%` }}
                  />
                </div>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
