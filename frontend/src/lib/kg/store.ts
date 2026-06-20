import { create } from "zustand";
import type {
  GraphNode,
  KnowledgeGraph,
  NodeCategory,
  NodeType,
} from "./types";
import { NODE_TYPE_TO_CATEGORY } from "./types";

export type NavigationLevel = "overview" | "layer-detail";
export type ViewMode = "structural" | "knowledge";
export type Persona = "experienced" | "junior" | "non-technical";
export type DetailLevel = "file" | "class";
export type KnowledgeViewFilter = "files" | "functions" | "both";

export interface SearchResult {
  nodeId: string;
  score: number;
}

export interface ContainerLayout {
  childPositions: Map<string, { x: number; y: number }>;
  actualSize: { width: number; height: number };
}

export interface LayoutIssue {
  message: string;
  level: "warning" | "error";
}

interface DashboardState {
  // Graph data
  graph: KnowledgeGraph | null;
  nodesById: Map<string, GraphNode>;
  nodeIdToLayerId: Map<string, string>;
  nodeIdToLayerIds: Map<string, string[]>;

  // Navigation
  navigationLevel: NavigationLevel;
  activeLayerId: string | null;
  viewMode: ViewMode;
  isKnowledgeGraph: boolean;

  // Selection / focus
  selectedNodeId: string | null;
  focusNodeId: string | null;
  nodeHistory: string[];

  // Search
  searchQuery: string;
  searchResults: SearchResult[];
  searchMode: "fuzzy" | "semantic";

  // Knowledge view
  knowledgeViewFilter: KnowledgeViewFilter;

  // Filters
  nodeTypeFilters: Record<NodeCategory, boolean>;
  detailLevel: DetailLevel;
  showFunctionsInClassView: boolean;
  filterPanelOpen: boolean;

  // Containers
  expandedContainers: Set<string>;
  containerLayoutCache: Map<string, ContainerLayout>;
  containerSizeMemory: Map<string, { width: number; height: number }>;
  stage1Tick: number;
  pendingFocusContainer: string | null;

  // Persona
  persona: Persona;

  // Diff
  diffMode: boolean;
  changedNodeIds: Set<string>;
  affectedNodeIds: Set<string>;

  // Code viewer
  codeViewerOpen: boolean;
  codeViewerNodeId: string | null;
  codeViewerExpanded: boolean;

  // Tour
  tourActive: boolean;
  currentTourStep: number;
  tourHighlightedNodeIds: string[];

  // UI
  exportMenuOpen: boolean;
  pathFinderOpen: boolean;
  pendingExportFormat: "png" | "svg" | null;
  layoutIssues: LayoutIssue[];
}

interface DashboardActions {
  setGraph: (graph: KnowledgeGraph | null) => void;
  selectNode: (id: string | null) => void;
  setFocusNode: (id: string | null) => void;
  clearFocus: () => void;
  drillIntoLayer: (layerId: string) => void;
  navigateToOverview: () => void;
  navigateToNodeInLayer: (nodeId: string) => void;

  setSearchQuery: (query: string) => void;
  setSearchResults: (results: SearchResult[]) => void;
  setSearchMode: (mode: "fuzzy" | "semantic") => void;
  setKnowledgeViewFilter: (filter: KnowledgeViewFilter) => void;

  toggleNodeTypeFilter: (category: NodeCategory) => void;
  setDetailLevel: (level: DetailLevel) => void;
  toggleShowFunctionsInClassView: () => void;
  toggleFilterPanel: () => void;
  setFilterPanelOpen: (open: boolean) => void;

  toggleContainer: (containerId: string) => void;
  expandContainer: (containerId: string) => void;
  collapseContainer: (containerId: string) => void;
  toggleContainerRecursive: (containerId: string, descendantIds: string[]) => void;
  setContainerLayout: (
    containerId: string,
    childPositions: Map<string, { x: number; y: number }>,
    actualSize: { width: number; height: number },
  ) => void;
  bumpStage1Tick: () => void;
  setPendingFocusContainer: (id: string | null) => void;
  resetContainerCaches: () => void;

  setPersona: (persona: Persona) => void;

