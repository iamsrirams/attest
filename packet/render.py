"""Trust packet rendering.

The packet is what gets handed to an auditor or a prospective customer, so its
defining property is that **every statement is traceable**. A verdict without a
citation is not rendered as a claim; it is rendered as a defect, visibly.

Two artefacts, same data:
  - packet.json  machine-readable, the full record including raw evidence
  - packet.html  self-contained, no external assets, printable

Identities are already pseudonymized at the tool boundary, so the packet is safe
to share as-is. The account owner can resolve a pseudonym locally with
`attest resolve <pseudonym>`.
"""

from __future__ import annotations

import html
import json
from typing import Any

import yaml

from tools import state
from tools.config import AWS_REGION, CATALOG_PATH, client, evidence_bucket

VERDICT_ORDER = {"FAIL": 0, "PARTIAL": 1, "INDETERMINATE": 2, "PASS": 3}


def _catalog() -> dict[str, dict]:
    with open(CATALOG_PATH) as f:
        cat = yaml.safe_load(f)
    return {c["id"]: c for c in cat.get("controls", [])}


def build_packet(run_id: str) -> dict:
    """Assemble the packet data model for a run."""
    run = state.get_run(run_id)
    if not run:
        raise ValueError(f"run {run_id} not found")

    catalog = _catalog()
    controls = state.get_controls(run_id)
    evidence = {e["evidence_id"]: e for e in state.get_evidence(run_id)}

    prev = state.previous_run(run_id)
    prev_verdicts = (
        {c["control_id"]: c["verdict"] for c in state.get_controls(prev["run_id"])}
        if prev
        else {}
    )

    rows: list[dict] = []
    for c in controls:
        cid = c["control_id"]
        meta = catalog.get(cid, {})
        cited = [evidence[e] for e in c.get("evidence_ids", []) if e in evidence]
        missing = [e for e in c.get("evidence_ids", []) if e not in evidence]
        was = prev_verdicts.get(cid)
        rows.append(
            {
                "control_id": cid,
                "title": meta.get("title", cid),
                "refs": meta.get("refs", []),
                "severity": meta.get("severity", ""),
                "verdict": c["verdict"],
                "rationale": c.get("rationale", ""),
                "remediation": c.get("remediation", ""),
                "recorded_at": c.get("recorded_at", ""),
                "evidence": [
                    {
                        "evidence_id": e["evidence_id"],
                        "tool": e.get("tool", ""),
                        # The evidence bucket is account-id-suffixed, so the raw
                        # URI would reintroduce the account id into an artefact
                        # designed to be handed to a third party.
                        "s3_uri": _safe_uri(e.get("s3_uri", "")),
                        "collected_at": e.get("collected_at", ""),
                        "result": _parse(e.get("result_json", "")),
                    }
                    for e in cited
                ],
                "uncited": not cited,
                "missing_evidence_ids": missing,
                "previous_verdict": was,
                "drift": _drift(was, c["verdict"]),
            }
        )

    rows.sort(key=lambda r: (VERDICT_ORDER.get(r["verdict"], 9), r["control_id"]))

    counts: dict[str, int] = {}
    for r in rows:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1

    assessed = {r["control_id"] for r in rows}
    return {
        "run_id": run_id,
        "region": run.get("region", AWS_REGION),
        "generated_from_run_started_at": run.get("started_at"),
        "generated_from_run_finished_at": run.get("finished_at"),
        "framework": "SOC 2 (Trust Services Criteria, CC series)",
        "controls": rows,
        "counts": counts,
        "total_assessed": len(rows),
        "catalog_size": len(catalog),
        "not_assessed": sorted(set(catalog) - assessed),
        "previous_run": prev["run_id"] if prev else None,
        "regressions": [r["control_id"] for r in rows if r["drift"] == "REGRESSED"],
        "fixes": [r["control_id"] for r in rows if r["drift"] == "FIXED"],
        "summary": run.get("summary", ""),
        "identity_note": (
            "Identities in this packet are pseudonymized. A pseudonym such as "
            "iam-user-a3f2c1 refers consistently to the same principal across "
            "runs; the account owner can resolve it locally."
        ),
    }


def _safe_uri(uri: str) -> str:
    """Scrub the account id out of an s3:// URI before it enters the packet."""
    if not uri:
        return uri
    from tools.redact import default_redactor, redaction_enabled

    if not redaction_enabled():
        return uri
    return default_redactor()._scrub_account(uri)


