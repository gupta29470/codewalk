import type { Edge, Node } from "@xyflow/react";
import {
  forceSimulation,
  forceManyBody,
  forceLink,
  forceCenter,
  forceCollide,
  forceX,
  forceY,
} from "d3-force";
import type { SimulationNodeDatum } from "d3-force";

export const NODE_WIDTH = 220;
export const NODE_HEIGHT = 90;
export const LAYER_CLUSTER_WIDTH = 280;
export const LAYER_CLUSTER_HEIGHT = 160;
export const PORTAL_NODE_WIDTH = 180;
export const PORTAL_NODE_HEIGHT = 70;
export const CONTAINER_HEADER_HEIGHT = 44;
export const CONTAINER_PADDING = 24;

export const ELK_DEFAULT_LAYOUT_OPTIONS = {
  "elk.algorithm": "layered",
  "elk.direction": "DOWN",
  "elk.spacing.nodeNodeBetweenLayers": "80",
  "elk.spacing.nodeNode": "60",
  "elk.layered.spacing.nodeNodeBetweenLayers": "80",
  "elk.edgeRouting": "ORTHOGONAL",
  "elk.layered.crossingMinimization.strategy": "LAYER_SWEEP",
  "elk.layered.nodePlacement.strategy": "NETWORK_SIMPLEX",
  "elk.layered.considerModelOrder": "NODES_AND_EDGES",
};

export interface ElkChild {
  id: string;
  width: number;
  height: number;
  x?: number;
  y?: number;
  children?: ElkChild[];
}

export interface ElkEdge {
  id: string;
  sources: string[];
  targets: string[];
}

export interface ElkInput {
  id: string;
  layoutOptions: Record<string, string>;
  children: ElkChild[];
  edges: ElkEdge[];
}

export interface ElkOutput {
  id: string;
  x?: number;
  y?: number;
  width?: number;
  height?: number;
  children?: ElkChild[];
}

export function nodesToElkInput(
  nodes: Node[],
  edges: Edge[],
  dims: Map<string, { width: number; height: number }>,
): ElkInput {
  return {
    id: "root",
    layoutOptions: ELK_DEFAULT_LAYOUT_OPTIONS,
    children: nodes.map((n) => {
      const dim = dims.get(n.id) ?? { width: NODE_WIDTH, height: NODE_HEIGHT };
      return { id: n.id, width: dim.width, height: dim.height };
    }),
    edges: edges.map((e) => ({
      id: e.id,
      sources: [e.source],
      targets: [e.target],
    })),
  };
}

export function mergeElkPositions(
  nodes: Node[],
  positioned: ElkOutput,
): Node[] {
  const posMap = new Map<string, { x: number; y: number }>();
  for (const ch of positioned.children ?? []) {
    if (typeof ch.x === "number" && typeof ch.y === "number") {
      posMap.set(ch.id, { x: ch.x, y: ch.y });
    }
  }
  return nodes.map((n) => ({
    ...n,
    position: posMap.get(n.id) ?? n.position,
  }));
}

export function applyForceLayout(
  nodes: Node[],
  edges: Edge[],
  dims: Map<string, { width: number; height: number }>,
  communityMap?: Map<string, number>,
): { nodes: Node[]; width: number; height: number } {
  const width = 1200;
  const height = 900;

  type SimNode = SimulationNodeDatum & {
    id: string;
    width: number;
    height: number;
    community: number;
  };
  type SimLink = SimulationNodeDatum & { source: string; target: string };

  const simulationNodes: SimNode[] = nodes.map((n) => ({
    id: n.id,
    x: n.position.x,
    y: n.position.y,
    width: dims.get(n.id)?.width ?? NODE_WIDTH,
    height: dims.get(n.id)?.height ?? NODE_HEIGHT,
    community: communityMap?.get(n.id) ?? 0,
  }));

  const simulationLinks: SimLink[] = edges.map((e) => ({
    source: e.source,
    target: e.target,
  }));

  const simulation = forceSimulation<SimNode>(simulationNodes)
    .force(
      "charge",
      forceManyBody<SimNode>().strength((d) => {
        const area = d.width * d.height;
        return area > NODE_WIDTH * NODE_HEIGHT * 1.5 ? -600 : -350;
      }),
    )
    .force(
      "link",
      forceLink<SimNode, SimLink>(simulationLinks)
        .id((d) => d.id)
        .distance((d) => {
          const source = simulationNodes.find((n) => n.id === d.source);
          const target = simulationNodes.find((n) => n.id === d.target);
          const sArea = (source?.width ?? NODE_WIDTH) * (source?.height ?? NODE_HEIGHT);
          const tArea = (target?.width ?? NODE_WIDTH) * (target?.height ?? NODE_HEIGHT);
          return Math.sqrt(sArea + tArea) * 0.35 + 80;
        }),
    )
    .force("center", forceCenter(width / 2, height / 2))
    .force(
      "collide",
      forceCollide<SimNode>().radius((d) => Math.max(d.width, d.height) / 1.8),
    )
    .force("x", forceX(width / 2).strength(0.05))
    .force("y", forceY(height / 2).strength(0.05));

  if (communityMap && communityMap.size > 1) {
    const communityCount = new Set(communityMap.values()).size;
    simulation
      .force(
        "communityX",
        forceX<SimNode>((d) => {
          const angle = (d.community / communityCount) * Math.PI * 2;
          return width / 2 + Math.cos(angle) * width * 0.3;
        }).strength(0.12),
      )
      .force(
        "communityY",
        forceY<SimNode>((d) => {
          const angle = (d.community / communityCount) * Math.PI * 2;
          return height / 2 + Math.sin(angle) * height * 0.3;
        }).strength(0.12),
      );
  }

  simulation.stop();
  for (let i = 0; i < 300; i++) simulation.tick();

  const outNodes = nodes.map((n) => {
    const sn = simulationNodes.find((x) => x.id === n.id);
    return {
      ...n,
      position: { x: sn?.x ?? n.position.x, y: sn?.y ?? n.position.y },
    };
  });

  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const n of simulationNodes) {
    const w = n.width;
    const h = n.height;
    const x = n.x ?? 0;
    const y = n.y ?? 0;
    minX = Math.min(minX, x - w / 2);
    minY = Math.min(minY, y - h / 2);
    maxX = Math.max(maxX, x + w / 2);
    maxY = Math.max(maxY, y + h / 2);
  }

  return {
    nodes: outNodes,
    width: maxX - minX || width,
    height: maxY - minY || height,
  };
}
