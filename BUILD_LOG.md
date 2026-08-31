# BUILD_LOG

Append-only. Decisions, resolved APIs, version pins, gotchas, fixes.
Newest entries at the bottom.

---

## 2026-09-01 — Phase 0 initialization

### Environment as found

| thing | value |
|---|---|
| Python | 3.13.1 (Homebrew, `/opt/homebrew/bin/python3`) |
| git | 2.37.1 (Apple Git-137.1) |
| AWS CLI | 2.15.56 |
| Node | **BROKEN** — `node 22.3.0` fails to load `libicui18n.74.dylib` (icu4c upgraded to 76 underneath it) |
| Platform | darwin 24.2.0 (arm64) |

**Node is broken but not yet needed.** It is only required at Phase 5 for the
React dashboard. Fix deferred; recorded in STATUS.md. Remedy when we get there:
`brew reinstall node` (or `brew link icu4c@74`).

**AWS CLI is too old for the `converse` API** (`aws bedrock-runtime converse`
does not exist in 2.15.56 — only `invoke-model`). Not worth upgrading: all
Bedrock calls go through boto3 in the venv, which is current. Use boto3 for any
Bedrock probing, not the CLI.

### Decision: venv over uv

`uv` is not installed. Used stdlib `venv` at `attest/.venv` rather than
installing another toolchain. Reversible, zero cost to switch later.

### Decision: region us-east-1

No region was configured in the AWS profile (`aws configure get region` empty).
Chose `us-east-1` — Sonnet 4.5 is present there, and it is the default home for
Bedrock/SES/AgentCore availability. Set explicitly via `AWS_REGION` everywhere
rather than relying on profile config, so the container and Lambda behave the
same as local.

### Resolved: pinned versions

Installed 2026-09-01, pinned in `pyproject.toml`:

```
strands-agents        1.54.0
strands-agents-tools  0.8.7
boto3                 1.43.83
botocore              1.43.83
fastapi               0.141.1
uvicorn               0.52.4
pydantic              2.13.5
PyYAML                6.0.3
pytest                9.1.1
moto                  5.2.3
opentelemetry-sdk     1.44.0   (transitive, via strands — relevant at Phase 8)
```

### Resolved: Strands 1.54.0 API surface

Inspected the installed package. The founding brief's example imports hold up;
recording the exact surface so future sessions do not re-derive it:

```python
from strands import Agent, tool                        # both top-level
from strands.models import BedrockModel                 # also strands.models.bedrock
from strands.agent import SlidingWindowConversationManager
```

`strands` top-level exports (1.54.0): `Agent`, `AgentBase`, `AgentSkills`,
`InterventionHandler`, `ModelRetryStrategy`, `MultiAgentPlugin`, `Plugin`,
`PosixShellSandbox`, `Sandbox`, `Skill`, `Snapshot`, `ToolContext`, `tool`, plus
the `agent`, `event_loop`, `experimental`, `handlers`, `hooks`, `injection`,
`interrupt`, `interventions`, `memory`, `models`, `plugins`, `sandbox`,
`session`, `storage`, `telemetry`, `tools`, `types`, `vended_plugins`
submodules.

`strands.agent` conversation managers available: `NullConversationManager`,
`SlidingWindowConversationManager`, `SummarizingConversationManager`. Using
`SlidingWindowConversationManager` per PLAN §7.

Note for Phase 4: `strands.hooks` exists in this version and does expose
tool-call hook events. Per PLAN §6 we still use the in-tool wrapper pattern for
the approval gate — it is version-proof and easier to demonstrate to judges.
`ToolContext` and `InterventionHandler` are worth revisiting at Phase 4 as a
*second* layer, not a replacement.

### Resolved: Bedrock model ID

**`us.anthropic.claude-sonnet-4-5-20250929-v1:0`** in `us-east-1`.

`aws bedrock list-foundation-models` reports the bare ID
`anthropic.claude-sonnet-4-5-20250929-v1:0` in both us-east-1 and us-west-2, but
invoking it directly fails:

```
ValidationException: Invocation of model ID anthropic.claude-sonnet-4-5-20250929-v1:0
with on-demand throughput isn't supported.
```

Sonnet 4.5 is inference-profile-only for on-demand. The `us.` cross-region
inference profile prefix is required. **Always use the `us.`-prefixed ID.**

### BLOCKER: Bedrock account gate (Anthropic use case details form)

First probe of `converse` with the `us.` profile returned `'OK'` — it genuinely
worked. Minutes later, the same call and `converse_stream` both began failing:

```
ResourceNotFoundException: Model use case details have not been submitted for
this account. Fill out the Anthropic use case details form before using the
model. If you have already filled out the form, try again in 15 minutes.
```

