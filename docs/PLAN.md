# Attest — Durable Build Contract

This is the project's memory. It is condensed from the founding brief and does
not change unless the product direction changes. Read this first, every session,
then `STATUS.md`, then `BUILD_LOG.md`.

---

## 1. Mission

Attest is an autonomous compliance agent for small startups facing their first
enterprise security review. It:

1. sweeps a live AWS account against a 10-control starter catalog (mapped to
   common SOC 2 CC-series controls) using read-only boto3 tools,
2. records a verdict — PASS / FAIL / PARTIAL / INDETERMINATE — per control, with
   evidence archived to S3,
3. compares against the previous run and narrates the drift,
4. proposes safe remediations and executes them **only after human approval that
   is enforced in code**,
5. re-verifies its own work, and
6. generates an auditor-ready "trust packet" in which every statement cites the
   exact tool call, timestamp, and raw JSON evidence.

### The north-star demo moment

Everything built serves this: the agent finds a bucket whose encryption does not
meet the customer-managed-key requirement, requests approval, the human clicks
Approve, the S3 console shows the new encryption land live, the agent re-verifies,
and the control flips FAIL → PASS with a fresh evidence citation.

> Originally specified as "finds an *unencrypted* bucket". S3 has applied SSE-S3
> as an unremovable baseline to every bucket since January 2023, so a genuinely
> unencrypted bucket cannot be created and that demo was not constructible. The
> control now tests encryption *strength* — SSE-KMS with a customer-managed key —
> which preserves the moment and is what enterprise reviews actually ask for.
> Verified end to end, including the restore that re-arms the demo. See
> BUILD_LOG.

## 2. Hackathon context

AWS Agents for Humans Hackathon, Professional Agents track.

- Strands Agents SDK is **required** and must be genuinely load-bearing — the
  agent plans its own tool calls; the sweep is not scripted.
- Amazon Bedrock hosts the model. Deployment target is Bedrock AgentCore Runtime
  if it is deployable within ~1 focused day, otherwise Lambda + API Gateway.
  **Decide at Phase 6, not before.**
- Time is the scarcest resource. Build the Must-Have list only. Polish one narrow
  end-to-end workflow. Do not build a platform.
- Judges are AWS/AI engineers who will read the repo. Superficial agent
  implementations will be recognized and rejected.

## 3. Decisions already made — do not relitigate

- **Single Strands Agent.** No multi-agent, no swarm, no graph orchestrator. The
  Strands agent loop *is* the orchestrator.
- **Model:** Claude Sonnet 4.5 on Bedrock via Strands `BedrockModel`. Resolved ID
  is recorded in `BUILD_LOG.md`.
- **Tools:** ~9 read-only evidence tools; gated remediation tools
  (`enable_s3_default_encryption`, `disable_iam_access_key`); control-flow tools
  (`save_evidence`, `request_approval`, `get_approval_status`, `notify_user`,
  `record_finding`, `generate_trust_packet`, `get_control_catalog`).
- **State:** DynamoDB tables `runs`, `controls`, `evidence`, `approvals`
  (TTL 24h), `audit_log` (append-only).
- **Evidence + packets:** S3. **Notifications:** SES. **Trigger:** EventBridge
  Scheduler (nightly) + on-demand via API.
- **API:** FastAPI. **Web:** React + Vite, polling only — NO websockets or
  streaming.
- **Gated writes are enforced in code** via an APPROVED approval record in
  DynamoDB bound to the exact (action, resource) — not merely via system prompt.
  Every remediation re-runs the corresponding read tool to verify its own work.
- **Runtime-agnostic agent code:** the agent is importable and runnable locally
  (CLI) and in a container. AgentCore vs Lambda is a deployment decision only.

## 4. Hard constraints — DO NOT build

Multi-agent anything; multi-account support; full SOC 2 (60+ controls);
Jira/Slack/Vanta integrations; infrastructure-as-code perfectionism;
multi-tenancy; auth beyond a single Cognito user; PDF rendering heroics
(HTML+JSON packet is fine); a marketing site; any feature not on the Must-Have
list before the Must-Have list is done.

## 5. The authenticity contract

Highest-priority rules. Violating these fails the project.

1. **Never hardcode the sweep order.** The control catalog lists *candidate*
   tools; the agent decides which to call, in what order, and adapts when tools
   fail. If a fixed sequence of tool calls appears in Python, that is a script,
   and it disqualifies the project.
2. **Every verdict must be traceable to real tool output.** The system prompt
   asks for PASS/FAIL/PARTIAL/INDETERMINATE + one-line rationale + evidence
   citation per control. Findings come from observations, never invented.
3. **Gated tools check approval records in code** before any write. A model
   saying "approved" is not authorization.
4. **No mocks or fake data in the demo path.** Mocks are allowed only in unit
   tests (stubbed boto3 / moto). The demo runs against the real seeded account.
