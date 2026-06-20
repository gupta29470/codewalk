import { useEffect } from "react";

export interface KeyboardShortcut {
  key: string;
  shiftKey?: boolean;
  ctrlKey?: boolean;
  altKey?: boolean;
  metaKey?: boolean;
  description: string;
  action: () => void;
  category: string;
}

function formatKey(shortcut: KeyboardShortcut): string {
  const parts: string[] = [];
  if (shortcut.ctrlKey || shortcut.metaKey) parts.push("Ctrl");
  if (shortcut.altKey) parts.push("Alt");
  if (shortcut.shiftKey) parts.push("Shift");
  parts.push(shortcut.key === " " ? "Space" : shortcut.key);
  return parts.join(" + ");
}

export function useKeyboardShortcuts(shortcuts: KeyboardShortcut[]) {
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const isTyping =
        target &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.isContentEditable);

      for (const shortcut of shortcuts) {
        if (event.key !== shortcut.key) continue;
        if (!!shortcut.shiftKey !== event.shiftKey) continue;
        if (!!shortcut.ctrlKey && !(event.ctrlKey || event.metaKey)) continue;
        if (!!shortcut.altKey !== event.altKey) continue;
        if (!!shortcut.metaKey && !event.metaKey) continue;

        if (isTyping && event.key !== "Escape") continue;

        event.preventDefault();
        shortcut.action();
        return;
      }
    };

    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [shortcuts]);
}

export { formatKey };
