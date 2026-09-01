const BASE = "/api";

async function get(path) {
  const r = await fetch(`${BASE}${path}`);
  if (!r.ok) throw new Error(`${r.status} ${path}`);
  return r.json();
}

async function post(path, body) {
  const r = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
  if (!r.ok) throw new Error(`${r.status} ${path}`);
  return r.json();
}

export const api = {
  health: () => get("/health"),
  runs: () => get("/runs"),
  run: (id) => get(`/runs/${id}`),
  controls: (id) => get(`/runs/${id}/controls`),
  timeline: (id) => get(`/runs/${id}/timeline`),
  evidence: (id) => get(`/runs/${id}/evidence`),
  approvals: () => get("/approvals"),
  packetUrl: (id) => `${BASE}/runs/${id}/packet`,
  startSweep: () => post("/runs", { trigger: "dashboard" }),
  approve: (aid) => post(`/approvals/${aid}/approve`, { decided_by: "dashboard" }),
  reject: (aid) => post(`/approvals/${aid}/reject`, { decided_by: "dashboard" }),
};
