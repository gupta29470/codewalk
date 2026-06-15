"use client";

import { Loader2 } from "lucide-react";
import { useAnalyze } from "@/lib/analyze-context";

export function AnalysisLoader() {
  const { loading, steps } = useAnalyze();

  if (!loading) return null;

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-background/80 backdrop-blur-sm">
      <div className="w-full max-w-md rounded-lg border bg-card p-6 shadow-lg">
        <div className="flex items-center gap-3 mb-4">
          <Loader2 className="h-5 w-5 animate-spin text-primary" />
          <h2 className="text-lg font-semibold">Analyzing codebase…</h2>
        </div>
        {steps.length > 0 && (
          <div className="space-y-2 max-h-64 overflow-y-auto pr-1">
            {steps.map((step, i) => (
              <div
                key={`${i}-${step}`}
                className={`text-sm ${
                  i === steps.length - 1
                    ? "text-foreground font-medium"
                    : "text-muted-foreground"
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