5. **Never claim something works without running it.** If it cannot be verified
   (e.g. credentials missing), build it, mark it `UNVERIFIED` in `STATUS.md`, and
   continue. Never mark unverified work as done.
6. **Version drift is ours to solve.** The Strands SDK evolves. If imports or
   APIs differ from the founding examples, inspect the installed package, adapt,
   and record the resolved API surface in `BUILD_LOG.md`. Design intent matters,
   not the exact import path.
7. **The repo will be public on Devpost.** No account IDs, real emails, ARNs of
   real resources, or credentials in code, docs, commits, or logs. Use
   placeholders.

## 6. Control catalog

Ten controls. The catalog lists *candidate* tools per control; the agent chooses.

| id | control | refs | primary tools |
|---|---|---|---|
| ctrl-mfa-users | All IAM users have MFA | CC6.1 | credential report / list_users+mfa |
| ctrl-mfa-root | Root has MFA, no root access keys | CC6.1 | get_account_summary |
| ctrl-s3-encryption | All buckets use SSE-KMS with a customer-managed key | CC6.7/CC6.8 | list_s3_encryption_status |
| ctrl-s3-public | All buckets block public access | CC6.6 | list_s3_public_access |
| ctrl-cloudtrail | Multi-region trail, logging on | CC7.2 | get_cloudtrail_status |
| ctrl-key-rotation | No active access keys older than MAX_KEY_AGE_DAYS | CC6.1 | get_iam_credential_report |
| ctrl-guardduty | GuardDuty detector enabled | CC7.1 | get_guardduty_status |
| ctrl-open-sgs | No 0.0.0.0/0 ingress on ports 22/3389 | CC6.6 | list_open_security_groups |
| ctrl-ebs-default | Account default EBS encryption | CC6.7 | get_default_ebs_encryption |
| ctrl-config | Config recorder recording | CC7.2/CC7.3 | get_config_recorder_status |

## 7. Agent system-prompt contract

Persona: meticulous staff security auditor for a small startup. Input: a run
manifest (run_id, control catalog, previous run results). For EVERY control:

1. decide which tools to call and in what order;
2. emit PASS | FAIL | PARTIAL | INDETERMINATE with a one-line rationale and an
   evidence citation;
3. call `save_evidence` for every tool result used;
4. if fixable by a remediation tool, call `request_approval` FIRST — never call a
   remediation tool unless an approval you requested is APPROVED;
5. on tool errors, mark INDETERMINATE, explain, and continue — never abort the
   sweep;
6. compare to the previous run and call out regressions;
7. end with a 30-second plain-language summary for a busy founder.

Use `SlidingWindowConversationManager` to cap context during long sweeps. Keep
tool outputs compact (e.g. truncate credential reports to the N oldest keys).

## 8. Phase plan and definitions of done

- **Phase 0/1 Foundation** — seed script, tables, bucket, catalog, hello-world.
  DoD: a local terminal run in which the Strands agent autonomously calls ≥3 real
  tools, produces correct verdicts for the seeded misconfigurations, saves
  evidence to S3, and writes findings to DynamoDB.
- **Phase 2 Agent** — all ~9 read tools, full sweep locally, 40+ tool calls/run,
  verdicts correct on all seeded findings, evidence in S3. DoD: golden-run test
  passes locally.
- **Phase 3 State** — runs/controls/evidence tables populated, prior-run drift
  compared and narrated by the agent, run checkpointing for crash-resume. DoD: a
  second run narrates the drift created by un-fixing something.
- **Phase 4 HITL** — `request_approval`, gated S3 encryption tool,
  approve-and-resume, verify-after-write, SES notify. DoD: approve a record →
  bucket encrypts live → control flips PASS → evidence updated.
- **Phase 5 Backend + Frontend** — FastAPI (runs, controls, approvals, chat,
  packet) + React dashboard (run timeline showing agent messages and tool calls,
  controls view, approval cards, packet view; polling). DoD: a non-technical user
  completes the full approve flow in the browser.
- **Phase 6 AWS deployment** — containerize; AgentCore Runtime if it works within
  one focused day, else Lambda + API Gateway; EventBridge nightly schedule; SES;
  IAM: `EvidenceRole` (ReadOnly/SecurityAudit) and `RemediationRole` (only
  `s3:PutEncryptionConfiguration`, `iam:UpdateAccessKey`), the latter assumable
  only via the approval-checked code path. DoD: nightly run fires in the cloud;
  approve flow works end-to-end deployed. Write `docs/architecture.md` (mermaid).
- **Phase 7 Testing** — unit tests (stubbed boto3), golden-run integration test
  (skipif no creds), failure scenarios: AccessDenied → INDETERMINATE; invalid
  bucket name rejected; timeout → retry ×2 → INDETERMINATE; duplicate trigger
  no-ops via run lock; user REJECTS → control stays FAIL, no retry loop; crash
  mid-sweep → resume from checkpoint. DoD: all pass or are explicitly UNVERIFIED.
