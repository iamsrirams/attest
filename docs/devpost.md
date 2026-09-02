# Devpost submission — draft

Copy for the submission form. Verify the deadline, category and video length
limit on the official page before submitting — that part is yours.

---

## Title

**Attest — the compliance agent that shows its work**

## Tagline

Audits your AWS account against SOC 2 controls, cites the evidence for every
claim, and fixes what it finds only after you approve.

## Category

Professional Agents

---

## The problem

A startup closes its first enterprise customer, and a security questionnaire
arrives. Do all IAM users have MFA? Is data encrypted at rest? Is CloudTrail on?
Prove it.

There is no compliance team. There is a two-year-old AWS account nobody has
fully audited, and a deal waiting on the answers. The existing options are a
consultant who costs more than the deal, a compliance platform priced for
companies ten times the size, or a founder clicking through the console at
midnight guessing at what an auditor wants.

The hard part is not finding misconfigurations — plenty of scanners do that. It
is *proving* the answer. An auditor does not want a dashboard; they want to know
how you know, and when you checked.

## What Attest does

1. **Sweeps** the account against a 10-control catalog mapped to SOC 2 CC-series
   criteria, using read-only AWS APIs.
2. **Judges** each control — PASS, FAIL, PARTIAL or INDETERMINATE — with a
   one-line rationale and a citation to the exact tool call behind it.
3. **Narrates drift** against the previous run, leading with regressions.
4. **Asks** before changing anything, in language a non-expert can evaluate.
5. **Fixes** only after a human approves, then **re-reads the resource** to
   verify its own work.
6. **Emits a trust packet** where every statement expands to the raw JSON the
   AWS API returned.

## Why it is an agent, not a scanner

The control catalog lists *candidate* tools per control. It does not define an
order, and nothing in the code iterates it to "run the sweep". The agent reads
the catalog and decides what to call, in what sequence, and when one result has
already answered another control — the IAM credential report settles both MFA
coverage and key rotation, so it is not fetched twice.

The clearest case is partial observability. One bucket in the demo account
denies permission to read its encryption configuration. The tool returns
`encrypted: null` with an error code rather than raising, and the agent has to
decide that this specific bucket is unobservable: mark the control
INDETERMINATE, name what could not be read, and carry on with the rest of the
sweep. Not PASS, which would be a false assurance an auditor would eventually
catch. Not FAIL, which would be a finding nobody actually observed. Not a crash.

That judgment is the product. It is not expressible as a fixed sequence of API
calls.

## Human in the loop, enforced in code

A model saying "the user approved this" is not authorization.

Before any write, the remediation tool checks a DynamoDB record that is APPROVED,
bound to that exact `(action, resource)` pair, unexpired, and not already used.
Separately, it refuses any resource outside a configured prefix — so an approved
request for a production bucket is still denied. Both checks are in the tool
body. The IAM policy enforces the same prefix independently, so neither layer is
the only thing standing between the agent and a resource it should not touch.

Approvals expire after 24 hours and are burned on use, so one decision cannot
authorize two writes.

Verified against a live account, checking the bucket was genuinely unchanged
after each refusal: no approval, pending approval, an approval replayed from a
different bucket, and an approved-but-out-of-scope resource are all refused. The
correctly bound approval applies, verifies, and flips the control.

## Handling real identities

Attest was built against an account with real workloads and real people in it,
which forced a decision the demo-account version would have skipped.

Identities are pseudonymized **at the tool boundary** — inside the tool, before
the result reaches the model. So the model never sees a real IAM user name or
email at all, and they cannot leak into the conversation transcript, the
dashboard timeline, a screenshot, or a demo recording. Not merely into stored
evidence.

The pseudonyms are deterministic, so the same principal maps to the same label
across runs and drift comparison still works; structure-preserving, so counts
and cardinality survive and the agent's reasoning is unaffected; and reversible
only through a local map that never leaves the operator's machine.

## Architecture

One Strands agent running Claude Sonnet 4.5 on Amazon Bedrock, with 19 tools:
10 read-only evidence tools, 8 control-flow tools, and 1 approval-gated
remediation tool. `SlidingWindowConversationManager` caps context on long sweeps.

State is DynamoDB — runs, controls, evidence, approvals with a TTL, and an
append-only audit log. Evidence and trust packets go to S3. Notifications via
SES. Nightly trigger via EventBridge Scheduler. FastAPI backend, React
dashboard, polling only.

Two IAM roles, separated by intent: `EvidenceRole` can read the account but
holds no write permission at all, so a sweep cannot modify what it audits.
`RemediationRole` has exactly the write actions the tools perform, scoped to
prefix-matched resources, assumable only from `EvidenceRole`, and able to read
and burn an approval but never create or decide one.

The agent is runtime-agnostic — it does not know whether it was invoked from the
CLI, a container, Lambda or AgentCore.

## Technical decisions worth calling out

**An unencrypted S3 bucket cannot exist any more.** The original plan was to
demo finding an unencrypted bucket and encrypting it. Since January 2023, S3
applies AES256 as an unremovable baseline: `delete_bucket_encryption` returns
success and the bucket immediately reports AES256 again. The control was
retargeted to test encryption *strength* — SSE-KMS with a customer-managed key —
which is what enterprise reviews actually ask, and which makes the agent
distinguish `AES256` from `aws:kms`, and an AWS-managed `aws/s3` key from a real
CMK, rather than reading a boolean.

**Sonnet 4.5 is inference-profile-only on Bedrock.** The bare
`anthropic.claude-sonnet-4-5-*` model id is rejected for on-demand throughput;
the `us.`-prefixed cross-region inference profile is required.

**Four bugs sat between correct layers.** Redaction was right and remediation was
right, but the seam between them was not: the agent addressed resources by
pseudonym while AWS only knows the real name, so an approval would have bound to
a bucket that does not exist and the write would have failed with NoSuchBucket —
after every guard reported success. Separately, real bucket names leaked into
the trust packet through lists of bare strings, because walking a list passes
the parent key down and those keys matched no redaction rule. Two more of the same shape followed: the resume message handed the model the
approval record's real resource name, and the remediation tool returned the real
name it had resolved internally — both reaching the published packet through the
model's own rationale.

Every one was found by exercising the full path with real values. Unit tests on
either side passed throughout, because each layer was correct; the seam between
them was not.

## Failing safely

Two failures found by running it for real, both of the same kind — a wrong
answer that looks like a right one:

- A content filter truncated the model's output mid-answer, and the run was
  recorded **COMPLETE with zero verdicts**. For a compliance tool that reads as
  "we checked and found nothing wrong", which is the worst available way to be
  wrong. A sweep that records nothing is now FAILED, explicitly.
- That empty run then became the **drift baseline**, which made every control
  look new and would have hidden a regression. A baseline must now have recorded
  verdicts.

`record_finding` also accepted citations naming evidence that did not exist —
found the moment the golden-run test was written. A hallucinated id would have
been stored looking fully substantiated. Citations are now checked against what
the run actually archived.

## What I would build next

- More controls, and evidence tools for the services behind them.
- Continuous monitoring rather than nightly, so a regression surfaces in minutes.
- A remediation library beyond the two write actions, each with the same gate.
- Packet export to the questionnaire formats buyers actually send.

## Built with

Strands Agents SDK · Amazon Bedrock (Claude Sonnet 4.5) · Python · boto3 ·
DynamoDB · S3 · Lambda · EventBridge Scheduler · SES · IAM · KMS · CloudFormation ·
FastAPI · React · Vite · OpenTelemetry

## Links

- Repository: https://github.com/iamsrirams/attest
- Demo video: _to record_