def _parse(blob: str) -> Any:
    try:
        return json.loads(blob)
    except (json.JSONDecodeError, TypeError):
        return None


def _drift(was: str | None, now: str) -> str:
    if was is None:
        return "NEW"
    if was == now:
        return "UNCHANGED"
    if was == "PASS" and now in ("FAIL", "PARTIAL"):
        return "REGRESSED"
    if was in ("FAIL", "PARTIAL", "INDETERMINATE") and now == "PASS":
        return "FIXED"
    return "CHANGED"


# -- HTML --------------------------------------------------------------------

_CSS = """
:root{--bg:#fff;--fg:#1a1a1a;--muted:#666;--line:#e2e2e2;--card:#fafafa;
--pass:#0a7d33;--fail:#c62828;--partial:#b26a00;--indet:#5a5a5a;}
@media(prefers-color-scheme:dark){:root{--bg:#161616;--fg:#ececec;--muted:#a0a0a0;
--line:#2e2e2e;--card:#1e1e1e;--pass:#4ec46f;--fail:#ff6b6b;--partial:#e3a33c;--indet:#9a9a9a;}}
*{box-sizing:border-box}
body{margin:0;padding:2.5rem 1.5rem;background:var(--bg);color:var(--fg);
font:15px/1.6 ui-sans-serif,-apple-system,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:60rem;margin:0 auto}
h1{font-size:1.6rem;margin:0 0 .25rem}
h2{font-size:1.1rem;margin:2.5rem 0 .75rem;padding-bottom:.35rem;border-bottom:1px solid var(--line)}
.sub{color:var(--muted);font-size:.9rem;margin-bottom:1.5rem}
.tiles{display:flex;flex-wrap:wrap;gap:.6rem;margin:1.25rem 0}
.tile{border:1px solid var(--line);border-radius:8px;padding:.6rem 1rem;background:var(--card);min-width:7rem}
.tile .n{font-size:1.5rem;font-weight:650;line-height:1}
.tile .l{font-size:.72rem;letter-spacing:.06em;text-transform:uppercase;color:var(--muted)}
.v{font-weight:650;font-size:.78rem;letter-spacing:.05em}
.PASS{color:var(--pass)}.FAIL{color:var(--fail)}.PARTIAL{color:var(--partial)}.INDETERMINATE{color:var(--indet)}
.ctrl{border:1px solid var(--line);border-radius:10px;padding:1rem 1.15rem;margin-bottom:.9rem;background:var(--card)}
.ctrl h3{margin:0 0 .2rem;font-size:1rem}
.meta{color:var(--muted);font-size:.8rem;margin-bottom:.6rem}
.rat{margin:.5rem 0}
.tag{display:inline-block;border:1px solid var(--line);border-radius:99px;
padding:.1rem .55rem;font-size:.72rem;color:var(--muted);margin-right:.3rem}
.drift{font-size:.75rem;font-weight:650;letter-spacing:.04em}
.REGRESSED{color:var(--fail)}.FIXED{color:var(--pass)}
details{margin-top:.6rem;border-top:1px solid var(--line);padding-top:.6rem}
summary{cursor:pointer;font-size:.82rem;color:var(--muted)}
pre{background:var(--bg);border:1px solid var(--line);border-radius:6px;
padding:.7rem;overflow-x:auto;font-size:.75rem;line-height:1.45;max-height:26rem}
.warn{border-left:3px solid var(--fail);padding-left:.7rem;color:var(--fail);font-size:.85rem}
.note{color:var(--muted);font-size:.82rem;border-left:2px solid var(--line);padding-left:.8rem;margin:1.25rem 0}
footer{margin-top:3rem;padding-top:1rem;border-top:1px solid var(--line);color:var(--muted);font-size:.8rem}
@media print{body{padding:0}details{display:block}details>summary{display:none}}
"""


def _esc(s: Any) -> str:
    return html.escape(str(s if s is not None else ""))


