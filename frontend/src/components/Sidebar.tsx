"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
    LayoutDashboard,
    Package,
    FileSearch,
    Zap,
    BookOpen,
    GitBranch,
    MessageCircle,
    Home,
    ShieldCheck,
    RefreshCw,
    Mic,
    Cloud,
    Network,
    Search,
    FileText,
    Share2,
    Lock,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useAnalyze } from "@/lib/analyze-context";

const NAV_ITEMS = [
    { href: "/", label: "Home", icon: Home, requiresIndex: false },
    { href: "/overview", label: "Overview", icon: LayoutDashboard, requiresIndex: true },
    { href: "/architecture", label: "Architecture", icon: Network, requiresIndex: true },
    { href: "/knowledge-graph", label: "Knowledge Graph", icon: Share2, requiresIndex: true },
    { href: "/modules", label: "Modules", icon: Package, requiresIndex: true },
    { href: "/module", label: "Module Detail", icon: FileSearch, requiresIndex: true },
    { href: "/blast-radius", label: "Blast Radius", icon: Zap, requiresIndex: true },
    { href: "/reading-order", label: "Reading Order", icon: BookOpen, requiresIndex: true },
    { href: "/execution-flow", label: "Execution Flow", icon: GitBranch, requiresIndex: true },
    { href: "/docs", label: "Docs", icon: FileText, requiresIndex: true },
    { href: "/research", label: "Research", icon: Search, requiresIndex: true },
    { href: "/chat", label: "Chat", icon: MessageCircle, requiresIndex: true },
    { href: "/voice", label: "Voice", icon: Mic, requiresIndex: true },
    { href: "/review", label: "Code Review", icon: ShieldCheck, requiresIndex: true },
    { href: "/incremental-reindex", label: "Smart Reindex", icon: RefreshCw, requiresIndex: true },
    { href: "/admin", label: "Cloud Admin", icon: Cloud, requiresIndex: false },
];

export function Sidebar() {
    const pathname = usePathname();
    const { hasIndex } = useAnalyze();

    return (
        <aside className="w-56 border-r bg-card flex flex-col h-screen sticky top-0">
            <div className="p-4 border-b">
                <Link href="/" className="flex items-center gap-2">
                    <span className="text-xl font-bold tracking-tight">CODEWALK</span>
                </Link>
                <p className="text-xs text-muted-foreground mt-1">
                    Codebase onboarding tool
                </p>
            </div>

            <nav className="flex-1 p-2 space-y-1">
                {NAV_ITEMS.map((item) => {
                    const isActive =
                        item.href === "/"
                            ? pathname === "/"
                            : pathname === item.href || pathname.startsWith(item.href + "/");
                    const locked = item.requiresIndex && !hasIndex;

                    const className = cn(
                        "flex items-center gap-3 px-3 py-2 rounded-md text-sm font-medium transition-colors",
                        isActive
                            ? "bg-primary text-primary-foreground"
                            : locked
                            ? "text-muted-foreground/50 cursor-not-allowed"
                            : "text-muted-foreground hover:bg-accent hover:text-accent-foreground"
                    );

                    if (locked) {
                        return (
                            <span
                                key={item.href}
                                className={className}
                                title="Analyze a codebase first to unlock this tab"
                            >
                                <item.icon className="h-4 w-4" />
                                <span className="flex-1">{item.label}</span>
                                <Lock className="h-3 w-3" />
                            </span>
                        );
                    }

                    return (
                        <Link
                            key={item.href}
                            href={item.href}
                            className={className}
                        >
                            <item.icon className="h-4 w-4" />
                            {item.label}
                        </Link>
                    );
                })}
            </nav>
        </aside>
    );
}