Both `converse` and `converse_stream` are gated identically, so this is not a
streaming-specific issue and `BedrockModel(streaming=False)` is not a workaround.
The one successful call appears to have been a brief grace window before the gate
applied.

This is a **human-owned task** (PLAN §11): submit the Anthropic use case details
form in the Bedrock console for this account, then re-run the Phase 0 gate.

**It does not block most of the build.** STS/S3/IAM/DynamoDB access all work with
the current credentials, so the seed script, bootstrap, evidence tools, catalog,
and agent construction can all be built and — apart from the LLM loop itself —
verified against real AWS. Only the agent's autonomous tool selection is
unverifiable until the form clears. Everything downstream of the model call is
marked UNVERIFIED in STATUS.md until then.

### Phase 0 gate script

`scripts/hello_strands.py` is the go/no-go gate: one Strands `Agent`, one real
tool (`get_caller_identity` wrapping boto3 STS), asked "Who am I in AWS, and what
region am I operating in?". It counts tool invocations and fails loudly if the
agent answers without calling the tool. Written and wired; **blocked on the
Bedrock gate above**, so its result is UNVERIFIED.

---

## 2026-09-01 — Phase 0 build-out

### Verified: infrastructure bootstrap

`scripts/bootstrap_aws.py` ran clean against `us-east-1`. Created
`attest_runs`, `attest_controls`, `attest_evidence`, `attest_approvals`
(TTL enabled on `expires_at`), `attest_audit_log`, plus the evidence bucket
(block-public-access on, AES256 default SSE, versioning). Idempotent on re-run.

Evidence bucket name is `{TABLE_PREFIX}-evidence-{account_id}`, resolved at
runtime from STS so the account id never lands in the repo.

### Verified: demo seeding

`scripts/seed_demo_account.py` ran clean. Real state created:

- an S3 bucket with default encryption explicitly **removed** — S3 has applied
  AES256 to new buckets by default since 2023, so `delete_bucket_encryption` is
  required to make `ctrl-s3-encryption` genuinely fail
- a second bucket with a policy denying `s3:GetEncryptionConfiguration` to the
  calling principal, to exercise AccessDenied → INDETERMINATE live. The policy
  denies the *specific* caller, not the account root, to avoid lockout;
  `s3:DeleteBucketPolicy` is deliberately not denied so `--clean` always works.
- an IAM console user with no MFA device (random password, never printed, no
  attached policies)
- a real, active access key on a second user
- a security group opening port 22 to `0.0.0.0/0`, attached to no instance

### Verified: 10 evidence tools against live AWS

`scripts/probe_tools.py` — **10/10 returned structured data, 0 raised.** Every
seeded misconfiguration was detected with correct values.

Design notes worth keeping:

- **Errors are returned as data, never raised.** A permissions gap has to become
  INDETERMINATE for one control, not an aborted sweep (PLAN §7.5).
- `ServerSideEncryptionConfigurationNotFoundError` is a legitimate FAIL; any
  *other* S3 error code means the bucket was unobservable → INDETERMINATE. The
  tool encodes this as `encrypted: true|false|null` so the model cannot conflate
  "no encryption" with "could not check".
- `list_open_security_groups` handles port *ranges* and the all-protocols `-1`
  rule, so a group opening 0-65535 is correctly caught as exposing both 22
  and 3389.
- Credential-report output is capped (`MAX_ROWS_RETURNED = 25`) with counts and a
  `truncated` flag, per PLAN §7's context-window guidance.

### Confirmed: Strands `@tool` behaviour in 1.54.0

`@tool` returns a `DecoratedFunctionTool` that **remains directly callable**
(`f(3)` works), and the function's docstring becomes the tool `description` in
the generated spec. Two consequences:

1. Tools are unit-testable without an agent — no wrapper indirection needed.
2. **Docstrings are the agent's tool-selection surface.** They are load-bearing
   product code, not comments. Each one names the control it serves.

### DECISION: repo made public early

Created the public repository at Phase 0 rather than at submission, so the commit
history is a visible, honest audit trail (PLAN §9).

Publishing early raises the stakes on what may be committed, so two rules now
apply to every change:

1. **`scripts/scan_repo.sh` must pass before every push.** It greps the working
   tree for AWS account ids, `AKIA` access key ids, private keys, `.env` files
   and known-sensitive markers. It is also wired as a local `pre-commit` hook.
2. **Sweep output never enters the repo.** The evidence tools return live IAM
   user names, email addresses and resource identifiers. `runs/`, `evidence/`,
   `packets/` and `*.evidence.json` are gitignored, and no probe output is
   pasted into documentation.

### DECISION: STATUS.md is local-only, BUILD_LOG.md is public

`STATUS.md` is an ephemeral scratchpad — current blockers, machine-specific
notes, and observations about the live AWS account being swept. Those
observations describe a real account's security posture, which must not be
published. It is now **gitignored** and untracked. It remains the session-resume
dashboard on disk (PLAN §13), and it was verified that no version containing
account observations ever reached the remote.

