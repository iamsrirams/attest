import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "./api.js";

const POLL_MS = 2000;
const ORDER = ["FAIL", "PARTIAL", "INDETERMINATE", "PASS"];
const SEVERITY_RANK = { critical: 0, high: 1, medium: 2, low: 3, "": 4 };

function usePoll(fn, deps, enabled = true) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  const tick = useCallback(async () => {
    try {
      setData(await fn());
      setError(null);
    } catch (e) {
      setError(e.message);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  useEffect(() => {
    if (!enabled) return;
    tick();
    const id = setInterval(tick, POLL_MS);
    return () => clearInterval(id);
  }, [tick, enabled]);

  return { data, error, refresh: tick };
}

/** One line a founder can act on, derived from the verdicts. */
function posture(controls) {
  if (!controls?.length) return null;
  const fail = controls.filter((c) => c.verdict === "FAIL");
  const critical = fail.filter((c) => c.severity === "critical");
  const unknown = controls.filter((c) => c.verdict === "INDETERMINATE");
  const regressed = controls.filter((c) => c.drift === "regressed");

  if (regressed.length)
    return {
      tone: "bad",
      headline: `${regressed.length} control${regressed.length > 1 ? "s" : ""} regressed since the last run`,
      detail: regressed.map((c) => c.title).join(" · "),
    };
  if (critical.length)
    return {
      tone: "bad",
      headline: `Not ready for a security review — ${critical.length} critical ${critical.length > 1 ? "failures" : "failure"}`,
      detail: critical.map((c) => c.title).join(" · "),
    };
  if (fail.length)
    return {
      tone: "warn",
      headline: `${fail.length} control${fail.length > 1 ? "s" : ""} failing`,
      detail: fail.map((c) => c.title).slice(0, 3).join(" · "),
    };
  if (unknown.length)
    return {
      tone: "warn",
      headline: `${unknown.length} control${unknown.length > 1 ? "s" : ""} could not be checked`,
      detail: "Attest could not read these resources, so their state is unknown.",
    };
  return { tone: "good", headline: "All assessed controls pass", detail: "" };
}

function Tiles({ controls }) {
  const counts = useMemo(() => {
    const c = {};
    for (const x of controls ?? []) c[x.verdict] = (c[x.verdict] ?? 0) + 1;
    return c;
  }, [controls]);

  return (
    <div className="tiles">
      {ORDER.map((v) => (
        <div className={`tile ${counts[v] ? "" : "zero"}`} key={v}>
          <div className={`n ${v}`}>{counts[v] ?? 0}</div>
          <div className="l">{v.toLowerCase()}</div>
        </div>
      ))}
    </div>
  );
}

/** Evidence, expandable. Traceability is the product, so it must be reachable. */
function Evidence({ ids, byId }) {
  const [open, setOpen] = useState(null);
  if (!ids?.length)
    return <div className="uncited">No evidence cited — do not rely on this verdict.</div>;

  return (
    <div className="evidence">
      {ids.map((id) => {
        const ev = byId?.[id];
        const isOpen = open === id;
        return (
          <div key={id}>
            <button
              className={`evbtn ${isOpen ? "on" : ""}`}
              onClick={() => setOpen(isOpen ? null : id)}
              title={ev ? `${ev.tool} · ${ev.collected_at}` : id}
            >
              <span className="mono">{id}</span>
              {ev && <span className="evtool">{ev.tool}</span>}
              <span className="chev">{isOpen ? "−" : "+"}</span>
            </button>
            {isOpen && (
              <pre className="evjson">
                {ev ? formatEvidence(ev) : "Evidence record not found for this run."}
              </pre>
            )}
          </div>
        );
      })}
    </div>
  );
}

/** Minimal markdown: models reliably emit **bold** and "- " bullets. */
function Summary({ text }) {
  const lines = (text ?? "").split("\n");
  return (
    <div className="summary">
      {lines.map((line, i) => {
        const bullet = /^\s*[-*]\s+/.test(line);
        const body = bullet ? line.replace(/^\s*[-*]\s+/, "") : line;
        const parts = body.split(/\*\*(.+?)\*\*/g);
        const rendered = parts.map((p, j) =>
          j % 2 ? <strong key={j}>{p}</strong> : <span key={j}>{p}</span>
        );
        if (!body.trim()) return <div key={i} className="sgap" />;
        return bullet ? (
          <div key={i} className="sbullet">{rendered}</div>
        ) : (
          <div key={i} className="sline">{rendered}</div>
        );
      })}
    </div>
  );
}

function formatEvidence(ev) {
  try {
    const parsed = JSON.parse(ev.result_json);
    return JSON.stringify(parsed.result ?? parsed, null, 2);
  } catch {
    return ev.result_json ?? "";
  }
}

function ControlCard({ c, evidenceById }) {
  return (
    <div className={`card ctrl v-${c.verdict}`}>
      <div className="chead">
        <span className={`v ${c.verdict}`}>{c.verdict}</span>
        {c.severity && <span className={`sev ${c.severity}`}>{c.severity}</span>}
        {c.drift === "regressed" && <span className="drift bad">regressed</span>}
        {c.drift === "fixed" && <span className="drift good">fixed</span>}
        {c.drift === "new" && <span className="drift">new</span>}
        <span className="spacer" />
        {(c.refs ?? []).map((r) => (
          <span className="ref" key={r}>
            {r}
          </span>
        ))}
      </div>
      <h3>{c.title}</h3>
      <p>{c.rationale}</p>
      {c.remediation && <div className="fix">Proposed fix: {c.remediation}</div>}
      <Evidence ids={c.evidence_ids} byId={evidenceById} />
      <div className="cid mono">{c.control_id}</div>
    </div>
  );
}

function ApprovalCard({ a, onDecide, busy }) {
  return (
    <div className="card approval">
      <div className="chead">
        <span className="v PENDING">AWAITING YOUR DECISION</span>
        <span className="spacer" />
        <span className="ref">expires {(a.expires_at_iso || "").slice(0, 16).replace("T", " ")}</span>
      </div>
      <h3>{a.action.replace(/_/g, " ")}</h3>
      <div className="target mono">{a.resource}</div>
      <p>{a.reason}</p>
      <div className="actions">
        <button className="approve" disabled={busy} onClick={() => onDecide(a.approval_id, true)}>
          Approve
        </button>
        <button className="reject" disabled={busy} onClick={() => onDecide(a.approval_id, false)}>
          Reject
        </button>
        <span className="hint">
          Attest cannot make this change without you. The approval is bound to this
          one action on this one resource, and is used once.
        </span>
      </div>
    </div>
  );
}

function Timeline({ rows }) {
  if (!rows?.length) return <div className="empty">No tool calls yet.</div>;
  return (
    <div className="timeline">
      {rows.map((r) => (
        <div className={`row ${r.ok ? "" : "bad"}`} key={r.seq}>
          <span className="seq mono">{String(Number(r.seq)).padStart(2, "0")}</span>
          <span className="tool mono">{r.tool}</span>
          <span className="detail">{r.detail}</span>
        </div>
      ))}
    </div>
  );
}

export default function App() {
  const [runId, setRunId] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [filter, setFilter] = useState("all");

  const health = usePoll(() => api.health(), []);
  const runs = usePoll(() => api.runs(), []);
  const approvals = usePoll(() => api.approvals(), []);

  useEffect(() => {
    if (!runId && runs.data?.runs?.length) setRunId(runs.data.runs[0].run_id);
  }, [runs.data, runId]);

  const run = usePoll(() => api.run(runId), [runId], !!runId);
  const controls = usePoll(() => api.controls(runId), [runId], !!runId);
  const timeline = usePoll(() => api.timeline(runId), [runId], !!runId);
  const evidence = usePoll(() => api.evidence(runId), [runId], !!runId);

  const evidenceById = useMemo(() => {
    const m = {};
    for (const e of evidence.data?.evidence ?? []) m[e.evidence_id] = e;
    return m;
  }, [evidence.data]);

  const rows = useMemo(() => {
    const all = [...(controls.data?.controls ?? [])].sort(
      (a, b) =>
        ORDER.indexOf(a.verdict) - ORDER.indexOf(b.verdict) ||
        (SEVERITY_RANK[a.severity] ?? 4) - (SEVERITY_RANK[b.severity] ?? 4) ||
        a.control_id.localeCompare(b.control_id)
    );
    if (filter === "all") return all;
    if (filter === "attention")
      return all.filter((c) => c.verdict !== "PASS");
    return all.filter((c) => c.verdict === filter);
  }, [controls.data, filter]);

  const allControls = controls.data?.controls ?? [];
  const p = posture(allControls);
  const pending = approvals.data?.approvals ?? [];
  const current = run.data?.run;
  const running = current?.status === "RUNNING";

  async function startSweep() {
    setBusy(true);
    setNote("");
    try {
      const r = await api.startSweep();
      if (r.started) {
        setRunId(r.run_id);
        setNote(`Sweep started. Watching ${r.run_id}.`);
      } else setNote(r.reason);
      runs.refresh();
    } catch (e) {
      setNote(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function decide(id, approved) {
    setBusy(true);
    try {
      await (approved ? api.approve(id) : api.reject(id));
      setNote(
        approved
          ? "Approved. Attest is applying the change and will re-check the resource."
          : "Rejected. The control stays as it is."
      );
      approvals.refresh();
    } catch (e) {
      setNote(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="wrap">
      <header className="top">
        <div className="brand">
          <h1>Attest</h1>
          <span className="region">
            <span className={`dot ${health.data?.ok ? "ok" : "off"}`} />
            {health.data?.region ?? "connecting"}
          </span>
        </div>
        <div className="spacer" />
        <select value={runId} onChange={(e) => setRunId(e.target.value)}>
          {(runs.data?.runs ?? []).map((r) => (
            <option key={r.run_id} value={r.run_id}>
              {r.run_id.replace("run-", "")} · {r.status}
            </option>
          ))}
        </select>
        {runId && (
          <a href={api.packetUrl(runId)} target="_blank" rel="noreferrer">
            <button>Trust packet</button>
          </a>
        )}
        <button className="primary" onClick={startSweep} disabled={busy || running}>
          {running ? "Sweeping…" : busy ? "Working…" : "Run sweep"}
        </button>
      </header>

      {note && <p className="note-line">{note}</p>}

      {p && (
        <div className={`posture ${p.tone}`}>
          <div className="headline">{p.headline}</div>
          {p.detail && <div className="detail">{p.detail}</div>}
        </div>
      )}

      <Tiles controls={allControls} />

      {controls.data && controls.data.not_assessed?.length > 0 && (
        <p className="gap">
          Not assessed in this run: {controls.data.not_assessed.join(", ")}
        </p>
      )}

      {pending.length > 0 && (
        <section>
          <h2>Waiting on you</h2>
          {pending.map((a) => (
            <ApprovalCard key={a.approval_id} a={a} onDecide={decide} busy={busy} />
          ))}
        </section>
      )}

      <div className="grid">
        <section>
          <div className="secthead">
            <h2>Controls</h2>
            <div className="filters">
              {["all", "attention", ...ORDER].map((f) => (
                <button
                  key={f}
                  className={`chip ${filter === f ? "on" : ""}`}
                  onClick={() => setFilter(f)}
                >
                  {f === "all" ? "all" : f === "attention" ? "needs attention" : f.toLowerCase()}
                </button>
              ))}
            </div>
          </div>
          {rows.length === 0 ? (
            <div className="empty">
              {running
                ? "The agent is working. Verdicts appear as it reaches them."
                : "No verdicts recorded for this run."}
            </div>
          ) : (
            rows.map((c) => (
              <ControlCard key={c.control_id} c={c} evidenceById={evidenceById} />
            ))
          )}
        </section>

        <section>
          <h2>What the agent did</h2>
          <Timeline rows={timeline.data?.timeline} />

          {current?.summary && (
            <>
              <h2 style={{ marginTop: "1.5rem" }}>Summary for you</h2>
              <Summary text={current.summary} />
            </>
          )}
        </section>
      </div>
    </div>
  );
}