  setDiffOverlay: (changed: string[], affected: string[]) => void;
  toggleDiffMode: () => void;
  setDiffMode: (on: boolean) => void;

  openCodeViewer: (nodeId: string) => void;
  closeCodeViewer: () => void;
  expandCodeViewer: () => void;
  collapseCodeViewer: () => void;

  startTour: (stepIndex?: number) => void;
  stopTour: () => void;
  nextTourStep: () => void;
  prevTourStep: () => void;
  setTourHighlightedNodeIds: (ids: string[]) => void;

  toggleExportMenu: () => void;
  setExportMenuOpen: (open: boolean) => void;
  togglePathFinder: () => void;
  setPathFinderOpen: (open: boolean) => void;

  pendingExportFormat: "png" | "svg" | null;
  requestExport: (format: "png" | "svg") => void;
  clearPendingExport: () => void;

  appendLayoutIssues: (issues: LayoutIssue[]) => void;
  clearLayoutIssues: () => void;

  setViewMode: (mode: ViewMode) => void;
  setIsKnowledgeGraph: (value: boolean) => void;
}

const DEFAULT_NODE_TYPE_FILTERS: Record<NodeCategory, boolean> = {
  code: true,
  config: true,
  docs: true,
  infra: true,
  data: true,
  domain: true,
  knowledge: true,
};

const initialState: DashboardState = {
  graph: null,
  nodesById: new Map(),
  nodeIdToLayerId: new Map(),
  nodeIdToLayerIds: new Map(),

  navigationLevel: "overview",
  activeLayerId: null,
  viewMode: "structural",
  isKnowledgeGraph: false,

  selectedNodeId: null,
  focusNodeId: null,
  nodeHistory: [],

  searchQuery: "",
  searchResults: [],
  searchMode: "fuzzy",
  knowledgeViewFilter: "both",

  nodeTypeFilters: { ...DEFAULT_NODE_TYPE_FILTERS },
  detailLevel: "file",
  showFunctionsInClassView: false,
  filterPanelOpen: false,

  expandedContainers: new Set(),
  containerLayoutCache: new Map(),
  containerSizeMemory: new Map(),
  stage1Tick: 0,
  pendingFocusContainer: null,

  persona: "experienced",

  diffMode: false,
  changedNodeIds: new Set(),
  affectedNodeIds: new Set(),

  codeViewerOpen: false,
  codeViewerNodeId: null,
  codeViewerExpanded: false,

  tourActive: false,
  currentTourStep: 0,
  tourHighlightedNodeIds: [],

  exportMenuOpen: false,
  pathFinderOpen: false,
  pendingExportFormat: null,
  layoutIssues: [],
};

function buildIndexes(graph: KnowledgeGraph | null) {
  const nodesById = new Map<string, GraphNode>();
  const nodeIdToLayerId = new Map<string, string>();
  const nodeIdToLayerIds = new Map<string, string[]>();

  if (!graph) {
    return { nodesById, nodeIdToLayerId, nodeIdToLayerIds };
  }

  for (const node of graph.nodes) {
    nodesById.set(node.id, node);
  }

  for (const layer of graph.layers) {
    for (const nodeId of layer.nodeIds) {
      nodeIdToLayerId.set(nodeId, layer.id);
      const existing = nodeIdToLayerIds.get(nodeId) ?? [];
      existing.push(layer.id);
      nodeIdToLayerIds.set(nodeId, existing);
    }
  }

  return { nodesById, nodeIdToLayerId, nodeIdToLayerIds };
}

