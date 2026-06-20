"use client";

import { useEffect, useMemo, useState, Suspense, lazy } from "react";
import { useKgStore } from "@/lib/kg/store";
import { SearchBar } from "./SearchBar";
import { NodeInfo } from "./NodeInfo";
import { ProjectOverview } from "./ProjectOverview";
import { FileExplorer } from "./FileExplorer";
import { DiffToggle } from "./DiffToggle";
import { ExportMenu } from "./ExportMenu";
import { ThemePicker } from "./ThemePicker";
import { LayerLegend } from "./LayerLegend";
import { WarningBanner } from "./WarningBanner";
import type { KeyboardShortcut } from "./useKeyboardShortcuts";
import { useKeyboardShortcuts } from "./useKeyboardShortcuts";
import { useIsMobile } from "./useIsMobile";
import { MobileLayout } from "./MobileLayout";
import { Route } from "lucide-react";
import { ThemeProvider } from "@/lib/kg/themes";

const PathFinderModal = lazy(() => import("./PathFinderModal"));
const KeyboardShortcutsHelp = lazy(() => import("./KeyboardShortcutsHelp"));
const OnboardingOverlay = lazy(() => import("./OnboardingOverlay"));

type SidebarTab = "info" | "files";

export function DashboardShell({ children }: { children: React.ReactNode }) {
  const graph = useKgStore((s) => s.graph);
  const selectedNodeId = useKgStore((s) => s.selectedNodeId);
  const viewMode = useKgStore((s) => s.viewMode);
  const setViewMode = useKgStore((s) => s.setViewMode);
  const togglePathFinder = useKgStore((s) => s.togglePathFinder);
  const pathFinderOpen = useKgStore((s) => s.pathFinderOpen);
  const layoutIssues = useKgStore((s) => s.layoutIssues);

  const isMobile = useIsMobile();

  const [sidebarTab, setSidebarTab] = useState<SidebarTab>("info");
  const [showKeyboardHelp, setShowKeyboardHelp] = useState(false);
  const [showOnboarding, setShowOnboarding] = useState(() => {
    if (typeof window === "undefined") return false;
    return localStorage.getItem("codewalk-kg-onboarding") !== "1";
  });

  useEffect(() => {
    if (selectedNodeId) setSidebarTab("info");
  }, [selectedNodeId]);

  const dismissOnboarding = (remember: boolean) => {
    if (remember && typeof window !== "undefined") {
      localStorage.setItem("codewalk-kg-onboarding", "1");
    }
    setShowOnboarding(false);
  };

  const shortcuts = useMemo<KeyboardShortcut[]>(
    () => [
      {
        key: "?",
        shiftKey: true,
        description: "Show keyboard shortcuts",
        action: () => setShowKeyboardHelp((prev) => !prev),
        category: "General",
      },
      {
        key: "Escape",
        description: "Close panels / deselect",
        action: () => {
          const state = useKgStore.getState();
          if (state.pathFinderOpen) state.togglePathFinder();
          else if (state.filterPanelOpen) state.setFilterPanelOpen(false);
          else if (state.exportMenuOpen) state.setExportMenuOpen(false);
          else if (state.codeViewerExpanded) state.collapseCodeViewer();
          else if (state.codeViewerOpen) state.closeCodeViewer();
          else if (state.selectedNodeId) state.selectNode(null);
          else if (state.navigationLevel === "layer-detail") state.navigateToOverview();
          else if (state.tourActive) state.stopTour();
          else setShowKeyboardHelp(false);
        },
        category: "Navigation",
      },
      {
        key: "/",
        description: "Focus search",
        action: () => {
          const input = document.querySelector<HTMLInputElement>("[data-testid='search-input']");
          input?.focus();
        },
        category: "Navigation",
      },
      {
        key: "d",
        description: "Toggle diff mode",
        action: () => useKgStore.getState().toggleDiffMode(),
        category: "View",
      },
      {
        key: "f",
        description: "Toggle filters",
        action: () => useKgStore.getState().toggleFilterPanel(),
        category: "View",
      },
      {
        key: "e",
        description: "Toggle export menu",
        action: () => useKgStore.getState().toggleExportMenu(),
        category: "View",
      },
      {
        key: "p",
        description: "Open path finder",
        action: () => useKgStore.getState().togglePathFinder(),
        category: "View",
      },
      {
        key: "ArrowRight",
        description: "Next tour step",
        action: () => useKgStore.getState().nextTourStep(),
        category: "Tour",
      },
      {
        key: "ArrowLeft",
        description: "Previous tour step",
        action: () => useKgStore.getState().prevTourStep(),
        category: "Tour",
      },
    ],
    [],
  );

  useKeyboardShortcuts(shortcuts);

  const infoSidebarContent = (
    <>
      {selectedNodeId && <NodeInfo />}
      {!selectedNodeId && <ProjectOverview />}
    </>
  );

  const knowledgeViewFilter = useKgStore((s) => s.knowledgeViewFilter);
  const setKnowledgeViewFilter = useKgStore((s) => s.setKnowledgeViewFilter);

  // Check if graph has layers for structural view support
  const hasLayers = graph?.layers && graph.layers.length > 0;

  return (
    <ThemeProvider>
      {isMobile ? (
        <MobileLayout
          graphView={children}
          onOpenPathFinder={togglePathFinder}
          onOpenKeyboardHelp={() => setShowKeyboardHelp(true)}
        />
      ) : (
        <div className="h-screen w-screen flex flex-col bg-kg-root text-kg-text-primary">
          {/* Header */}
          <header className="flex items-center px-4 sm:px-5 py-3 bg-kg-surface border-b border-kg-border-subtle shrink-0 gap-3">
            <div className="flex items-center gap-4 shrink-0 min-w-0">
              <h1 className="font-heading text-base sm:text-lg text-kg-text-primary tracking-wide truncate max-w-[180px] sm:max-w-[260px]">
                {graph?.project.name ?? "CodeWalk"}
              </h1>
              <div className="w-px h-5 bg-kg-border-subtle hidden sm:block" />
              <div className="flex items-center bg-kg-elevated rounded-lg p-0.5">
                <button
                  type="button"
                  onClick={() => setViewMode("structural")}
                  disabled={!hasLayers}
                  title={!hasLayers ? "This graph has no structural layers" : "Switch to Structural view"}
                  className={`px-3 py-1 text-xs font-medium rounded-md transition-colors ${!hasLayers
                      ? "opacity-40 cursor-not-allowed text-kg-text-muted"
                      : viewMode === "structural"
                        ? "bg-kg-accent/20 text-kg-accent"
                        : "text-kg-text-muted hover:text-kg-text-secondary"
                    }`}
                >
                  Structural
                </button>
                <button
                  type="button"
                  onClick={() => setViewMode("knowledge")}
                  className={`px-3 py-1 text-xs font-medium rounded-md transition-colors ${viewMode === "knowledge"
                    ? "bg-kg-accent/20 text-kg-accent"
                    : "text-kg-text-muted hover:text-kg-text-secondary"
                    }`}
                >
                  Knowledge
                </button>
              </div>
            </div>

            <div className="flex-1 min-w-0 overflow-x-auto kg-scrollbar-hide">
              <div className="flex items-center gap-4 w-max">
                <DiffToggle />
                {viewMode === "knowledge" && (
                  <>
                    <div className="w-px h-5 bg-kg-border-subtle" />
                    <div className="flex items-center bg-kg-elevated rounded-lg p-0.5">
                      {(["files", "functions", "both"] as const).map((mode) => (
                        <button
                          key={mode}
                          type="button"
                          onClick={() => setKnowledgeViewFilter(mode)}
                          className={`px-3 py-1 text-xs font-medium rounded-md transition-colors whitespace-nowrap ${knowledgeViewFilter === mode
                            ? "bg-kg-accent/20 text-kg-accent"
                            : "text-kg-text-muted hover:text-kg-text-secondary"
                            }`}
                        >
                          {mode === "files" && "Files"}
                          {mode === "functions" && "Functions"}
                          {mode === "both" && "Files + Funcs"}
                        </button>
                      ))}
                    </div>
                  </>
                )}
                <LayerLegend />
              </div>
            </div>

            <div className="flex items-center gap-2 sm:gap-3 shrink-0">
              <ExportMenu />
              <button
                onClick={togglePathFinder}
                className="flex items-center gap-1.5 px-2 sm:px-3 py-1.5 rounded-lg text-sm bg-kg-elevated text-kg-text-secondary hover:text-kg-text-primary transition-colors"
                title="Path finder"
              >
                <Route className="w-4 h-4" />
                <span className="hidden md:inline">Path</span>
              </button>
              <ThemePicker />
            </div>
          </header>

          {/* Search */}
          <SearchBar />

          {/* Warning banner */}
          {layoutIssues.length > 0 && <WarningBanner issues={layoutIssues} />}

          {/* Main content */}
          <div className="flex-1 flex min-h-0 relative">
            <div className="flex-1 min-w-0 min-h-0 relative">
              {children}
            </div>

            <aside className="w-[260px] md:w-[300px] lg:w-[360px] shrink-0 bg-kg-surface border-l border-kg-border-subtle overflow-auto kg-glass">
              <div className="h-full flex flex-col min-h-0">
                <div className="flex items-center gap-1 p-2 border-b border-kg-border-subtle bg-kg-surface shrink-0">
                  {(["info", "files"] as const).map((tab) => (
                    <button
                      key={tab}
                      type="button"
                      onClick={() => setSidebarTab(tab)}
                      className={`flex-1 px-3 py-1.5 rounded-md text-xs font-semibold uppercase tracking-wider transition-colors ${sidebarTab === tab
                        ? "bg-kg-accent/15 text-kg-accent"
                        : "text-kg-text-muted hover:text-kg-text-primary hover:bg-kg-elevated"
                        }`}
                    >
                      {tab === "info" ? "Info" : "Files"}
                    </button>
                  ))}
                </div>
                <div className="flex-1 min-h-0 overflow-auto">
                  {sidebarTab === "files" ? <FileExplorer /> : infoSidebarContent}
                </div>
              </div>
            </aside>
          </div>
        </div>
      )}

      {pathFinderOpen && (
        <Suspense fallback={null}>
          <PathFinderModal onClose={togglePathFinder} />
        </Suspense>
      )}

      {showKeyboardHelp && (
        <Suspense fallback={null}>
          <KeyboardShortcutsHelp
            shortcuts={shortcuts}
            onClose={() => setShowKeyboardHelp(false)}
          />
        </Suspense>
      )}

      {showOnboarding && (
        <Suspense fallback={null}>
          <OnboardingOverlay onDismiss={dismissOnboarding} />
        </Suspense>
      )}
    </ThemeProvider>
  );
}
