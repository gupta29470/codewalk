import { applyElkLayout } from "./elk-layout";
import type { ElkInput, ElkOutput } from "./layout";

interface WorkerResponse {
  id: number;
  success: true;
  positioned: ElkOutput;
  issues: { level: "warning" | "error"; message: string }[];
}

interface WorkerError {
  id: number;
  success: false;
  error: string;
}

const WORKER_TIMEOUT_MS = 8000;

type Pending = {
  resolve: (value: { positioned: ElkOutput; issues: { level: "warning" | "error"; message: string }[] }) => void;
  reject: (reason: Error) => void;
};

let worker: Worker | null = null;
let nextId = 1;
const pending = new Map<number, Pending>();

function getWorker(): Worker {
  if (typeof window === "undefined") {
    throw new Error("Web Worker is only available in the browser");
  }
  if (worker) return worker;

  worker = new Worker(new URL("./elk.worker.ts", import.meta.url), { type: "module" });
  worker.onmessage = (event: MessageEvent<WorkerResponse | WorkerError>) => {
    const data = event.data;
    const p = pending.get(data.id);
    if (!p) return;
    pending.delete(data.id);
    if (data.success) {
      p.resolve({ positioned: data.positioned, issues: data.issues });
    } else {
      p.reject(new Error(data.error));
    }
  };
  worker.onerror = (err) => {
    // Reject all pending promises on a generic worker error
    for (const p of Array.from(pending.values())) {
      p.reject(new Error(err.message || "ELK worker failed"));
    }
    pending.clear();
  };
  worker.onmessageerror = (err) => {
    for (const p of Array.from(pending.values())) {
      p.reject(new Error(`ELK worker message error: ${err}`));
    }
    pending.clear();
  };
  return worker;
}

export async function applyElkLayoutWorker(
  input: ElkInput,
  options?: { strict?: boolean },
): Promise<{ positioned: ElkOutput; issues: { level: "warning" | "error"; message: string }[] }> {
  try {
    const w = getWorker();
    const id = nextId++;
    return await new Promise<{ positioned: ElkOutput; issues: { level: "warning" | "error"; message: string }[] }>(
      (resolve, reject) => {
        const timer = window.setTimeout(() => {
          pending.delete(id);
          reject(new Error("ELK worker timed out"));
        }, WORKER_TIMEOUT_MS);

        const wrappedResolve: Pending["resolve"] = (value) => {
          window.clearTimeout(timer);
          resolve(value);
        };
        const wrappedReject: Pending["reject"] = (reason) => {
          window.clearTimeout(timer);
          reject(reason);
        };
        pending.set(id, { resolve: wrappedResolve, reject: wrappedReject });
        w.postMessage({ id, input, options });
      },
    );
  } catch {
    // Fall back to main-thread layout if the worker is unavailable
    return applyElkLayout(input, options);
  }
}
