"use client";

import { useKgStore } from "@/lib/kg/store";

function getClusterVar(index: number): string {
  return `var(--kg-cluster-${index % 8})`;
}

export function LayerLegend() {
  const layers = useKgStore((s) => s.graph?.layers ?? []);
  if (layers.length === 0) return null;

  return (
    <div className="flex items-center gap-3">
      {layers.slice(0, 6).map((layer, i) => (
        <div key={layer.id} className="flex items-center gap-1.5 whitespace-nowrap">
          <span
            className="w-2 h-2 rounded-full"
            style={{ backgroundColor: getClusterVar(i) }}
          />
          <span className="text-[10px] uppercase tracking-wider text-kg-text-secondary">
            {layer.name}
          </span>
        </div>
      ))}
      {layers.length > 6 && (
        <span className="text-[10px] text-kg-text-muted">+{layers.length - 6} more</span>
      )}
    </div>
  );
}
