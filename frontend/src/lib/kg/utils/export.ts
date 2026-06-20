import type { Edge, Node } from "@xyflow/react";
import { NODE_WIDTH, NODE_HEIGHT } from "./layout";

export function buildSvgFromNodes(nodes: Node[], edges: Edge[]): string {
  if (nodes.length === 0) return "";

  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;

  for (const n of nodes) {
    const w = n.width ?? NODE_WIDTH;
    const h = n.height ?? NODE_HEIGHT;
    minX = Math.min(minX, n.position.x);
    minY = Math.min(minY, n.position.y);
    maxX = Math.max(maxX, n.position.x + w);
    maxY = Math.max(maxY, n.position.y + h);
  }

  const padding = 40;
  const viewBox = `${minX - padding} ${minY - padding} ${maxX - minX + padding * 2} ${maxY - minY + padding * 2}`;
  const width = maxX - minX + padding * 2;
  const height = maxY - minY + padding * 2;

  const nodeRects = nodes
    .map((n) => {
      const w = n.width ?? NODE_WIDTH;
      const h = n.height ?? NODE_HEIGHT;
      const color =
        n.type === "layer-cluster"
          ? "var(--kg-accent)"
          : n.type === "container"
          ? "var(--kg-node-file)"
          : `var(--kg-node-${(n.data as { nodeType?: string }).nodeType ?? "file"})`;
      const label = (n.data as { label?: string }).label ?? n.id;
      return `
        <rect x="${n.position.x}" y="${n.position.y}" width="${w}" height="${h}" rx="8" fill="#111111" stroke="${color}" stroke-width="2" />
        <text x="${n.position.x + 8}" y="${n.position.y + 18}" fill="#f5f0eb" font-size="11" font-family="Inter, sans-serif">${escapeXml(label)}</text>
      `;
    })
    .join("");

  const edgeLines = edges
    .map((e) => {
      const source = nodes.find((n) => n.id === e.source);
      const target = nodes.find((n) => n.id === e.target);
      if (!source || !target) return "";
      const sx = source.position.x + (source.width ?? NODE_WIDTH) / 2;
      const sy = source.position.y + (source.height ?? NODE_HEIGHT) / 2;
      const tx = target.position.x + (target.width ?? NODE_WIDTH) / 2;
      const ty = target.position.y + (target.height ?? NODE_HEIGHT) / 2;
      return `<line x1="${sx}" y1="${sy}" x2="${tx}" y2="${ty}" stroke="rgba(212,165,116,0.35)" stroke-width="1" />`;
    })
    .join("");

  return `
    <svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="${viewBox}" style="background:#0a0a0a">
      <defs>
        <style>
          text { user-select: none; }
        </style>
      </defs>
      ${edgeLines}
      ${nodeRects}
    </svg>
  `;
}

function escapeXml(str: string): string {
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;");
}

export function downloadSvg(svg: string) {
  const blob = new Blob([svg], { type: "image/svg+xml;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "knowledge-graph.svg";
  a.click();
  URL.revokeObjectURL(url);
}

export function downloadPng(svg: string) {
  const parser = new DOMParser();
  const doc = parser.parseFromString(svg, "image/svg+xml");
  const svgEl = doc.documentElement;
  const width = Number.parseInt(svgEl.getAttribute("width") ?? "1200", 10);
  const height = Number.parseInt(svgEl.getAttribute("height") ?? "900", 10);

  const canvas = document.createElement("canvas");
  canvas.width = width * 2;
  canvas.height = height * 2;
  const ctx = canvas.getContext("2d");
  if (!ctx) return;

  const svgBlob = new Blob([svg], { type: "image/svg+xml;charset=utf-8" });
  const url = URL.createObjectURL(svgBlob);
  const img = new Image();
  img.onload = () => {
    ctx.fillStyle = "#0a0a0a";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
    URL.revokeObjectURL(url);
    const pngUrl = canvas.toDataURL("image/png");
    const a = document.createElement("a");
    a.href = pngUrl;
    a.download = "knowledge-graph.png";
    a.click();
  };
  img.src = url;
}
