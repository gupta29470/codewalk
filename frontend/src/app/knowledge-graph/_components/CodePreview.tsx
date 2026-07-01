"use client";

import { useEffect, useState } from "react";
import { Loader2, AlertCircle } from "lucide-react";

interface CodePreviewProps {
  filePath?: string;
  lineRange?: [number, number];
}

export function CodePreview({ filePath, lineRange }: CodePreviewProps) {
  const [content, setContent] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!filePath) {
      setContent(null);
      setError(null);
      return;
    }
    setLoading(true);
    setError(null);
    fetch(`/api/file-content?path=${encodeURIComponent(filePath)}`)
      .then(async (res) => {
        if (!res.ok) throw new Error(await res.text() || "Failed to load file");
        return res.text();
      })
      .then((text) => {
        setContent(text);
      })
      .catch((err) => {
        setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => setLoading(false));
  }, [filePath]);

  const displayed = content ?? "";
  const lines = displayed.split("\n");
  const startLine = lineRange ? lineRange[0] : 1;
  const visibleLines = lineRange
    ? lines.slice(Math.max(0, startLine - 1), lineRange[1])
    : lines.slice(0, 40);

  if (loading) {
    return (
      <div className="flex h-32 items-center justify-center gap-2 text-kinetic-on-surface-variant">
        <Loader2 size={16} className="animate-spin" />
        <span className="text-xs">Loading source...</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex h-32 items-center gap-2 rounded-md border border-kinetic-error/30 bg-kinetic-status-error px-3 text-xs text-kinetic-status-error-text">
        <AlertCircle size={14} />
        {error}
      </div>
    );
  }

  if (!filePath) {
    return (
      <div className="rounded-md border border-kinetic-border bg-kinetic-surface-container-lowest p-3 text-xs text-kinetic-on-surface-variant kinetic-font-mono">
        No file path available.
      </div>
    );
  }

  return (
    <div className="overflow-hidden rounded-md border border-kinetic-border bg-kinetic-surface-container-lowest">
      <div className="max-h-64 overflow-y-auto overflow-x-hidden">
        <table className="w-full table-fixed border-collapse">
          <tbody>
            {visibleLines.map((line, i) => {
              const lineNo = startLine + i;
              return (
                <tr key={i} className="leading-5">
                  <td className="w-10 select-none border-r border-kinetic-border bg-kinetic-surface-container-low px-2 text-right text-[10px] text-kinetic-on-surface-variant kinetic-font-mono">
                    {lineNo}
                  </td>
                  <td className="whitespace-pre-wrap break-all px-3 text-xs text-kinetic-on-surface-variant kinetic-font-mono">
                    {line || " "}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