`BUILD_LOG.md` stays tracked. It is the engineering decision record — versions,
resolved APIs, tradeoffs, gotchas — and it is genuinely useful to a reader
evaluating the build. The split is: **decisions and rationale are public;
operational state and anything describing the audited account are not.**

Sensitive operational notes belong in `NOTES.local.md` (gitignored).

### Sanitization rule for evidence

Because Attest sweeps a live account, any artefact that leaves it — a trust
packet, a screenshot, a demo recording, a commit — must be checked for real
identities first. This is a product requirement, not just repo hygiene: the
packet is designed to be handed to a third-party auditor, so identity handling
in it has to be deliberate. Tracked in STATUS.md.

---

## 2026-09-01 — Pseudonymization and the S3 encryption control

### DECISION: redact at the tool boundary, not the storage boundary

Attest is being run against an account containing real people, so evidence has
to be pseudonymized. The obvious place is on the way into S3. The better place is
the moment a tool returns.

`tools/redact.py` + `tools/evidence/_wrap.py` apply redaction *inside* `@tool`:

```python
@tool
@redacted
def list_s3_encryption_status() -> dict: ...
```

The model therefore never receives a real IAM user name or email at all. Real
identities cannot leak into the conversation transcript, the run timeline shown
in the dashboard, a screenshot, or a demo recording — not merely into stored
evidence. Redaction defaults to ON; it takes an explicit `ATTEST_REDACT=0` to
disable, because the cost of redacting a scratch account is a slightly less
readable demo while the cost of the reverse is publishing colleagues' names.

Three properties make it usable rather than merely safe:

- **Deterministic** (salted SHA-256, salt persisted in gitignored
  `.attest_local/`): the same user maps to the same pseudonym across runs, so
  run-over-run drift comparison still works.
- **Structure-preserving**: counts, shapes and cardinality survive, so three
  distinct users remain three distinct users and the agent's reasoning is intact.
- **Reversible by the owner only**: a local gitignored map resolves
  `iam-user-a3f2c1` back to a real name. The pseudonyms travel; the map does not.

Demo resources (`attest-demo-*`) are deliberately exempt so the demo stays
legible and remediation can address them by real name — but an account id
embedded in an exempt name is still scrubbed.

Two leaks found and fixed while testing: an exempt bucket name retained the
12-digit account id suffix, and `arn:aws:iam::<account>:user/<name>` leaked the
principal because only the account id was being scrubbed. `tests/test_redact.py`
(19 tests) now asserts directly that no account id, email or known identity
survives a realistic payload.

**Verified against the live account:** full probe of all 10 tools, then grepped
the output for the account id, real usernames and any email address — 0 hits,
with counts and key ages preserved.

### DISCOVERY: an unencrypted S3 bucket can no longer exist

The founding demo moment was "the agent finds an unencrypted bucket, you
approve, encryption lands, the control flips FAIL → PASS". That is no longer
constructible.

Since January 2023 S3 applies SSE-S3 (AES256) as an unremovable baseline to
every bucket. `delete_bucket_encryption` returns success, and
`get_bucket_encryption` immediately reports AES256 again. Confirmed directly
against the account: the seeded bucket reported `encrypted: true, AES256` even
after an explicit delete.

`ctrl-s3-encryption` was therefore rewritten to test encryption **strength**
rather than presence: *every bucket must use SSE-KMS with a customer-managed
key*. This is what an enterprise security review actually asks — a
customer-managed CMK gives auditable key access, rotation and revocation, none of
which SSE-S3 provides. It is also a stronger authenticity story, because the
agent must distinguish `AES256` from `aws:kms`, and `aws/s3` (AWS-managed) from a
real CMK, rather than reading a boolean.

The demo moment is preserved in shape and is now genuinely reproducible:

```
BEFORE:   AES256   -> FAIL
          put_bucket_encryption(SSE-KMS, customer CMK)
AFTER :   aws:kms  -> PASS
RESTORED: AES256   -> FAIL          (seed script re-arms the demo)
```

**Verified end to end against the live bucket**, including the restore, so the
demo is repeatable rather than one-shot.

Consequences: `seed_demo_account.py` now creates a customer-managed CMK
(`alias/attest-demo`) and forces the demo bucket back to SSE-S3 on every run, so
re-seeding re-arms the demo after a remediation. `--clean` schedules the key for
deletion on the minimum 7-day window.

**Cost:** a CMK is ~$1/month — the only recurring charge the demo introduces, far
inside the budget in PLAN §10, but it is a real charge and `--clean` should be
run when the project is finished.

The remediation tool is renamed `enable_s3_default_encryption` →
`enable_s3_kms_encryption` in the catalog to match what it actually does.