- **Phase 8 Observability** — Strands OpenTelemetry instrumentation →
  CloudWatch. DoD: per-model-call and per-tool-call spans visible.
- **Phase 9 Demo prep** — README (diagram + GIF of the approve→fix→verify moment
  at top), recorded backup demo run, `demo-script.md` matching the 5:00 timeline
  (problem 0:00–0:25, product 0:25–0:50, live sweep 0:50–2:20, approval+fix
  split-screen 2:20–3:10, packet 3:10–3:50, Strands/AWS + ad-hoc chat query
  3:50–4:30, close 4:30–5:00). Video recording is human-owned.
- **Phase 10 Submission** — Devpost copy drafted in `docs/devpost.md`. Final
  submission is human-owned.

## 9. Operating protocol

- **Work autonomously.** Make sensible default choices, record them in
  `BUILD_LOG.md`, and move on.
- **`STATUS.md` is the dashboard:** current phase, next task, blockers,
  UNVERIFIED items, "waiting on human" items. Update after every meaningful unit
  of work.
- **`BUILD_LOG.md` is append-only.** Every decision, resolved API/ID, version
  pin, gotcha, and fix goes there.
- **Commit small and often**, conventional commits (`feat:`, `fix:`, `test:`,
  `docs:`). Every phase exit is a commit.
- **When blocked** after ~3 genuine attempts: log the blocker in `STATUS.md`,
  switch to the next unblocked task, surface the blocker at end of session.
- **Honesty rule:** "done" means the DoD is met AND the thing was run. Otherwise
  it is UNVERIFIED.
- **Priorities are fixed:** Must-Have list before anything else; the north-star
  demo moment is the tiebreaker for every tradeoff.

## 10. AWS safety rules

Operate ONLY in the configured scratch/demo account. Never touch another account
or profile. Write operations are allowed only on resources prefixed
`attest-demo-` or on Attest's own tables/bucket. Never enable anything that costs
real money beyond Bedrock inference, DynamoDB, S3, and SES basics — if anything
threatens to push monthly spend above ~$30 equivalent, stop and flag it. Clean up
stray test resources. No credentials or account identifiers ever enter the repo.

## 11. Human-owned tasks

Never done by the agent; tracked in `STATUS.md`.

- Verify the Devpost 2026 deadline, judging criteria, video length limit, and any
  AgentCore/build-story bonus points on the official page.
- Enable Bedrock model access (including the Anthropic use case details form).
- Record the demo video (voiceover).
- Create the Devpost submission and press Submit.
- Approve any AWS spending beyond the budget.

## 12. Stop-and-ask triggers

The only valid interruptions:

1. No AWS credentials / Bedrock access, and stubbed work is exhausted.
2. Any action that is destructive outside `attest-demo-*` resources.
3. A proposed change that alters product direction or violates §3–§5.
4. Budget threshold risk.
5. A hackathon rule ambiguity that affects architecture (e.g. an AgentCore
   requirement).

## 13. Multi-session resume protocol

At the start of EVERY session, before writing any code: read `docs/PLAN.md`,
`STATUS.md`, and `BUILD_LOG.md` in that order. They are the memory. Then continue
with the next open task per `STATUS.md`, following the operating protocol above.

## 14. What may and may not be committed

The repository is public. Attest sweeps a live AWS account, so its own output is
sensitive: real IAM user names, email addresses, resource identifiers, and a
description of an account's security weaknesses.

**Public (tracked):**

- source, tests, `controls/catalog.yaml`, `infra/`
- `docs/PLAN.md` — the durable contract
- `BUILD_LOG.md` — the engineering decision record: versions, resolved APIs,
  tradeoffs, gotchas
- `README.md`, `LICENSE`, `SECURITY.md`, `.env.example`

**Local-only (gitignored):**

- `STATUS.md` — ephemeral working state: current blockers, machine-specific
  notes, and observations about the account being swept
- `NOTES.local.md`, `*.local.md` — sensitive operational notes
- `.env`, any credential material
- `runs/`, `evidence/`, `packets/`, `*.evidence.json` — sweep output

The dividing line: **decisions and rationale are public; operational state and
anything describing the audited account are not.**

`scripts/scan_repo.sh` enforces this. It runs as a local `pre-commit` hook and in
CI, and checks for AWS account ids, access key ids, private keys, email
addresses, AI-attribution watermarks, and accidental tracking of `.env` or
`STATUS.md`. Install the hook once per clone:

```bash
ln -sf ../../scripts/scan_repo.sh .git/hooks/pre-commit
```

If a hit is a false positive, narrow the pattern — never skip the scan.
