# Attest

**An autonomous compliance agent that sweeps your AWS account, cites its evidence, and fixes what it finds — only after you say yes.**

Built for the AWS Agents for Humans Hackathon (Professional Agents track) on the
[Strands Agents SDK](https://github.com/strands-agents/sdk-python) and Amazon Bedrock.

> ⚠️ Early build — see `STATUS.md` for what is verified and what is not.

## The problem

Your startup just got its first enterprise customer. They sent a security
questionnaire. You have no compliance team, no SOC 2, and no idea whether your
AWS account would survive an auditor's first look.

## What Attest does

1. **Sweeps** a live AWS account against a 10-control catalog mapped to SOC 2
   CC-series controls, using read-only boto3 tools.
2. **Judges** each control — PASS / FAIL / PARTIAL / INDETERMINATE — with a
   one-line rationale and a citation to the exact tool call that produced it.
3. **Narrates drift** against the previous run, calling out regressions.
4. **Proposes remediations** and asks for approval.
5. **Fixes** — only after a human approves, enforced in code, never by prompt.
6. **Re-verifies its own work** and flips the control.
7. **Emits a trust packet** where every statement cites raw JSON evidence.

The agent is not a script. The control catalog lists *candidate* tools; the
Strands agent loop decides which to call, in what order, and adapts when they
fail.

## Quickstart

```bash
python3 -m venv .venv && ./.venv/bin/pip install -e ".[dev]"
cp .env.example .env    # fill in region, bucket, SES addresses
AWS_REGION=us-east-1 ./.venv/bin/python scripts/hello_strands.py   # go/no-go gate
```

Requires Bedrock model access for Claude Sonnet 4.5 in your region, including the
Anthropic use case details form.

## Safety model

- Evidence tools are strictly read-only.
- Remediation tools verify an `APPROVED` DynamoDB record bound to the exact
  (action, resource) pair **before** any write, and refuse to touch resources
  outside the `attest-demo-` prefix.
- Every remediation re-runs the corresponding read tool to verify its own work.
- Every tool call is written to an append-only audit log.

## License

MIT