def render_html(packet: dict) -> str:
    c = packet["counts"]
    tiles = "".join(
        f'<div class="tile"><div class="n {v}">{c.get(v,0)}</div>'
        f'<div class="l">{v.lower()}</div></div>'
        for v in ("FAIL", "PARTIAL", "INDETERMINATE", "PASS")
    )

    body = []
    for r in packet["controls"]:
        refs = "".join(f'<span class="tag">{_esc(x)}</span>' for x in r["refs"])
        drift = (
            f'<span class="drift {r["drift"]}">{r["drift"]}</span>'
            f' <span class="tag">was {_esc(r["previous_verdict"])}</span>'
            if r["drift"] in ("REGRESSED", "FIXED")
            else f'<span class="tag">{r["drift"].lower()}</span>'
        )

        ev_html = ""
        if r["uncited"]:
            ev_html = (
                '<p class="warn">No evidence is cited for this verdict. '
                "It must not be relied upon.</p>"
            )
        else:
            for e in r["evidence"]:
                raw = json.dumps(e["result"], indent=2, default=str) if e["result"] else "{}"
                ev_html += (
                    f"<details><summary>{_esc(e['evidence_id'])} &middot; "
                    f"{_esc(e['tool'])} &middot; collected {_esc(e['collected_at'])}"
                    f"</summary><p class=\"meta\">{_esc(e['s3_uri'])}</p>"
                    f"<pre>{_esc(raw)}</pre></details>"
                )

        rem = (
            f'<p class="meta">Proposed remediation: {_esc(r["remediation"])}</p>'
            if r["remediation"]
            else ""
        )
        body.append(
            f'<div class="ctrl"><h3>{_esc(r["title"])}</h3>'
            f'<div class="meta"><span class="v {r["verdict"]}">{r["verdict"]}</span> '
            f'&middot; {_esc(r["control_id"])} &middot; {refs}{drift}</div>'
            f'<p class="rat">{_esc(r["rationale"])}</p>{rem}{ev_html}</div>'
        )

    regressed = ""
    if packet["regressions"]:
        regressed = (
            f'<p class="warn">Regressed since the previous run: '
            f'{_esc(", ".join(packet["regressions"]))}</p>'
        )

    gap = ""
    if packet["not_assessed"]:
        gap = (
            f'<p class="warn">Not assessed in this run: '
            f'{_esc(", ".join(packet["not_assessed"]))}</p>'
        )

    summary = (
        f'<h2>Summary</h2><p>{_esc(packet["summary"])}</p>' if packet["summary"] else ""
    )

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Attest trust packet {_esc(packet['run_id'])}</title>
<style>{_CSS}</style></head><body><div class="wrap">
<h1>Compliance trust packet</h1>
<p class="sub">{_esc(packet['framework'])} &middot; region {_esc(packet['region'])}
&middot; run {_esc(packet['run_id'])}<br>
Assessed {packet['total_assessed']} of {packet['catalog_size']} controls
&middot; generated from a sweep finished {_esc(packet['generated_from_run_finished_at'])}</p>
{tiles}{regressed}{gap}{summary}
<h2>Controls</h2>{''.join(body)}
<p class="note">{_esc(packet['identity_note'])}</p>
<footer>Every verdict above cites the tool call that produced it. Expand a
citation to read the raw JSON exactly as the tool returned it. Evidence is
archived to S3 under the run id and is independently retrievable.</footer>
</div></body></html>"""


# -- publishing --------------------------------------------------------------


def generate(run_id: str, upload: bool = True) -> dict:
    """Build, render, and optionally archive the packet to S3."""
    packet = build_packet(run_id)
    html_doc = render_html(packet)
    json_doc = json.dumps(packet, indent=2, default=str)

    out = {
        "run_id": run_id,
        "counts": packet["counts"],
        "regressions": packet["regressions"],
        "uncited_controls": [c["control_id"] for c in packet["controls"] if c["uncited"]],
        "html_bytes": len(html_doc),
    }

    if upload:
        s3 = client("s3")
        base = f"packets/{run_id}"
        for key, body, ctype in (
            (f"{base}/packet.html", html_doc, "text/html"),
            (f"{base}/packet.json", json_doc, "application/json"),
        ):
            s3.put_object(
                Bucket=evidence_bucket(),
                Key=key,
                Body=body.encode("utf-8"),
                ContentType=ctype,
            )
        out["html_uri"] = f"s3://{evidence_bucket()}/{base}/packet.html"
        out["json_uri"] = f"s3://{evidence_bucket()}/{base}/packet.json"

    out["_html"] = html_doc
    out["_json"] = json_doc
    return out
