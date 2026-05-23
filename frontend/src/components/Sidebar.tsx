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
    Lock,
    ShieldCheck,
    RefreshCw,
    Mic,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useAnalyze } from "@/lib/analyze-context";

const NAV_ITEMS = [
    { href: "/", label: "Home", icon: Home, locked: false },
    { href: "/overview", label: "Overview", icon: LayoutDashboard, locked: true },
    { href: "/modules", label: "Modules", icon: Package, locked: true },
    { href: "/module", label: "Module Detail", icon: FileSearch, locked: true },
    { href: "/blast-radius", label: "Blast Radius", icon: Zap, locked: true },
    { href: "/reading-order", label: "Reading Order", icon: BookOpen, locked: true },
    { href: "/execution-flow", label: "Execution Flow", icon: GitBranch, locked: true },
    { href: "/chat", label: "Chat", icon: MessageCircle, locked: true },
    { href: "/voice", label: "Voice", icon: Mic, locked: true },
    { href: "/review", label: "Code Review", icon: ShieldCheck, locked: true },
    { href: "/incremental-reindex", label: "Smart Reindex", icon: RefreshCw, locked: true },
];

export function Sidebar() {
    const pathname = usePathname();
    const { result } = useAnalyze();
    const analyzed = result !== null;

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
                    const disabled = item.locked && !analyzed;

                    if (disabled) {
                        return (
                            <div
                                key={item.href}
                                className="flex items-center gap-3 px-3 py-2 rounded-md text-sm font-medium text-muted-foreground/40 cursor-not-allowed"
                            >
                                <item.icon className="h-4 w-4" />
                                {item.label}
                                <Lock className="h-3 w-3 ml-auto" />
                            </div>
                        );
                    }

                    return (
                        <Link
                            key={item.href}
                            href={item.href}
                            className={cn(
                                "flex items-center gap-3 px-3 py-2 rounded-md text-sm font-medium transition-colors",
                                isActive
                                    ? "bg-primary text-primary-foreground"
                                    : "text-muted-foreground hover:bg-accent hover:text-accent-foreground"
                            )}
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
