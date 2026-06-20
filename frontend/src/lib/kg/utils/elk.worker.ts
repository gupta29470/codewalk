import { applyElkLayout } from "./elk-layout";
import type { ElkInput } from "./layout";

interface WorkerRequest {
  id: number;
  input: ElkInput;
  options?: { strict?: boolean };
}

self.onmessage = async (event: MessageEvent<WorkerRequest>) => {
  const { id, input, options } = event.data;
  try {
    const { positioned, issues } = await applyElkLayout(input, options);
    self.postMessage({ id, success: true, positioned, issues });
  } catch (err) {
    self.postMessage({
      id,
      success: false,
      error: err instanceof Error ? err.message : String(err),
    });
  }
};
