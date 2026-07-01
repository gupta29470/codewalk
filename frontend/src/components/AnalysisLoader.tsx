"use client";

import { Loader2 } from "lucide-react";
import { useAnalyze } from "@/lib/analyze-context";

export function AnalysisLoader() {
  const { loading, steps } = useAnalyze();

  if (!loading) return null;

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-kinetic-root/90 backdrop-blur-sm">
      <div className="w-full max-w-md rounded-md border border-kinetic-border bg-kinetic-surface-container-low p-6">
        <div className="flex items-center gap-3 mb-4">
          <Loader2 className="h-5 w-5 animate-spin text-kinetic-primary" />
          <h2 className="text-lg font-semibold text-kinetic-on-surface">Analyzing codebase…</h2>
        </div>
        {steps.length > 0 && (
          <div className="space-y-2 max-h-64 overflow-y-auto pr-1">
            {steps.map((step, i) => (
              <div
                key={`${i}-${step}`}
                className={`text-sm ${
                  i === steps.length - 1
                    ? "text-kinetic-on-surface font-medium"
                    : "text-kinetic-on-surface-variant"
                }`}
              >
                {step}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
