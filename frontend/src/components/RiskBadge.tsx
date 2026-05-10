import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

const RISK_COLORS: Record<string, string> = {
    critical: "bg-red-600 text-white hover:bg-red-600",
    high: "bg-orange-500 text-white hover:bg-orange-500",
    moderate: "bg-yellow-500 text-black hover:bg-yellow-500",
    low: "bg-green-600 text-white hover:bg-green-600",
    none: "bg-gray-400 text-white hover:bg-gray-400",
    safe: "bg-green-600 text-white hover:bg-green-600",
};

interface RiskBadgeProps {
    level: string;
    className?: string;
}

export function RiskBadge({ level, className }: RiskBadgeProps) {
    const normalized = level.toLowerCase();
    return (
        <Badge className={cn(RISK_COLORS[normalized] || RISK_COLORS.none, className)}>
            {normalized.toUpperCase()}
        </Badge>
    );
}