export const useKgStore = create<DashboardState & DashboardActions>((set, get) => ({
  ...initialState,

  setGraph: (graph) => {
    const indexes = buildIndexes(graph);
    const isKnowledgeGraph = graph?.kind === "knowledge";
    set({
      graph,
      ...indexes,
      navigationLevel: "overview",
      activeLayerId: null,
      selectedNodeId: null,
      focusNodeId: null,
      nodeHistory: [],
      searchQuery: "",
      searchResults: [],
      knowledgeViewFilter: "both",
      expandedContainers: new Set(),
      containerLayoutCache: new Map(),
      containerSizeMemory: new Map(),
      stage1Tick: 0,
      pendingFocusContainer: null,
      viewMode: isKnowledgeGraph ? "knowledge" : "structural",
      isKnowledgeGraph,
      diffMode: false,
      changedNodeIds: new Set(),
      affectedNodeIds: new Set(),
      tourActive: false,
      currentTourStep: 0,
      tourHighlightedNodeIds: [],
      layoutIssues: [],
    });
  },

  selectNode: (id) => {
    const current = get().selectedNodeId;
    if (id && id !== current) {
      set((state) => {
        const history = [current, ...state.nodeHistory].filter(
          (x): x is string => !!x && x !== id,
        );
        return {
          selectedNodeId: id,
          nodeHistory: history.slice(0, 4),
          focusNodeId: null,
        };
      });
    } else if (!id) {
      set({ selectedNodeId: null });
    }
  },

  setFocusNode: (id) => set({ focusNodeId: id }),
  clearFocus: () => set({ focusNodeId: null }),

  drillIntoLayer: (layerId) => {
    set({
      navigationLevel: "layer-detail",
      activeLayerId: layerId,
      selectedNodeId: null,
      focusNodeId: null,
      expandedContainers: new Set(),
      containerLayoutCache: new Map(),
      pendingFocusContainer: null,
    });
  },

  navigateToOverview: () => {
    set({
      navigationLevel: "overview",
      activeLayerId: null,
      selectedNodeId: null,
      focusNodeId: null,
      expandedContainers: new Set(),
      containerLayoutCache: new Map(),
      pendingFocusContainer: null,
    });
  },

  navigateToNodeInLayer: (nodeId) => {
    const { nodeIdToLayerId, drillIntoLayer, selectNode } = get();
    const layerId = nodeIdToLayerId.get(nodeId);
    if (layerId) {
      drillIntoLayer(layerId);
      // Defer selection so the layer detail graph can mount first.
      setTimeout(() => selectNode(nodeId), 50);
    }
  },

  setSearchQuery: (query) => set({ searchQuery: query }),
  setSearchResults: (results) => set({ searchResults: results }),
  setSearchMode: (mode) => set({ searchMode: mode }),
  setKnowledgeViewFilter: (filter) => set({ knowledgeViewFilter: filter }),

  toggleNodeTypeFilter: (category) => {
    set((state) => ({
      nodeTypeFilters: {
        ...state.nodeTypeFilters,
        [category]: !state.nodeTypeFilters[category],
      },
      expandedContainers: new Set(),
      containerLayoutCache: new Map(),
    }));
  },

  setDetailLevel: (level) => {
    set({
      detailLevel: level,
      expandedContainers: new Set(),
      containerLayoutCache: new Map(),
    });
  },

  toggleShowFunctionsInClassView: () =>
    set((state) => ({
      showFunctionsInClassView: !state.showFunctionsInClassView,
      expandedContainers: new Set(),
      containerLayoutCache: new Map(),
    })),

  toggleFilterPanel: () => set((state) => ({ filterPanelOpen: !state.filterPanelOpen })),
  setFilterPanelOpen: (open) => set({ filterPanelOpen: open }),

  toggleContainer: (containerId) =>
    set((state) => {
      const next = new Set(state.expandedContainers);
      if (next.has(containerId)) {
        next.delete(containerId);
      } else {
        next.add(containerId);
      }
      return { expandedContainers: next, pendingFocusContainer: containerId };
    }),

  expandContainer: (containerId) =>
    set((state) => {
      const next = new Set(state.expandedContainers);
      next.add(containerId);
      return { expandedContainers: next, pendingFocusContainer: containerId };
    }),

  collapseContainer: (containerId) =>
    set((state) => {
      const next = new Set(state.expandedContainers);
      next.delete(containerId);
      return { expandedContainers: next };
    }),

  toggleContainerRecursive: (containerId, descendantIds) =>
    set((state) => {
      const next = new Set(state.expandedContainers);
      const isExpanded = next.has(containerId);
      for (const id of [containerId, ...descendantIds]) {
        if (isExpanded) {
          next.delete(id);
        } else {
          next.add(id);
        }
      }
      return { expandedContainers: next, pendingFocusContainer: containerId };
    }),

  setContainerLayout: (containerId, childPositions, actualSize) =>
    set((state) => {
      const cache = new Map(state.containerLayoutCache);
      const sizeMemory = new Map(state.containerSizeMemory);
      cache.set(containerId, { childPositions, actualSize });
      sizeMemory.set(containerId, actualSize);
      return { containerLayoutCache: cache, containerSizeMemory: sizeMemory };
    }),

  bumpStage1Tick: () => set((state) => ({ stage1Tick: state.stage1Tick + 1 })),

  setPendingFocusContainer: (id) => set({ pendingFocusContainer: id }),

  resetContainerCaches: () =>
    set({
      expandedContainers: new Set(),
      containerLayoutCache: new Map(),
      containerSizeMemory: new Map(),
      stage1Tick: 0,
    }),

  setPersona: (persona) => {
    set({ persona });
    if (persona === "non-technical") {
      set({ detailLevel: "file", showFunctionsInClassView: false });
    }
  },

  setDiffOverlay: (changed, affected) =>
    set({
      changedNodeIds: new Set(changed),
      affectedNodeIds: new Set(affected),
    }),

  toggleDiffMode: () => set((state) => ({ diffMode: !state.diffMode })),
  setDiffMode: (on) => set({ diffMode: on }),

  openCodeViewer: (nodeId) =>
    set({ codeViewerOpen: true, codeViewerNodeId: nodeId, codeViewerExpanded: false }),
  closeCodeViewer: () =>
    set({ codeViewerOpen: false, codeViewerNodeId: null, codeViewerExpanded: false }),
  expandCodeViewer: () => set({ codeViewerExpanded: true }),
  collapseCodeViewer: () => set({ codeViewerExpanded: false }),

  startTour: (stepIndex = 0) => {
    const graph = get().graph;
    const steps = graph?.tour ?? [];
    const step = steps[stepIndex];
    if (!step) return;
    set({
      tourActive: true,
      currentTourStep: stepIndex,
      tourHighlightedNodeIds: step.nodeIds,
    });
    get().navigateToNodeInLayer(step.nodeIds[0] ?? "");
  },

  stopTour: () =>
    set({
      tourActive: false,
      currentTourStep: 0,
      tourHighlightedNodeIds: [],
    }),

  nextTourStep: () => {
    const { graph, currentTourStep } = get();
    const steps = graph?.tour ?? [];
    const next = currentTourStep + 1;
    if (next < steps.length) {
      get().startTour(next);
    }
  },

  prevTourStep: () => {
    const { currentTourStep } = get();
    if (currentTourStep > 0) {
      get().startTour(currentTourStep - 1);
    }
  },

  setTourHighlightedNodeIds: (ids) => set({ tourHighlightedNodeIds: ids }),

  toggleExportMenu: () => set((state) => ({ exportMenuOpen: !state.exportMenuOpen })),
  setExportMenuOpen: (open) => set({ exportMenuOpen: open }),
  togglePathFinder: () => set((state) => ({ pathFinderOpen: !state.pathFinderOpen })),
  setPathFinderOpen: (open) => set({ pathFinderOpen: open }),

  requestExport: (format) => set({ pendingExportFormat: format, exportMenuOpen: false }),
  clearPendingExport: () => set({ pendingExportFormat: null }),

  appendLayoutIssues: (issues) =>
    set((state) => ({ layoutIssues: [...state.layoutIssues, ...issues] })),
  clearLayoutIssues: () => set({ layoutIssues: [] }),

  setViewMode: (mode) => set({ viewMode: mode }),
  setIsKnowledgeGraph: (value) => set({ isKnowledgeGraph: value }),
}));

// Derived selectors (simple helpers)
export function getSelectedNode(state: ReturnType<typeof useKgStore.getState>): GraphNode | null {
  return state.selectedNodeId ? state.nodesById.get(state.selectedNodeId) ?? null : null;
}

export function getNodeCategory(type: NodeType): NodeCategory {
  return NODE_TYPE_TO_CATEGORY[type] ?? "code";
}
