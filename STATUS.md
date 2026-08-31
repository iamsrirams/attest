# STATUS

Live dashboard. Updated after every meaningful unit of work.

**Current phase:** Phase 0 — Foundation
**Last updated:** 2026-09-01

---

## 🔴 Blockers

### B1 — Bedrock model access gated (HUMAN-OWNED, blocks the Phase 0 gate)

Bedrock rejects every call to `us.anthropic.claude-sonnet-4-5-20250929-v1:0`:

```
ResourceNotFoundException: Model use case details have not been submitted for
this account. Fill out the Anthropic use case details form before using the
model.
```

Both `converse` and `converse_stream` are gated, so there is no streaming
workaround.

**To unblock:** in the AWS console → Bedrock → Model access, submit the Anthropic
use case details form for this account/region (`us-east-1`), wait ~15 min, then:

```bash
AWS_REGION=us-east-1 ./.venv/bin/python scripts/hello_strands.py
```

Expect `GATE PASSED`. Everything marked UNVERIFIED below becomes verifiable at
that moment.

**Impact:** does NOT block the seed script, bootstrap, evidence tools, catalog,
or agent construction — all of those use STS/S3/IAM/DynamoDB, which work today.

---

## ⚠️ UNVERIFIED

Built but not proven by running it. Never count these as done.

- `scripts/hello_strands.py` — Phase 0 go/no-go gate. Blocked on B1.

---

## 👤 Waiting on human

- **B1** — submit the Bedrock Anthropic use case details form (above).
- Verify the Devpost 2026 deadline, judging criteria, and video length limit on
  the official page.
- Record the demo video (Phase 9).
- Create and submit the Devpost entry (Phase 10).

---

## 🟡 Known issues (not blocking)

- **Node 22.3.0 is broken** on this machine — `libicui18n.74.dylib` missing after
  an icu4c upgrade. Only needed at Phase 5 for the React dashboard. Fix with
  `brew reinstall node` when we get there.
- AWS CLI 2.15.56 predates `bedrock-runtime converse`. Use boto3 for Bedrock
  probing, not the CLI. Not worth upgrading.

---

## Phase checklist

- [ ] **Phase 0/1 — Foundation** ← in progress
  - [x] Repo structure, `docs/PLAN.md`, `BUILD_LOG.md`, `STATUS.md`, `git init`
  - [x] venv + install + pin versions in `pyproject.toml`
  - [x] Resolve Strands 1.54.0 API surface (recorded in BUILD_LOG)
  - [x] Verify AWS identity via STS
  - [x] Resolve Bedrock model ID (`us.` inference profile required)
  - [ ] **Strands hello-world gate** — written, blocked on B1
  - [ ] `scripts/seed_demo_account.py` + run it
  - [ ] DynamoDB tables + S3 bucket bootstrap
  - [ ] First three evidence tools + `controls/catalog.yaml`
  - [ ] `agent/attest.py` + `agent/instructions.py`
  - [ ] Local CLI sweep
- [ ] Phase 2 — Agent (all ~9 read tools, full sweep, 40+ tool calls/run)
- [ ] Phase 3 — State (runs/controls/evidence, drift narration, checkpointing)
- [ ] Phase 4 — HITL (approval gate, remediation, verify-after-write, SES)
- [ ] Phase 5 — FastAPI backend + React dashboard
- [ ] Phase 6 — AWS deployment (AgentCore vs Lambda decision)
- [ ] Phase 7 — Testing (unit + golden run + failure scenarios)
- [ ] Phase 8 — Observability (OTel → CloudWatch)
- [ ] Phase 9 — Demo prep
- [ ] Phase 10 — Submission

---

## Next task

Seed script (`scripts/seed_demo_account.py`) and the tables/bucket bootstrap —
both are unblocked by B1 and are on the critical path to the Phase 0 exit
criteria.
