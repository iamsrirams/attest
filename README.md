# Attest

**An autonomous compliance agent that sweeps your AWS account, cites its evidence, and fixes what it finds — only after you say yes.**

Built for the AWS Agents for Humans Hackathon (Professional Agents track) on the
[Strands Agents SDK](https://github.com/strands-agents/sdk-python) and Amazon Bedrock.

> **Status:** early build. See `BUILD_LOG.md` for the decision record.

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
| `ctrl-s3-encryption` | All buckets have default SSE | CC6.7, CC6.8 |
| `ctrl-s3-public` | All buckets block public access | CC6.6 |
| `ctrl-cloudtrail` | Multi-region trail, logging on | CC7.2 |
| `ctrl-key-rotation` | No access keys older than the threshold | CC6.1 |
| `ctrl-guardduty` | GuardDuty detector enabled | CC7.1 |
| `ctrl-open-sgs` | No `0.0.0.0/0` ingress on 22 / 3389 | CC6.6 |
| `ctrl-ebs-default` | Account default EBS encryption | CC6.7 |
| `ctrl-config` | Config recorder recording | CC7.2, CC7.3 |

## Quickstart

Requires Python 3.11+, AWS credentials, and Bedrock model access for Claude
Sonnet 4.5 in your region (including the Anthropic use case details form).

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

## Layout

```
agent/       Agent construction, system prompt, sweep entry point
controls/    catalog.yaml — the 10 controls
tools/
  evidence/    read-only boto3 tools (one module per service)
  remediation/ approval-gated write tools
api/         FastAPI: runs, controls, approvals, chat, packet
packet/       trust packet rendering
web/         React + Vite dashboard
infra/       CloudFormation / SAM
scripts/     bootstrap, seeding, probes, repo scan
docs/PLAN.md  the durable build contract
```

`STATUS.md` is intentionally **not** tracked: it holds working state and
observations about the account under audit. See `docs/PLAN.md` §14 for what may
and may not be committed.

## Safety

Evidence tools are strictly read-only. Remediation tools verify an `APPROVED`
DynamoDB record bound to the exact `(action, resource)` pair before any write,
refuse to touch resources outside the configured prefix, and re-read the resource
afterwards to verify their own work. Every tool call is written to an append-only
audit log.

Full details in [`SECURITY.md`](SECURITY.md).

## License

MIT
