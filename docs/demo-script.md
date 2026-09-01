# Demo script — 5:00

Recording is human-owned. This is the shot list and the words.

## Before you start

```bash
# 1. Arm the demo: puts the bucket back on SSE-S3, recreates the seeded findings
./.venv/bin/python scripts/seed_demo_account.py

# 2. Confirm Bedrock answers
AWS_REGION=us-east-1 ./.venv/bin/python scripts/hello_strands.py   # expect GATE PASSED

# 3. Two terminals
AWS_REGION=us-east-1 MAX_KEY_AGE_DAYS=1 ./.venv/bin/uvicorn api.app:app --port 8000
export PATH=/usr/local/bin:$PATH && cd web && npm run dev

# 4. Browser: dashboard on :5173, S3 console open on the demo bucket's
#    Properties tab, scrolled to Default encryption
```

Check before recording:

- [ ] `attest-demo-logs-*` shows **SSE-S3 (AES256)** in the console
- [ ] The dashboard has at least one previous completed run, so drift has a baseline
- [ ] No pending approvals left over from a rehearsal
- [ ] `MAX_KEY_AGE_DAYS=1` is set, or the key-rotation control will pass

Rehearse once end to end. The approve step changes real state, so re-run the
seed script between takes.

---

## 0:00–0:25 — the problem

> Your startup just closed its first enterprise customer. Then their security
> team sends a questionnaire. Do all your IAM users have MFA? Is everything
> encrypted at rest? Prove it.
>
> You have no compliance team. You have an AWS account that has been growing for
> two years, and no idea what is actually in it.

Screen: the questionnaire, or just the dashboard cold.

## 0:25–0:50 — what Attest is

> Attest is an agent that audits your AWS account against SOC 2 controls, shows
> its evidence for every claim, and fixes what it finds — but only after you say
> yes.
>
> It is one Strands agent on Bedrock, with read-only tools, and a set of write
> tools it cannot use without your approval.

Screen: dashboard, empty or showing the last run.

## 0:50–2:20 — the live sweep

Click **Run sweep**. Let the timeline fill.

> It is deciding what to look at. The catalog tells it which controls matter,
> not which tools to call or in what order.

Point at the timeline as calls land.

> One credential report answers two different controls, so it does not fetch it
> twice.

When the INDETERMINATE bucket appears — this is the moment that matters:

> This bucket denies permission to read its encryption setting. It does not
> guess, and it does not report a pass. It marks the control indeterminate and
> says what it could not see. A tool that answers "probably fine" is worse than
> useless in an audit.

Then the drift line:

> And it compares against the last run. Anything that used to pass and now
> fails comes first.

## 2:20–3:10 — approve, fix, verify

Split screen: dashboard left, S3 console right.

> It found a bucket that only has S3-managed encryption. Enterprise reviewers
> want a customer-managed key, so you control rotation and can revoke access.
> It cannot make that change on its own.

Point at the approval card.

> It asked. Here is what it wants to change, which bucket, and why.

Click **Approve**. Then switch to the S3 console and refresh.

> That is the real bucket. SSE-KMS, customer-managed key.

Back to the dashboard.

> And it re-read the bucket to check its own work, rather than assuming the
> write landed. The control flips to pass with fresh evidence attached.

**If this fails live:** the approval gate refuses rather than half-applying, so
the bucket is unchanged. Say so and move on — the refusal is the feature.

## 3:10–3:50 — the trust packet

Click **Trust packet**.

> This is what you send back to the customer. Every line has the tool call
> behind it.

Expand a citation.

> That is the raw JSON the AWS API returned, with a timestamp. Not a summary of
> the evidence — the evidence.

Scroll to a pseudonym.

> Names are pseudonymized before the model ever sees them, so you can hand this
> to an auditor without leaking your team's identities. The same person maps to
> the same label across runs, so drift still works.

## 3:50–4:30 — it is an agent, not a report

Show the chat box, ask something not in the catalog:

> "Which of my S3 buckets would fail a customer-managed key requirement, and
> which ones can you not check at all?"

> Same tools, same read-only guarantees. It is not replaying the report — it is
> looking again and answering the question asked.

## 4:30–5:00 — close

> One Strands agent on Bedrock. Read-only evidence tools. Every write behind an
> approval that is checked in code, bound to one action on one resource,
> single-use, and expiring — not a prompt asking it to behave.
>
> First enterprise questionnaire, answered with evidence, in about four minutes.

---

## Lines worth keeping

- "An error is not a pass." — the INDETERMINATE moment
- "It re-read the bucket to check its own work."
- "Not a summary of the evidence. The evidence."
- "A prompt asking a model to behave is not a security control."

## What to cut if you run long

1. The drift narration at 2:20
2. The pseudonymization aside at 3:50
3. The ad-hoc chat question

Never cut the approve → fix → verify sequence. That is the demo.
