import ELK from "elkjs/lib/elk.bundled.js";
import type { ElkInput, ElkOutput } from "./layout";

let elkInstance: InstanceType<typeof ELK> | null = null;

function getElk(): InstanceType<typeof ELK> {
  if (!elkInstance) {
    elkInstance = new ELK();
  }
  return elkInstance;
}

export async function applyElkLayout(
  input: ElkInput,
  options: { strict?: boolean } = {},
): Promise<{ positioned: ElkOutput; issues: { level: "warning" | "error"; message: string }[] }> {
  try {
    const elk = getElk();
    const positioned = (await elk.layout(input as unknown as never)) as unknown as ElkOutput;
    return { positioned, issues: [] };
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    if (options.strict) {
      throw err;
    }
    console.warn("[ELK layout failed]", message);
    // Fallback: place children in a simple grid
    const positioned = fallbackLayout(input);
    return { positioned, issues: [{ level: "warning", message: `ELK layout failed: ${message}` }] };
  }
}

function fallbackLayout(input: ElkInput): ElkOutput {
  const children = input.children ?? [];
  const cols = Math.ceil(Math.sqrt(children.length));
  const spacingX = 280;
  const spacingY = 160;
  let x = 0;
  let y = 0;
  let col = 0;
  let maxHeight = 0;

  const positionedChildren = children.map((child) => {
    const node = { ...child, x, y };
    x += spacingX;
    maxHeight = Math.max(maxHeight, child.height);
    col++;
    if (col >= cols) {
      col = 0;
      x = 0;
      y += maxHeight + spacingY;
      maxHeight = 0;
    }
    return node;
  });

  return {
    id: input.id,
    children: positionedChildren,
  };
}
