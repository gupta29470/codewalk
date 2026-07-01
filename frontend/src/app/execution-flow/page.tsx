"use client";

import { useEffect, useState } from "react";
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  Controls,
  useNodesState,
  useEdgesState,
  useReactFlow,
  type Node,
  type Edge,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import ReactMarkdown from "react-markdown";

import { api, ExecutionFlowResponse, ExecutionFlowNode, ExecutionFlowEdge } from "@/lib/api";
import { useAnalyze } from "@/lib/analyze-context";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { FlowNode, FlowNodeData } from "./_components/FlowNode";
import { Loader2, RefreshCw, Maximize2, Minimize2, X } from "lucide-react";
import { Button } from "@/components/ui/button";

const nodeTypes = { flow: FlowNode };

function toFlowNode(node: ExecutionFlowNode): Node<FlowNodeData> {
  return {
    id: node.id,
    type: "flow",
    position: { x: node.x, y: node.y },
    data: {
      label: node.name,
      fullPath: node.full_path,
    },
  };
}

function toFlowEdge(edge: ExecutionFlowEdge, index: number): Edge {
  return {
    id: `${edge.source}-${edge.target}-${index}`,
    source: edge.source,
    target: edge.target,
    type: "smoothstep",
    style: { stroke: "var(--kinetic-outline-variant)", strokeWidth: 1.5 },
    animated: true,
  };
}

function ExecutionFlowGraphInner({
  data,
  heightClass = "h-[60vh]",
  expanded = false,
}: {
  data: ExecutionFlowResponse;
  heightClass?: string;
  expanded?: boolean;
}) {
  const [nodes, setNodes, onNodesChange] = useNodesState<Node<FlowNodeData>>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const { fitView } = useReactFlow();

  useEffect(() => {
    const flowNodes = data.nodes.map(toFlowNode);
    const flowEdges = data.edges.map(toFlowEdge);
    setNodes(flowNodes);
    setEdges(flowEdges);
    if (!expanded) {
      window.setTimeout(() => fitView({ padding: 0.15 }), 50);
    }
  }, [data, setNodes, setEdges, fitView, expanded]);

  return (
    <div className={cn("kinetic-graph w-full rounded-md border border-kinetic-border bg-kinetic-root", heightClass)}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        nodeTypes={nodeTypes}
        fitView={!expanded}
        minZoom={0.2}
        maxZoom={2}
        panOnScroll={expanded}
        zoomOnScroll={expanded}
        panOnDrag={expanded}
        defaultViewport={expanded ? { x: 40, y: 40, zoom: 0.85 } : undefined}
        attributionPosition="bottom-right"
        proOptions={{ hideAttribution: true }}
      >
        <Background color="var(--kinetic-outline-variant)" gap={20} size={1} />
        <Controls />
      </ReactFlow>
    </div>
  );
}

function ExecutionFlowGraph({
  data,
  heightClass = "h-[60vh]",
  expanded = false,
}: {
  data: ExecutionFlowResponse;
  heightClass?: string;
  expanded?: boolean;
}) {
  return (
    <ReactFlowProvider>
      <ExecutionFlowGraphInner data={data} heightClass={heightClass} expanded={expanded} />
    </ReactFlowProvider>
  );
}

// Small re-export of cn to avoid extra import file churn
import { cn } from "@/lib/utils";

export default function ExecutionFlowPage() {
  const { cache, setCache } = useAnalyze();
  const [flow, setFlow] = useState<ExecutionFlowResponse | null>(cache.executionFlow);
  const [loading, setLoading] = useState(!cache.executionFlow);
  const [error, setError] = useState("");
  const [expanded, setExpanded] = useState(false);

  function fetchData() {
    setLoading(true);
    setError("");
    api
      .getExecutionFlow()
      .then((res) => {
        setFlow(res);
        setCache("executionFlow", res);
      })
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    if (cache.executionFlow) return;
    fetchData();
  }, []);

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-full p-6">
        <Loader2 className="h-8 w-8 animate-spin text-kinetic-primary" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-6">
        <div className="p-4 bg-kinetic-error/10 text-kinetic-error rounded-md border border-kinetic-error/20">
          {error}
        </div>
      </div>
    );
  }

  if (!flow) return null;

  return (
    <div className="p-6 space-y-6 max-w-7xl">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-kinetic-on-surface">Execution Flow</h1>
          <p className="text-kinetic-on-surface-variant mt-1">
            Entry points flow left → right through dependencies
          </p>
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={fetchData}
          disabled={loading}
          className="border-kinetic-border bg-kinetic-surface-container text-kinetic-on-surface hover:bg-kinetic-surface-container-high hover:text-kinetic-on-surface"
        >
          <RefreshCw className={`h-4 w-4 mr-1 ${loading ? "animate-spin" : ""}`} />
          Refresh
        </Button>
      </div>

      <Card className="border-kinetic-border bg-kinetic-surface-container-low">
        <CardHeader>
          <CardTitle className="flex items-center justify-between text-kinetic-on-surface">
            <span>Execution Flow Graph</span>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setExpanded(true)}
              className="border-kinetic-border bg-kinetic-surface-container text-kinetic-on-surface hover:bg-kinetic-surface-container-high hover:text-kinetic-on-surface"
            >
              <Maximize2 className="h-4 w-4 mr-1" />
              Expand
            </Button>
          </CardTitle>
        </CardHeader>
        <CardContent>
          <ExecutionFlowGraph data={flow} />
        </CardContent>
      </Card>

      {flow.narration && (
        <Card className="border-kinetic-border bg-kinetic-surface-container-low">
          <CardHeader>
            <CardTitle className="text-kinetic-on-surface">How This Code Runs</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="prose prose-sm max-w-none dark:prose-invert execution-flow-narrative">
              <ReactMarkdown>{flow.narration}</ReactMarkdown>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Fullscreen modal */}
      {expanded && (
        <div className="fixed inset-0 z-50 flex flex-col bg-kinetic-root">
          <div className="flex h-12 items-center justify-between border-b border-kinetic-border bg-kinetic-surface-container-low px-4">
            <h2 className="text-sm font-semibold text-kinetic-on-surface">Execution Flow Graph</h2>
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={fetchData}
                disabled={loading}
                className="border-kinetic-border bg-kinetic-surface-container text-kinetic-on-surface hover:bg-kinetic-surface-container-high hover:text-kinetic-on-surface"
              >
                <RefreshCw className={`h-4 w-4 mr-1 ${loading ? "animate-spin" : ""}`} />
                Refresh
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => setExpanded(false)}
                className="border-kinetic-border bg-kinetic-surface-container text-kinetic-on-surface hover:bg-kinetic-surface-container-high hover:text-kinetic-on-surface"
              >
                <Minimize2 className="h-4 w-4 mr-1" />
                Close
              </Button>
              <button
                onClick={() => setExpanded(false)}
                className="rounded p-1 text-kinetic-on-surface-variant hover:bg-kinetic-surface-container-high hover:text-kinetic-on-surface"
              >
                <X size={18} />
              </button>
            </div>
          </div>
          <div className="flex-1 overflow-hidden p-4">
            <ExecutionFlowGraph data={flow} heightClass="h-full" expanded />
          </div>
        </div>
      )}
    </div>
  );
}
