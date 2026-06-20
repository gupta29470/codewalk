import type { Config } from "tailwindcss";
import tailwindAnimate from "tailwindcss-animate";
import tailwindTypography from "@tailwindcss/typography";

const config: Config = {
  darkMode: ["class"],
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        card: {
          DEFAULT: "hsl(var(--card))",
          foreground: "hsl(var(--card-foreground))",
        },
        popover: {
          DEFAULT: "hsl(var(--popover))",
          foreground: "hsl(var(--popover-foreground))",
        },
        primary: {
          DEFAULT: "hsl(var(--primary))",
          foreground: "hsl(var(--primary-foreground))",
        },
        secondary: {
          DEFAULT: "hsl(var(--secondary))",
          foreground: "hsl(var(--secondary-foreground))",
        },
        muted: {
          DEFAULT: "hsl(var(--muted))",
          foreground: "hsl(var(--muted-foreground))",
        },
        accent: {
          DEFAULT: "hsl(var(--accent))",
          foreground: "hsl(var(--accent-foreground))",
        },
        destructive: {
          DEFAULT: "hsl(var(--destructive))",
          foreground: "hsl(var(--destructive-foreground))",
        },
        border: "hsl(var(--border))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
        kg: {
          root: "var(--kg-root)",
          surface: "var(--kg-surface)",
          elevated: "var(--kg-elevated)",
          panel: "var(--kg-panel)",
          accent: "var(--kg-accent)",
          "text-primary": "var(--kg-text-primary)",
          "text-secondary": "var(--kg-text-secondary)",
          "text-muted": "var(--kg-text-muted)",
          "border-subtle": "var(--kg-border-subtle)",
          "border-medium": "var(--kg-border-medium)",
          "diff-changed": "var(--kg-diff-changed)",
          "diff-affected": "var(--kg-diff-affected)",
          "node-file": "var(--kg-node-file)",
          "node-function": "var(--kg-node-function)",
          "node-class": "var(--kg-node-class)",
          "node-method": "var(--kg-node-method)",
          "node-module": "var(--kg-node-module)",
          "node-config": "var(--kg-node-config)",
          "node-document": "var(--kg-node-document)",
          "node-resource": "var(--kg-node-resource)",
          "node-table": "var(--kg-node-table)",
          "node-service": "var(--kg-node-service)",
          "node-domain": "var(--kg-node-domain)",
          "node-flow": "var(--kg-node-flow)",
          "node-step": "var(--kg-node-step)",
          "node-article": "var(--kg-node-article)",
          "node-entity": "var(--kg-node-entity)",
          "node-topic": "var(--kg-node-topic)",
          "node-claim": "var(--kg-node-claim)",
          "node-source": "var(--kg-node-source)",
          "node-concept": "var(--kg-node-concept)",
        },
      },
      fontFamily: {
        heading: ["var(--font-dm-serif)", "Georgia", "serif"],
        sans: ["var(--font-inter)", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["var(--font-jetbrains-mono)", "ui-monospace", "monospace"],
      },
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
      },
      keyframes: {
        "kg-fade-slide-in": {
          "0%": { opacity: "0", transform: "translateY(8px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        "kg-slide-up": {
          "0%": { transform: "translateY(100%)" },
          "100%": { transform: "translateY(0)" },
        },
        "kg-accent-pulse": {
          "0%, 100%": { boxShadow: "0 0 0 0 rgba(212, 165, 116, 0.4)" },
          "50%": { boxShadow: "0 0 0 8px rgba(212, 165, 116, 0)" },
        },
      },
      animation: {
        "kg-fade-slide-in": "kg-fade-slide-in 0.3s ease-out forwards",
        "kg-slide-up": "kg-slide-up 0.3s ease-out forwards",
        "kg-accent-pulse": "kg-accent-pulse 2s ease-in-out infinite",
      },
    },
  },
  plugins: [tailwindAnimate, tailwindTypography],
};
export default config;
