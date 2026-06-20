"use client";

import { memo } from "react";
import { Handle, Position, type Node } from "@xyflow/react";
import { ArrowRightCircle } from "lucide-react";

export interface PortalNodeData extends Record<string, unknown> {
  targetLayerId: string;
  targetLayerName: string;
  connectionCount: number;
  layerColorIndex: number;
  onNavigate: (layerId: string) => void;
}

export type PortalFlowNode = Node<PortalNodeData, "portal">;

function getClusterVar(index: number): string {
  return `var(--kg-cluster-${index % 8})`;
}

function PortalNodeRaw({ data }: { data: PortalNodeData }) {
  const color = getClusterVar(data.layerColorIndex);
  return (
    <div
      className="w-[160px] rounded-lg border border-dashed bg-kg-surface/80 p-3 cursor-pointer hover:bg-kg-elevated transition-colors"
      style={{ borderColor: `color-mix(in srgb, ${color} 38%, transparent)` }}
      onClick={() => data.onNavigate(data.targetLayerId)}
    >
      <Handle type="target" position={Position.Left} className="!bg-transparent !border-0" />
      <div className="flex items-center gap-2">
        <ArrowRightCircle className="w-4 h-4" style={{ color }} />
        <div className="flex-1 min-w-0">
          <div className="text-xs font-medium text-kg-text-primary truncate">{data.targetLayerName}</div>
          <div className="text-[9px] text-kg-text-muted">{data.connectionCount} links</div>
        </div>
      </div>
      <Handle type="source" position={Position.Right} className="!bg-transparent !border-0" />
    </div>
  );
}

export const PortalNode = memo(PortalNodeRaw);
