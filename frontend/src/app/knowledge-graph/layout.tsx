import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Knowledge Graph — Codewalk",
  description: "Interactive knowledge graph explorer",
};

export default function KnowledgeGraphLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <div className="kinetic-dashboard h-full w-full bg-kinetic-root text-kinetic-on-surface">
      {children}
    </div>
  );
}
