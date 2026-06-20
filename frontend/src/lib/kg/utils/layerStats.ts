import type { GraphNode, Layer } from "../types";

const COMPLEXITY_WEIGHT: Record<string, number> = {
  simple: 1,
  moderate: 2,
  complex: 3,
};

export function computeLayerStats(
  layer: Layer,
  nodesById: Map<string, GraphNode>,
): {
  fileCount: number;
  functionCount: number;
  classCount: number;
  aggregateComplexity: "simple" | "moderate" | "complex";
} {
  let fileCount = 0;
  let functionCount = 0;
  let classCount = 0;
  let complexityScore = 0;

  for (const nodeId of layer.nodeIds) {
    const node = nodesById.get(nodeId);
    if (!node) continue;
    if (node.type === "file" || node.type === "config" || node.type === "document") {
      fileCount++;
    } else if (node.type === "function" || node.type === "method") {
      functionCount++;
    } else if (node.type === "class") {
      classCount++;
    }
    complexityScore += COMPLEXITY_WEIGHT[node.complexity] ?? 2;
  }

  const avg = layer.nodeIds.length ? complexityScore / layer.nodeIds.length : 0;
  let aggregateComplexity: "simple" | "moderate" | "complex" = "simple";
  if (avg > 2.3) aggregateComplexity = "complex";
  else if (avg > 1.5) aggregateComplexity = "moderate";

  return { fileCount, functionCount, classCount, aggregateComplexity };
}
