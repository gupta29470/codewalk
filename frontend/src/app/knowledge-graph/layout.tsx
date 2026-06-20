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
    <div className="kg-dashboard kg-noise-overlay h-screen w-screen overflow-hidden">
      {children}
    </div>
  );
}
