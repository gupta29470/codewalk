"use client";

import { useEffect, useState } from "react";
import { useKgStore } from "@/lib/kg/store";
import { Highlight, themes } from "prism-react-renderer";
import { X, Maximize2, Minimize2 } from "lucide-react";

export default function CodeViewer() {
  const nodeId = useKgStore((s) => s.codeViewerNodeId);
  const expanded = useKgStore((s) => s.codeViewerExpanded);
  const closeCodeViewer = useKgStore((s) => s.closeCodeViewer);
  const expandCodeViewer = useKgStore((s) => s.expandCodeViewer);
  const collapseCodeViewer = useKgStore((s) => s.collapseCodeViewer);
  const nodesById = useKgStore((s) => s.nodesById);

  const [content, setContent] = useState<string>("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const node = nodeId ? nodesById.get(nodeId) : null;

  useEffect(() => {
    if (!node?.filePath) return;
    setLoading(true);
    setError(null);
    fetch(`/api/file-content?path=${encodeURIComponent(node.filePath)}`)
      .then(async (res) => {
        if (!res.ok) throw new Error(await res.text());
        return res.text();
      })
      .then(setContent)
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setLoading(false));
  }, [node?.filePath]);

  if (!node) return null;

  const language = node.language ?? "text";
  const lineRange = node.lineRange;

  const viewer = (
    <div className="h-full flex flex-col bg-kg-surface border-t border-kg-border-subtle">
      <div className="flex items-center justify-between px-4 py-2 border-b border-kg-border-subtle bg-kg-elevated">
        <div>
          <div className="text-sm font-medium text-kg-text-primary">{node.name}</div>
          <div className="text-[10px] text-kg-text-muted font-mono">{node.filePath}</div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={expanded ? collapseCodeViewer : expandCodeViewer}
            className="p-1.5 rounded text-kg-text-muted hover:text-kg-text-primary hover:bg-kg-panel"
          >
            {expanded ? <Minimize2 className="w-4 h-4" /> : <Maximize2 className="w-4 h-4" />}
          </button>
          <button
            onClick={closeCodeViewer}
            className="p-1.5 rounded text-kg-text-muted hover:text-kg-text-primary hover:bg-kg-panel"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      </div>
      <div className="flex-1 overflow-auto">
        {loading && (
          <div className="h-full flex items-center justify-center text-kg-text-muted text-sm">
            Loading...
          </div>
        )}
        {error && (
          <div className="h-full flex items-center justify-center text-kg-diff-changed text-sm p-4 text-center">
            {error}
            <div className="text-xs text-kg-text-muted mt-2">
              Make sure a file-content API exists or open the file in your editor.
            </div>
          </div>
        )}
        {!loading && !error && (
          <Highlight theme={themes.vsDark} code={content || "// No content"} language={language as never}>
            {({ className, style, tokens, getLineProps, getTokenProps }) => (
              <pre className={`${className} text-xs p-4`} style={{ ...style, margin: 0, minHeight: "100%" }}>
                {tokens.map((line, i) => {
                  const lineNumber = i + 1;
                  const isHighlighted = lineRange
                    ? lineNumber >= lineRange[0] && lineNumber <= lineRange[1]
                    : false;
                  return (
                    <div
                      key={i}
                      {...getLineProps({ line })}
                      className={`${isHighlighted ? "bg-kg-accent/15" : ""} table-row`}
                    >
                      <span className="table-cell text-right pr-4 select-none text-kg-text-muted w-10">
                        {lineNumber}
                      </span>
                      <span className="table-cell">
                        {line.map((token, key) => (
                          <span key={key} {...getTokenProps({ token })} />
                        ))}
                      </span>
                    </div>
                  );
                })}
              </pre>
            )}
          </Highlight>
        )}
      </div>
    </div>
  );

  if (expanded) {
    return (
      <div
        className="fixed inset-0 z-50 flex items-center justify-center bg-black/65 backdrop-blur-sm p-4 sm:p-6"
        onMouseDown={collapseCodeViewer}
      >
        <div
          className="w-[calc(100vw-32px)] max-w-[1120px] h-[calc(100vh-32px)] sm:h-[calc(100vh-48px)] max-h-[820px] rounded-lg border border-kg-border-medium bg-kg-surface shadow-2xl overflow-hidden"
          onMouseDown={(e) => e.stopPropagation()}
        >
          {viewer}
        </div>
      </div>
    );
  }

  return (
    <div className="absolute bottom-0 left-0 right-0 h-[40vh] z-20 overflow-hidden animate-kg-slide-up">
      {viewer}
    </div>
  );
}
