import type { AgentConfig, AgentLive, AgentSummary, DataConfig, Meta, PresetInfo, RunInfo, Step } from "./types";

async function req<T>(method: string, url: string, body?: unknown, raw = false): Promise<T> {
  const init: RequestInit = { method };
  if (body !== undefined) {
    init.body = raw ? (body as string) : JSON.stringify(body);
    init.headers = { "Content-Type": raw ? "text/plain" : "application/json" };
  }
  const r = await fetch(url, init);
  if (!r.ok) {
    let msg = `${r.status} ${r.statusText}`;
    try {
      const j = await r.json();
      msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail ?? j);
    } catch { /* not JSON */ }
    throw new Error(msg);
  }
  const ct = r.headers.get("content-type") ?? "";
  return (ct.includes("json") ? r.json() : r.text()) as Promise<T>;
}

export const api = {
  meta: () => req<Meta>("GET", "/api/meta"),
  presets: () => req<PresetInfo[]>("GET", "/api/presets"),
  preset: (name: string) => req<AgentConfig>("GET", `/api/presets/${encodeURIComponent(name)}`),
  symbols: (provider: string) => req<string[]>("GET", `/api/data/${provider}/symbols`),
  uploadCsv: (symbol: string, content: string) =>
    req<{ symbol: string; candles: number }>("POST", "/api/data/csv", { symbol, content }),

  agents: () => req<AgentSummary[]>("GET", "/api/agents"),
  state: (id: string) => req<AgentLive>("GET", `/api/agents/${id}/state`),
  create: (config: AgentConfig) => req<AgentSummary>("POST", "/api/agents", { config }),
  update: (id: string, config: AgentConfig) => req<AgentSummary>("PUT", `/api/agents/${id}`, config),
  remove: (id: string) => req<{ ok: boolean }>("DELETE", `/api/agents/${id}`),
  control: (id: string, action: "start" | "pause" | "resume" | "step" | "stop") =>
    req<AgentSummary>("POST", `/api/agents/${id}/${action}`),

  runs: (agentId?: string) => req<RunInfo[]>("GET", `/api/runs${agentId ? `?agent_id=${agentId}` : ""}`),
  runSteps: (runId: string) => req<Step[]>("GET", `/api/runs/${runId}/steps`),
  step: (runId: string, step: number) => req<Step>("GET", `/api/runs/${runId}/steps/${step}`),

  arena: (agent_ids: string[], data: DataConfig | null, sync_features: boolean) =>
    req<{ ok: boolean }>("POST", "/api/arena", { agent_ids, data, sync_features }),

  exportYaml: () => req<string>("GET", "/api/export"),
  importYaml: (text: string) => req<string[]>("POST", "/api/import", text, true),
};
