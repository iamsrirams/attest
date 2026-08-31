import { useCallback, useEffect, useState } from "react";
import { api } from "./api.js";

const POLL_MS = 2000;
const VERDICTS = ["FAIL", "PARTIAL", "INDETERMINATE", "PASS"];

/** Poll an endpoint on an interval. Polling only — no websockets by design. */
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

function Tiles({ counts }) {
  return (
    <div className="tiles">
      {VERDICTS.map((v) => (
        <div className="tile" key={v}>
          <div className={`n ${v}`}>{counts?.[v] ?? 0}</div>
          <div className="l">{v.toLowerCase()}</div>
        </div>
      ))}
    </div>
  );
}

function ApprovalCard({ a, onDecide, busy }) {
  return (
    <div className="card approval">
      <h3>{a.action}</h3>
      <div className="meta mono">{a.resource}</div>
      <p>{a.reason}</p>
      <div className="meta" style={{ marginTop: ".5rem" }}>
        expires {a.expires_at_iso}
      </div>
      <div className="actions">
        <button
          className="approve"
          disabled={busy}
          onClick={() => onDecide(a.approval_id, true)}
        >
          Approve
        </button>
        <button
          className="reject"
          disabled={busy}
          onClick={() => onDecide(a.approval_id, false)}
        >
          Reject
        </button>
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
          <span className="seq mono">{r.seq}</span>
          <span className="tool mono">{r.tool}</span>
          <span className="detail mono">{r.detail}</span>
        </div>
      ))}
    </div>
  );
}

export default function App() {
  const [runId, setRunId] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");

  const health = usePoll(() => api.health(), []);
  const runs = usePoll(() => api.runs(), []);
  const approvals = usePoll(() => api.approvals(), []);

  // Default to the newest run, but never fight a manual selection.
  useEffect(() => {
    if (!runId && runs.data?.runs?.length) setRunId(runs.data.runs[0].run_id);
  }, [runs.data, runId]);

  const run = usePoll(() => api.run(runId), [runId], !!runId);
  const controls = usePoll(() => api.controls(runId), [runId], !!runId);
  const timeline = usePoll(() => api.timeline(runId), [runId], !!runId);

  async function startSweep() {
    setBusy(true);
    setNote("");
    try {
      const r = await api.startSweep();
      if (r.started) {
        setRunId(r.run_id);
        setNote(`Started ${r.run_id}`);
      } else {
        setNote(r.reason);
      }
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
          ? "Approved. The agent is applying and verifying the change."
          : "Rejected."
      );
      approvals.refresh();
    } catch (e) {
      setNote(e.message);
    } finally {
      setBusy(false);
    }
  }

  const rows = [...(controls.data?.controls ?? [])].sort(
    (a, b) =>
      VERDICTS.indexOf(a.verdict) - VERDICTS.indexOf(b.verdict) ||
      a.control_id.localeCompare(b.control_id)
  );
  const pending = approvals.data?.approvals ?? [];
  const current = run.data?.run;

  return (
    <div className="wrap">
      <header className="top">
        <h1>Attest</h1>
        <span className="meta" style={{ color: "var(--muted)", fontSize: ".8rem" }}>
          <span className={`dot ${health.data?.ok ? "ok" : "off"}`} />
          {health.data?.region ?? "connecting"}
        </span>
        <div className="spacer" />
        <select value={runId} onChange={(e) => setRunId(e.target.value)}>
          {(runs.data?.runs ?? []).map((r) => (
            <option key={r.run_id} value={r.run_id}>
              {r.run_id} · {r.status}
            </option>
          ))}
        </select>
        {runId && (
          <a href={api.packetUrl(runId)} target="_blank" rel="noreferrer">
            <button>Trust packet</button>
          </a>
        )}
        <button className="primary" onClick={startSweep} disabled={busy}>
          {busy ? "Working…" : "Run sweep"}
        </button>
      </header>

      {note && <p className="err">{note}</p>}

      <Tiles counts={run.data?.counts} />

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
          <h2>Controls</h2>
          {rows.length === 0 ? (
            <div className="empty">
              No verdicts recorded for this run yet.
            </div>
          ) : (
            rows.map((c) => (
              <div className="card" key={c.control_id}>
                <h3>
                  <span className={`v ${c.verdict}`}>{c.verdict}</span>
                  <span className="tag mono">{c.control_id}</span>
                </h3>
                <p>{c.rationale}</p>
                {c.remediation && (
                  <div className="meta" style={{ marginTop: ".4rem" }}>
                    fix: {c.remediation}
                  </div>
                )}
                <div className="meta mono" style={{ marginTop: ".35rem" }}>
                  {(c.evidence_ids ?? []).join(", ") || "no evidence cited"}
                </div>
              </div>
            ))
          )}
        </section>

        <section>
          <h2>Agent activity</h2>
          <Timeline rows={timeline.data?.timeline} />

          {current?.summary && (
            <>
              <h2 style={{ marginTop: "1.5rem" }}>Summary</h2>
              <p className="summary">{current.summary}</p>
            </>
          )}
        </section>
      </div>
    </div>
  );
}
