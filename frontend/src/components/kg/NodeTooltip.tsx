"use client";

import { useEffect, useState } from "react";
import { useKgStore } from "@/lib/kg/store";
import { getNodeColor } from "@/lib/kg/types";
import { buildAdjacencyList } from "@/lib/kg/utils/edgeAggregation";

export function NodeTooltip() {
  const graph = useKgStore((s) => s.graph);
  const nodesById = useKgStore((s) => s.nodesById);
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const [pos, setPos] = useState({ x: 0, y: 0 });

  useEffect(() => {
    function handleMouseMove(e: MouseEvent) {
      const target = e.target as HTMLElement | null;
      const nodeEl = target?.closest("[data-id]");
      if (nodeEl) {
        const id = nodeEl.getAttribute("data-id");
        if (id && nodesById.has(id)) {
          setHoveredId(id);
          setPos({ x: e.clientX + 16, y: e.clientY + 16 });
          return;
        }
      }
      setHoveredId(null);
    }
    window.addEventListener("mousemove", handleMouseMove);
    return () => window.removeEventListener("mousemove", handleMouseMove);
  }, [nodesById]);

  if (!hoveredId || !graph) return null;
  const node = nodesById.get(hoveredId);
  if (!node) return null;

  const { outgoing, incoming } = buildAdjacencyList(graph);
  const inCount = incoming.get(hoveredId)?.length ?? 0;
  const outCount = outgoing.get(hoveredId)?.length ?? 0;
  const color = getNodeColor(node.type);

  return (
    <div
      className="fixed z-[100] pointer-events-none kg-glass px-3 py-2 rounded-lg shadow-xl max-w-xs animate-kg-fade-slide-in"
      style={{ left: pos.x, top: pos.y }}
    >
      <div className="flex items-center gap-2 mb-1">
        <span className="w-2 h-2 rounded-full" style={{ backgroundColor: color }} />
        <span className="text-[10px] uppercase tracking-wider text-kg-text-muted capitalize">
          {node.type}
        </span>
        {node.complexity && (
          <span className="text-[10px] text-kg-text-muted">· {node.complexity}</span>
        )}
      </div>
      <div className="text-sm font-medium text-kg-text-primary mb-1">{node.name}</div>
      <div className="text-[10px] text-kg-text-secondary flex items-center gap-3 mb-1.5">
        <span>in {inCount}</span>
        <span>out {outCount}</span>
        <span>total {inCount + outCount}</span>
      </div>
      {node.summary && (
        <p className="text-[11px] text-kg-text-secondary line-clamp-3 leading-snug">{node.summary}</p>
      )}
      {node.tags.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-1">
          {node.tags.slice(0, 3).map((tag) => (
            <span key={tag} className="text-[9px] px-1.5 py-0.5 rounded bg-kg-elevated text-kg-text-muted">
              {tag}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
