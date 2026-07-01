import { applyForceLayout } from "@/lib/kg/utils/layout";
import type { Node, Edge } from "@xyflow/react";

self.onmessage = (event: MessageEvent<{
  nodes: Node[];
  edges: Edge[];
  dimsArray: [string, { width: number; height: number }][];
}>) => {
  const { nodes, edges, dimsArray } = event.data;
  const dims = new Map<string, { width: number; height: number }>(dimsArray);
  const { nodes: positioned } = applyForceLayout(nodes, edges, dims);
  self.postMessage({ positioned });
};
