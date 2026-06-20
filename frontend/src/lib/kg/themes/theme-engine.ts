import type { ThemeConfig } from "./types";
import { getAccent, getPreset } from "./presets";

export function hexToRgb(hex: string): string {
  const h = hex.replace("#", "");
  const n = parseInt(h, 16);
  return `${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}`;
}

function deriveFromAccent(accentHex: string, isDark: boolean): Record<string, string> {
  const rgb = hexToRgb(accentHex);
  return {
    "kg-border-subtle": `rgba(${rgb}, ${isDark ? 0.12 : 0.1})`,
    "kg-border-medium": `rgba(${rgb}, ${isDark ? 0.25 : 0.18})`,
    "kg-glass-bg": isDark ? "rgba(20, 20, 20, 0.8)" : "rgba(255, 255, 255, 0.8)",
    "kg-glass-heavy-bg": isDark ? "rgba(20, 20, 20, 0.95)" : "rgba(255, 255, 255, 0.95)",
    "kg-edge": `rgba(${rgb}, 0.3)`,
    "kg-edge-dim": `rgba(${rgb}, 0.08)`,
  };
}

export function applyTheme(config: ThemeConfig): void {
  if (typeof document === "undefined") return;
  const preset = getPreset(config.presetId);
  const accent = getAccent(preset, config.accentId);
  const style = document.documentElement.style;

  // Base preset colors
  for (const [key, value] of Object.entries(preset.colors)) {
    style.setProperty(`--kg-${key}`, value);
  }

  // Cluster colors
  preset.clusterColors.forEach((value, i) => {
    style.setProperty(`--kg-cluster-${i}`, value);
  });

  // Accent colors
  style.setProperty("--kg-accent", accent.accent);
  style.setProperty("--kg-accent-dim", accent.accentDim);
  style.setProperty("--kg-accent-bright", accent.accentBright);
  style.setProperty("--kg-accent-rgb", hexToRgb(accent.accent));

  // Derived values
  const derived = deriveFromAccent(accent.accent, preset.isDark);
  for (const [key, value] of Object.entries(derived)) {
    style.setProperty(`--${key}`, value);
  }

  // Theme attribute
  document.documentElement.setAttribute("data-theme", preset.isDark ? "dark" : "light");

  // Heading font
  const fontMap: Record<string, string> = {
    serif: "var(--font-dm-serif)",
    sans: "var(--font-inter)",
    mono: "var(--font-jetbrains-mono)",
  };
  const headingFont = config.headingFont ?? "serif";
  style.setProperty("--font-heading", fontMap[headingFont] ?? fontMap.serif);
}
