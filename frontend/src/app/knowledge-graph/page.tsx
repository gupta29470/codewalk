import { Suspense } from "react";
import KnowledgeGraphClient from "./KnowledgeGraphClient";

export const dynamic = "force-dynamic";

export default function KnowledgeGraphPage() {
  return (
    <Suspense
      fallback={
        <div className="h-screen w-screen flex flex-col items-center justify-center gap-3 bg-kinetic-root text-kinetic-on-surface-variant">
          <span className="text-sm">Loading knowledge graph...</span>
        </div>
      }
    >
      <KnowledgeGraphClient />
    </Suspense>
  );
}
