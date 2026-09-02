# Attest

**An autonomous compliance agent that sweeps your AWS account, cites its evidence, and fixes what it finds — only after you say yes.**

Built for the AWS Agents for Humans Hackathon (Professional Agents track) on the
[Strands Agents SDK](https://github.com/strands-agents/sdk-python) and Amazon Bedrock.

> **Status:** working end to end against a live AWS account — the agent sweeps
> autonomously, cites its evidence, narrates drift, and applies an approved fix
> before re-checking its own work. `BUILD_LOG.md` is the decision record,
> including the bugs found along the way.

## The problem

Your startup just landed its first enterprise customer. They sent a security
questionnaire. You have no compliance team, no SOC 2, and no idea whether your
AWS account would survive an auditor's first look.

## What Attest does

1. **Sweeps** a live AWS account against a 10-control catalog mapped to SOC 2
   CC-series criteria, using read-only boto3 tools.
2. **Judges** each control — `PASS` / `FAIL` / `PARTIAL` / `INDETERMINATE` — with
   a one-line rationale and a citation to the exact tool call behind it.
3. **Narrates drift** against the previous run, calling out regressions.
4. **Proposes remediations** and requests approval.
5. **Fixes** only after a human approves — enforced in code, never by prompt.
6. **Re-verifies its own work** and flips the control on observed evidence.
7. **Emits a trust packet** in which every statement cites raw JSON evidence.

## Why it is an agent, not a script

The control catalog lists *candidate* tools per control. It does not define an
order. The Strands agent loop decides which tools to call, in what sequence, when
one result makes another unnecessary, and how to proceed when a tool fails —
`controls/catalog.yaml` is a specification of intent, not a runbook.

Concretely: an `AccessDenied` on one bucket must not become a false `PASS`, and
must not abort the sweep. Evidence tools return errors as data, and the agent has
to reason about partial observability and mark that control `INDETERMINATE`.

## Controls

| id | control | criteria |
|---|---|---|
| `ctrl-mfa-users` | All IAM users have MFA | CC6.1 |
| `ctrl-mfa-root` | Root has MFA, no root access keys | CC6.1 |
| `ctrl-s3-encryption` | All buckets use SSE-KMS with a customer-managed key | CC6.7, CC6.8 |
| `ctrl-s3-public` | All buckets block public access | CC6.6 |
| `ctrl-cloudtrail` | Multi-region trail, logging on | CC7.2 |
| `ctrl-key-rotation` | No access keys older than the threshold | CC6.1 |
| `ctrl-guardduty` | GuardDuty detector enabled | CC7.1 |
| `ctrl-open-sgs` | No `0.0.0.0/0` ingress on 22 / 3389 | CC6.6 |
| `ctrl-ebs-default` | Account default EBS encryption | CC6.7 |
| `ctrl-config` | Config recorder recording | CC7.2, CC7.3 |

## Quickstart

Requires Python 3.11+ and AWS credentials.

Bedrock model access for Claude Sonnet 4.5 needs the **Anthropic use case
details form** submitted for your account (Bedrock console → Model access) —
without it every call returns `ResourceNotFoundException: Model use case details
have not been submitted`. That gate is Anthropic-specific, so
`BEDROCK_MODEL_ID=us.amazon.nova-pro-v1:0` will run the agent meanwhile, with
two caveats: Nova's content filters sometimes block security findings
mid-answer, and it tends to skip the approval request.

```bash
python3 -m venv .venv
./.venv/bin/pip install -e ".[dev]"

cp .env.example .env          # set region, bucket, SES addresses
ln -sf ../../scripts/scan_repo.sh .git/hooks/pre-commit

./.venv/bin/python scripts/hello_strands.py      # agent + one real tool
./.venv/bin/python scripts/bootstrap_aws.py      # tables + evidence bucket
./.venv/bin/python scripts/probe_tools.py        # verify tools against AWS
```

To create the demo findings — **in a scratch account only** — and tear them down
afterwards:

```bash
./.venv/bin/python scripts/seed_demo_account.py
./.venv/bin/python scripts/seed_demo_account.py --clean
```

## Deploying

```bash
./scripts/deploy.sh                       # dry run, shows what would change
./scripts/deploy.sh --apply --email you@example.com
./scripts/deploy.sh --apply --enable-schedule   # start the nightly sweep
```

The stack creates the state tables, the evidence bucket, and two IAM roles:
`EvidenceRole` can read the account but holds no write permission, and
`RemediationRole` has only the write actions the tools perform, scoped to
prefix-matched resources and assumable only from `EvidenceRole`. The nightly
schedule is created disabled — watch a sweep before letting it run unattended.

## Observability

Strands emits a span per model call and per tool call. Set `ATTEST_TELEMETRY=1`
and `OTEL_EXPORTER_OTLP_ENDPOINT` to export them; `ATTEST_TELEMETRY_CONSOLE=1`
prints them locally. Spans carry the run id as `session.id`, so a trace groups
by sweep.

## Layout

```
agent/       Agent construction, system prompt, sweep entry point
controls/    catalog.yaml — the 10 controls
tools/
  evidence/    read-only boto3 tools (one module per service)
  remediation/ approval-gated write tools
api/         FastAPI: runs, controls, approvals, chat, packet
packet/       trust packet rendering
web/         React + Vite dashboard (polling, no websockets)
infra/       CloudFormation: tables, bucket, the two IAM roles, schedule
scripts/     bootstrap, seeding, probes, repo scan
docs/PLAN.md  the durable build contract
```

`STATUS.md` is intentionally **not** tracked: it holds working state and
observations about the account under audit. See `docs/PLAN.md` §14 for what may
and may not be committed.

## What it looks like running

The dashboard polls a sweep as it happens. Each verdict carries its severity,
the SOC 2 criteria it maps to, and the evidence ids behind it — expand one and
you get the raw JSON the AWS API returned. The timeline shows what each call
found (`list_s3_encryption_status → 14 buckets, 12 without a customer-managed
key, 1 unreadable`), and the ask panel puts a question to the live account
rather than to the stored report.

## Safety

Evidence tools are strictly read-only. Remediation tools verify an `APPROVED`
DynamoDB record bound to the exact `(action, resource)` pair before any write,
refuse to touch resources outside the configured prefix, and re-read the resource
afterwards to verify their own work. Every tool call is written to an append-only
audit log.

Full details in [`SECURITY.md`](SECURITY.md).

## License

MIT
