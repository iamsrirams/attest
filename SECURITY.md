# Security

Attest reads a live AWS account and, with explicit approval, writes to it. The
security model is therefore part of the product, not an afterthought.

## Design guarantees

**Evidence tools are strictly read-only.** They call `Get*`, `List*` and
`Describe*` APIs only. They return errors as data rather than raising, so a
permissions gap yields an `INDETERMINATE` verdict for one control instead of
aborting a sweep or silently producing a false `PASS`.

**Remediation is gated in code, not by prompt.** Before any write, a remediation
tool verifies an `APPROVED` record in DynamoDB bound to the exact
`(action, resource)` pair it is about to act on. A model asserting that something
was approved is not authorization. Approval records expire after 24 hours via
DynamoDB TTL.

**Remediation is prefix-bounded.** Write tools refuse to act on any resource
outside the configured `DEMO_PREFIX` (default `attest-demo-`). This is a hard
boundary in the tool body, independent of what the model requests.

**Every remediation verifies its own work.** After a write, the tool re-runs the
corresponding read API and returns the observed post-state, so a control only
flips on evidence rather than on the assumption that the write succeeded.

**Every tool call is recorded** to an append-only audit log with its arguments,
result and timestamp.

## Least privilege

Two roles, separated by intent:

- `EvidenceRole` — `ReadOnlyAccess` / `SecurityAudit`. Used for all sweeps.
- `RemediationRole` — only the specific write actions the remediation tools need
  (e.g. `s3:PutEncryptionConfiguration`, `iam:UpdateAccessKey`), assumable only
  through the approval-checked code path.

## Handling swept data

Sweep output contains real IAM user names, email addresses and resource
identifiers. Treat it as sensitive:

- `runs/`, `evidence/`, `packets/` and `*.evidence.json` are gitignored.
- `STATUS.md` is local-only, because it records observations about the account
  under audit.
- `scripts/scan_repo.sh` runs as a pre-commit hook and in CI to block account
  ids, access key ids, private keys and email addresses from being committed.

Trust packets are designed to be handed to a third party. Review one before
sharing it, and pseudonymize identities where the recipient does not need them.

## Running Attest against your own account

Use a scratch account if you can. If you point it at an account with real
workloads, be aware that a sweep enumerates users, keys, buckets, trails and
security groups, and that the resulting evidence describes that account's
weaknesses. Store it accordingly.

`scripts/seed_demo_account.py` deliberately creates insecure resources — an
unencrypted bucket, an MFA-less console user, a live access key and a security
group open to `0.0.0.0/0` on port 22. Run it only in an account you are willing
to make temporarily non-compliant, and tear it down with `--clean` afterwards.

## Reporting a vulnerability

Open a GitHub issue for anything non-sensitive. For a vulnerability that should
not be public, use GitHub's private vulnerability reporting on this repository
rather than an issue.
