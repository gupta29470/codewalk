"use client";

import { useEffect, useRef } from "react";
import mermaid from "mermaid";

mermaid.initialize({
    startOnLoad: false,
    theme: "default",
    securityLevel: "loose",
});

interface MermaidDiagramProps {
    chart: string;
    className?: string;
}

export function MermaidDiagram({ chart, className }: MermaidDiagramProps) {
    const containerRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        if (!containerRef.current || !chart) return;

        // Strip ```mermaid fences if present (LLM sometimes wraps it)
        let cleaned = chart.trim();
        if (cleaned.startsWith("```")) {
            cleaned = cleaned.replace(/^```(?:mermaid)?\s*\n?/, "").replace(/\n?```\s*$/, "");
        }

        const id = `mermaid-${Math.random().toString(36).slice(2, 9)}`;
        containerRef.current.innerHTML = "";

        mermaid
            .render(id, cleaned)
            .then(({ svg }) => {
                if (containerRef.current) {
                    containerRef.current.innerHTML = svg;
                }
            })
            .catch((err) => {
                if (containerRef.current) {
                    containerRef.current.innerHTML = `<pre class="text-sm text-destructive">Diagram error: ${err.message}</pre>`;
                }
            });
    }, [chart]);

    return <div ref={containerRef} className={className} />;
}
