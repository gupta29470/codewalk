"use client";

import { memo } from "react";
import { Handle, Position, type Node } from "@xyflow/react";

export interface LayerClusterNodeData extends Record<string, unknown> {
  layerId: string;
  layerName: string;
  layerDescription?: string;
  fileCount: number;
  aggregateComplexity?: string;
  layerColorIndex: number;
  searchMatchCount?: number;
}

export type LayerClusterFlowNode = Node<LayerClusterNodeData, "layer-cluster">;

function getClusterVar(index: number): string {
  return `var(--kg-cluster-${index % 8})`;
}

function LayerClusterNodeRaw({ data }: { id: string; data: LayerClusterNodeData }) {
  const color = getClusterVar(data.layerColorIndex);

  return (
    <div
      className="relative w-[260px] rounded-xl border bg-kg-surface overflow-hidden cursor-pointer select-none shadow-[0_4px_16px_rgba(0,0,0,0.35)] hover:shadow-[0_6px_24px_rgba(0,0,0,0.45)] transition-shadow"
      style={{ borderColor: `color-mix(in srgb, ${color} 25%, transparent)` }}
    >
      <Handle type="target" position={Position.Top} className="!bg-transparent !border-0" />
      <div className="h-1.5 w-full" style={{ backgroundColor: color }} />
      <div className="p-4">
        <div className="flex items-start justify-between gap-2">
          <h3 className="font-heading text-base text-kg-text-primary truncate">{data.layerName}</h3>
        </div>
        {data.layerDescription && (
          <p className="mt-1.5 text-[11px] text-kg-text-secondary line-clamp-2 leading-snug">
            {data.layerDescription}
          </p>
        )}
        <div className="mt-3 flex items-center gap-3">
          {data.aggregateComplexity && (
            <span
              className="text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded border"
              style={{ color, borderColor: `${color}40`, backgroundColor: `${color}10` }}
            >
              {data.aggregateComplexity}
            </span>
          )}
        </div>
      </div>
      <Handle type="source" position={Position.Bottom} className="!bg-transparent !border-0" />
    </div>
  );
}

export const LayerClusterNode = memo(LayerClusterNodeRaw);
