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

---

## 2026-09-01 — Agent, gate, state, packet, API

### The agent is assembled — 19 tools

`agent/attest.py` builds the Strands agent and hands it a run manifest. It
deliberately never calls an evidence tool: tool selection and ordering are the
model's decisions. There is a comment in the file saying so, because the moment
someone adds `list_s3_encryption_status()` to that module the project stops
being an agent and becomes a script.

Toolset: 10 evidence + 8 control-flow (`get_control_catalog`,
`get_previous_run_findings`, `save_evidence`, `record_finding`,
`request_approval`, `get_approval_status`, `notify_user`,
`generate_trust_packet`) + 1 gated remediation.

Two things borrowed from Anthropic's prompt-caching guidance, since caching is a
prefix match and the render order is tools → system → messages:

- `ALL_TOOLS` is assembled in a fixed order, never from a set or dict iteration.
- `SYSTEM_PROMPT` is byte-stable — no run id, no timestamp. The volatile per-run
  data goes in the first user message (`run_manifest`) instead.

### DECISION: the run id is process-local, not a tool argument

`control_flow.set_current_run()` holds it. If `run_id` were a tool parameter the
model could mistype it and write findings into another run — an integrity bug
that would be invisible until someone read the wrong packet. Nothing is lost:
the agent has no legitimate reason to address a different run.

### Verified live: the approval gate

Exercised end to end against the real bucket, checking after every step that the
bucket was genuinely unchanged:

```
1. no approval          -> AWAITING_APPROVAL   AES256
2. PENDING approval     -> AWAITING_APPROVAL   AES256
3. approval replayed
   from another bucket  -> AWAITING_APPROVAL   AES256
4. approved, but a
   non-demo resource    -> REFUSED             (prefix guard)
5. APPROVED, bound      -> APPLIED             aws:kms, cmk=True, verified
6. reuse burned approval-> AWAITING_APPROVAL   (single-use)
```

Step 4 is the one worth keeping: the prefix guard and the approval check are
independent, so an approved request for a production resource is still refused.
Step 6 confirms `mark_applied` burns the approval so one decision cannot
authorize two writes.

### Verified live: state layer

Evidence archived to S3 and indexed in DynamoDB; verdict validation rejects both
an invalid verdict string and a verdict citing no evidence; controls, evidence
and audit entries read back; drift baseline resolved from the previous completed
run. The S3 object was fetched back and matched the tool output.

Evidence is stored as a JSON *string* rather than a native DynamoDB map. That
avoids the float/Decimal conversion problem entirely and keeps a byte-exact copy
of what the tool returned, which is what "cite the evidence" actually requires.

### BUG FOUND AND FIXED: names leaked through lists of bare strings

Rendering the first real trust packet and grepping it for identities found a
genuine hole in the redaction layer.

`Redactor.walk` passes the *parent* key down when recursing into a list. So for
`{"buckets": [{"bucket": "prod-secrets"}], "not_meeting_kms_requirement": ["prod-secrets"]}`
the name inside `buckets` was pseudonymized correctly, while the same name in
the flat list was handed the key `not_meeting_kms_requirement` — absent from
`KEY_KINDS` — and fell through to free-text scrubbing, which only catches
account ids, emails and ARNs. Real bucket names reached the packet.

This is exactly the failure mode the redaction layer exists to prevent, and it
was invisible until an artefact was rendered and grepped. Two-part fix:

1. The known list-of-names keys are mapped explicitly.
2. `walk()` now runs a **defensive second pass** that replaces any
   already-pseudonymized value wherever it still appears verbatim. Adding a tool
   with a new list key can no longer silently reintroduce the leak; the worst
   case becomes over-redaction, which is the safe direction.

Also fixed: the packet reintroduced the account id through the evidence
`s3://` URI, because the evidence bucket name is account-suffixed.

**Lesson recorded:** unit tests on the redactor passed throughout. The bug was
only visible in the rendered artefact. Grep every artefact that is designed to
leave the machine, not just the layer that produces it.

### Trust packet

`packet/render.py` emits HTML and JSON from the same model. Design choices worth
keeping: a verdict citing no evidence renders as a visible defect rather than as
a claim; controls not assessed in the run are listed explicitly rather than
silently omitted; drift is shown per control, not only in aggregate. The HTML is
self-contained, theme-aware and prints cleanly.

### API

FastAPI, polling only. Sweeps run as background tasks behind a single-flight
lock, so a duplicate trigger is a no-op rather than a second run interleaving
findings into the same tables — this covers the "duplicate trigger no-ops via
run lock" failure scenario from PLAN §8 Phase 7.

Verified against live data: every endpoint returns real records, unknown ids
404, and the packet served over HTTP is leak-free.

### UNVERIFIED, and why

Honest accounting (PLAN §5.5). Everything below is written but has not been run:

- **the sweep itself** — every path through `run_sweep()` calls Bedrock. The
  agent constructs and all 19 tools are individually verified, but the model has
  never chosen one. Blocked on the Bedrock use-case form.
- **`resume_after_decision()`** — the mechanism underneath it is verified live;
  the model-driven half is not.
- **`Dockerfile`** — written, NOT built. The Docker daemon is not running on this
  machine, so the image does not exist. It must not be counted as working.
- **SES** — `SES_FROM`/`SES_TO` unset, so notifications degrade to a logged
  no-op by design. Needs two verified SES identities.

Test count at this point: 47.

### Two bugs CI caught that local runs could not

Both passed cleanly on this machine and failed in GitHub Actions. Recording them
because the *reason* they were invisible locally generalises.

**1. `.gitignore` silently untracked a source package.**

The hardened ignore list contained a bare `evidence/`, intended for sweep output
at the repo root. A pattern without a leading slash matches a directory of that
name at **any depth**, so it also matched `tools/evidence/` — the evidence tool
package. Files already tracked stayed tracked, which is why nothing appeared to
break; but `_wrap.py`, created after the hardening, was never committed. CI
failed with `ModuleNotFoundError: No module named 'tools.evidence._wrap'` — the
redaction boundary itself was missing from the published repo.

Fixed by anchoring the artefact patterns: `/runs/`, `/evidence/`, `/packets/`.
Verified in both directions — the source package is tracked again, and root-level
artefact directories are still ignored.

The local venv reads from the working tree, so a file that exists on disk but is
untracked passes every local test and fails everywhere else. This is precisely
why CI installs the package and imports it rather than trusting a local run.

**2. API tests were integration tests wearing a unit test's clothes.**

Three `/runs/...` and `/approvals/...` 404 tests reached real DynamoDB. They
passed here because this machine has AWS credentials, and failed in CI with
`NoCredentialsError`. Rewritten against moto so the 404 paths are exercised
everywhere.

**Practice adopted:** before pushing a test change, run the suite with
credentials and `HOME` unset to reproduce CI's environment locally, rather than
trusting a normal local run:

```bash
env -u AWS_PROFILE AWS_ACCESS_KEY_ID= AWS_SECRET_ACCESS_KEY= HOME=/tmp/nohome \
  ./.venv/bin/python -m pytest tests/ -q
```

CI is green at 47 tests.

---

## 2026-09-01 — Dashboard, and the pseudonym round trip

### BUG: the agent would have addressed a bucket that does not exist

Found by screenshotting the dashboard, not by reading code.

Redaction scrubs the account id even from exempt `attest-demo-*` names, so the
agent sees `attest-demo-logs-account-<hash>` while S3 only knows
`attest-demo-logs-<account-id>`. The agent would therefore have called
`request_approval` with a name AWS has never heard of. `guard_resource` passes
(the prefix is right), the approval binds to the fake name, `check()` passes
(both sides agree on the fake name) — and then `put_bucket_encryption` fails
with NoSuchBucket. Every guard would have reported success right up to the write.

That is the north-star demo, so it would have failed live.

Fixed with `Redactor.unredact()`, the inverse of `_sweep`, applied at exactly
the two points where a name is used against AWS:

- `request_approval` resolves before creating the record, so the approval binds
  to the resource AWS knows, while echoing the pseudonym back to the agent.
- `enable_s3_kms_encryption` resolves before the guard, the approval check and
  the write.

Everything the model reads still comes back pseudonymised —
`get_approval_status` re-redacts the stored resource — so the agent works
entirely in pseudonyms and never learns a real name.

Verified end to end using only the name the model can see: the agent's observed
bucket name goes into `request_approval`, the record binds to the real bucket,
approval is granted, remediation applies and verifies, the control flips, and
the bucket is restored to re-arm the demo.

**Lesson, and it is the same one as the packet leak:** both bugs sat between two
correct layers. Redaction was right, remediation was right, and the seam between
them was wrong. Unit tests on either side pass. Only exercising the whole path,
end to end, with the values the real caller would use, finds this class of bug.

### The API redacts approvals for display

Approval records store the real resource because remediation needs it. The
dashboard is on screen during demos and recordings, so `/approvals` runs its
response through the same redaction as everything else. The account id was
visible in the approval card until this was fixed — again, caught in a
screenshot.

### Dashboard

React + Vite, polling every 2s, no websockets. Run timeline, controls with
verdicts and cited evidence ids, approval cards, trust packet link.

Node on this machine is broken (Homebrew node 22 against a missing icu4c 74),
but there is a working node 18 at `/usr/local/bin`. Putting that first on PATH
is enough — no need to touch the Homebrew install:

```bash
export PATH=/usr/local/bin:$PATH   # node 18.17.1, npm 9.6.7
cd web && npm install && npm run dev
```

### Verified: the approve flow works through the browser

A click on Approve in the dashboard flipped the approval, fired
`resume_after_decision`, and failed exactly where expected — the Bedrock gate —
with the error caught and written to the audit log rather than crashing the
request. The full path from browser to API to decision to resume is proven;
only the model call at the end of it is blocked.

Test count: 51.

---

## 2026-09-01 — Phases 6 to 9

### DECISION: Lambda, not AgentCore — but AgentCore is genuinely available

PLAN §8 says decide at Phase 6, so this was checked rather than assumed.

AgentCore **is** reachable in this account: `bedrock-agentcore-control` is in
boto3 1.43.83, `list_agent_runtimes` succeeds, and `CreateAgentRuntime` accepts
`agentRuntimeName`, `agentRuntimeArtifact`, `roleArn`. So this is a real choice,
not a fallback. (The AWS CLI on this machine predates the service; use boto3.)

`agentRuntimeArtifact` takes either `containerConfiguration.containerUri` or
`codeConfiguration`. The container path means building an image and pushing to
ECR — and the Docker daemon is not running on this machine, so that cannot even
be attempted today, let alone verified.

Chose **Lambda + EventBridge Scheduler**, on two grounds:

1. It can be verified now. A deployment path that cannot be exercised is not a
   deployment path, and PLAN §5.5 forbids marking unverified work as done.
2. The agent is runtime-agnostic by construction. `agent/handler.py` is the only
   module that knows what invoked it; `agent/attest.py` contains no Lambda
   import, and a test asserts that. Moving to AgentCore later is writing a
   different entry point, not a rewrite.

The container work is not wasted: the Dockerfile is what AgentCore would need,
and `codeConfiguration` remains an option if the image path stays blocked.

### The template is the security story

Two roles, separated by intent:

- **EvidenceRole** — `ReadOnlyAccess` + `SecurityAudit`, plus write access to
  Attest's *own* tables and bucket. It holds no permission to modify the account
  it audits, so a sweep cannot change anything even if the agent is convinced it
  should.
- **RemediationRole** — exactly the two write actions the tools perform, scoped
  to `${DemoPrefix}*` resources, assumable only from EvidenceRole. It can
  `GetItem` and `UpdateItem` on the approvals table (to read an approval and
  burn it) but **not** `PutItem` — the role that performs a write cannot
  manufacture its own authorization.

The prefix guard now exists in two independent places: the tool body and the IAM
policy. Neither is the only thing standing between the agent and a production
resource.

`tests/test_infra.py` asserts the remediation role stays narrow. Verified these
tests actually bite by temporarily widening the policy to `s3:*` on `"*"` and
watching two of them fail — a regression that would otherwise leave the code
guard intact while silently deleting the second layer.

Validated the template with a real CloudFormation change set: all 11 resources
resolve, every `!GetAtt` and `!Sub` valid. Change sets provision nothing, so the
preview stack was deleted without ever creating a resource.

Schedule ships **DISABLED**. A stack that starts sweeping unattended the moment
it is created is a surprise, and the first sweep should be watched.

### deploy.sh checks for the bootstrap collision

The first dry run failed with an opaque
`[AWS::EarlyValidation::ResourceExistenceCheck]` hook error that names nothing.
Cause: `scripts/bootstrap_aws.py` had already created `attest_*` tables and the
evidence bucket outside CloudFormation, which will not adopt resources it did
not create.

The template is fine — re-running with `TABLE_PREFIX=attestx` produced a clean
11-resource change set. But the error is unhelpful enough to lose an hour to, so
`deploy.sh` now checks for the conflicting tables and bucket up front and prints
both ways out (different prefix, or `bootstrap_aws.py --clean`).

### Telemetry

Resolved API for strands-agents 1.54.0: `StrandsTelemetry` from
`strands.telemetry`, with `setup_otlp_exporter()`, `setup_console_exporter()`
and `setup_meter()`. `Agent.__init__` takes `name` and `trace_attributes`.

Strands already emits a span per model call and per tool call, so `tools/
telemetry.py` only picks the exporter and attaches the run context.
`session.id` is set to the run id, so traces group by sweep — the unit an
operator actually asks about after a nightly run.

Off unless `ATTEST_TELEMETRY=1`, and setup failures degrade to a warning:
telemetry that can break a compliance sweep is worse than no telemetry.

Confirmed with the console exporter that spans emit, nest correctly, and carry
`attest.run_id`, `attest.trigger` and `attest.region`.

### Failure scenarios

`tests/test_failure_modes.py` covers the ways this could quietly produce a wrong
answer rather than an obvious error — the failure mode that actually matters
here, since a crash gets noticed and a false PASS does not:

- AccessDenied yields `null`, never `False` and never absent from the unreadable
  list, so an unobservable bucket cannot be reported as compliant *or* as a
  finding nobody observed
- a missing encryption rule is a real FAIL, distinct from an error
- a whole-tool failure returns error-as-data rather than raising
- an AWS-managed `aws/s3` key does not satisfy a customer-managed-key control
- rejection is terminal and stays terminal across repeated checks
- `resume_after_decision` refuses a PENDING approval, and reads the decision
  from the record rather than the caller — so a crafted event cannot assert that
  something was approved
- uncited and invented verdicts are rejected
- no tool writes findings without an active run

Test count: 78.

### Still unverified

The sweep, approve-and-resume through the model, the Docker image (daemon not
running), an actual stack deployment, and SES. Recorded in STATUS.md.

---

## 2026-09-01 — Golden run, and the stack deployed for real

### The scripted model, and what it is honestly worth

Bedrock is still gated, so the agent loop had never run. `tests/fake_model.py`
implements the Strands `Model` interface directly — emitting the Bedrock
converse-stream event shape — and replays a fixed list of turns. The **real**
Strands agent loop drives it: real tool dispatch, real conversation management,
real tool specs.

Being precise about what this proves, because it would be easy to oversell:

- **Proved:** when the model asks for a tool, everything downstream is sound.
  The tool executes, evidence reaches S3 and DynamoDB, the verdict is recorded
  and validated, the approval gate holds, the packet renders with citations.
- **Not proved:** that the model *chooses* the right tools. That judgment is the
  product and cannot be faked. Tool selection stays UNVERIFIED.

A tool input may be a callable, resolved at call time — a real model reads an id
out of the previous tool result before citing it, and a scripted one otherwise
cannot.

### BUG: a fabricated citation was accepted

The golden run found this immediately, and it is the worst class of bug for this
product.

`record_finding` validated that `evidence_ids` was non-empty but never that the
ids **existed**. A model that hallucinated `ev-abc123` would have its verdict
stored looking fully substantiated. The packet catches it at render time and
marks the control uncited — but by then the finding is already in the database
and in the API response, and anyone reading the controls endpoint sees a cited
verdict.

Now validated against the evidence this run actually archived. That also closes
a subtler case: evidence from *another* run can no longer be cited, which would
have meant a packet citing observations made at a different time against
different account state.

The system prompt now states the constraint too, so the agent fails cleanly
rather than by trial and error.

### The approve-and-resume path is verified

Previously only the halves either side of the model call were proven. With the
scripted model the whole demo moment runs: the agent asks, a human approves,
the agent is re-invoked, applies the change, re-reads the resource, and records
a fresh PASS **citing evidence gathered after the change** rather than reusing
the pre-change observation. The bucket really moves AES256 to aws:kms, and the
approval is burnt.

The rejection path too: a model that tries the write anyway after a rejection is
refused by the gate, the bucket is unchanged, and the control stays FAIL.

### Verified: the stack actually deploys

Change-set validation only proves references resolve. IAM policy syntax and
service-side constraints are enforced at create time, so the stack was deployed
for real under `TABLE_PREFIX=attestcf` to avoid colliding with the bootstrapped
resources.

All 11 resources created. Read back from AWS afterwards rather than trusted:

- `RemediationRole` stored with `s3:PutEncryptionConfiguration` /
  `GetEncryptionConfiguration` scoped to `arn:aws:s3:::attest-demo-*`, and
  `iam:UpdateAccessKey` scoped to `user/attest-demo-*`. The prefix confinement is
  real in the deployed policy, not just in the template.
- Approvals TTL `ENABLED` on `expires_at`.
- Schedule created `DISABLED`, as intended.
- Evidence bucket with all four public-access blocks on.

Then torn down completely — stack deleted, the `Retain`-policy tables and bucket
removed by hand, and confirmed no `attestcf` resources remain. Nothing left
accruing cost.

Test count: 94.

---

## 2026-09-01 — The agent actually runs

### What the Bedrock block actually is

Diagnosed properly rather than repeating the error string. `bedrock:GetUseCaseForModelAccess` returns:

> You have not filled out the request form.

The blocker is the **Anthropic use case details form**, an account-level
one-time submission. The flow is `PutUseCaseForModelAccess(formData=...)` then
`CreateFoundationModelAgreement(offerToken=...)`. `ListFoundationModelAgreementOffers`
confirms an offer is available for Sonnet 4.5 — nothing is missing but the form.

The form is company details plus a legal agreement, so it is the account
owner's to submit, not something to automate. (The AWS CLI here predates these
APIs; use boto3.)

**Crucially, the gate is Anthropic-specific.** Probed the other families:
Nova Micro/Lite/Pro, Llama 3.3 and Mistral Large all invoke fine. So the agent
loop could be proven immediately, with the configured default left as Sonnet
4.5 for the submission.

### The Phase 0 gate passes, and so does a full sweep

`scripts/hello_strands.py` with Nova Pro: the agent autonomously selected and
called the STS tool and answered from its output. **Gate passed.**

Then a full sweep against the live account: all 10 controls assessed, every
verdict citing evidence, trust packet generated. The audit log shows the
reasoning that matters:

```
000001  save_evidence     ev-2fd7d13d48
000002  record_finding    FAIL          <- ctrl-mfa-users
000003  record_finding    FAIL          <- ctrl-key-rotation
```

Two verdicts citing **one** piece of evidence: the agent recognised that the
credential report answers both MFA coverage and key rotation, and did not fetch
it twice. That is the behaviour the catalog's `candidate_tools` hints are
supposed to produce, and it is not scripted anywhere.

It also handled partial observability correctly without being told to:

> "13 buckets use SSE-S3 or AWS-managed KMS keys. Bucket
> attest-demo-denied-account-f685301e could not be read due to AccessDenied."

Named the unreadable bucket rather than reporting PASS for it.

### Nova is a poor stand-in, for two specific reasons

Worth recording so nobody mistakes it for a working demo configuration:

1. **Content filters block security findings.** Two sweeps died mid-answer with
   "The generated text has been blocked by our content filters" while the model
   was listing IAM users without MFA. Legitimate audit output reads as sensitive
   to the guardrail.
2. **It skips `request_approval`.** Even after the instruction was tightened, it
   diagnoses the remediable control correctly and does not ask. Sonnet-class
   instruction-following is what the demo assumes.

Llama 3.3 is not an option at all: `This model doesn't support tool use in
streaming mode`.

### BUG: the resume message leaked the real resource name

Third bug of exactly this shape. The approval record stores the real resource
name because AWS needs it, and `resume_after_decision` passed that record value
straight into the message handed to the model. The model then wrote the real
name into its rationale, and from there it reached DynamoDB, the dashboard and
the published packet.

Same pattern as the first two: two correct layers, wrong seam. Redaction was
right, the approval record was right, the handoff between them was not.

A fourth instance surfaced immediately after: the remediation tool resolves the
pseudonym internally and returned the **real** name in its result, which the
agent then archived as evidence. Fixed by applying the redaction decorator to
remediation tools too — it belongs on any tool whose result reaches the model,
not only evidence tools, so it moved from `tools/evidence/_wrap.py` to
`tools/redact.py`.

### BUG: an empty sweep was filed COMPLETE

When the content filter truncated the model's output, the run was recorded
COMPLETE with **zero verdicts**. For a compliance tool that reads as "we checked
and found nothing wrong" — the worst available way to be wrong.

`run_sweep` now checks whether anything was recorded and marks the run FAILED
with an explicit explanation if not.

A related fix: `previous_run` was returning such an empty run as the drift
baseline, which made every control look new and would have hidden a regression.
It now requires the baseline to have recorded verdicts.

### Retry policy

Strands retries only throttling. A malformed tool-use sequence
(`modelStreamErrorException`) killed a whole sweep. `SweepRetryStrategy` adds
transient stream faults; everything else still fails fast so a real bug is not
buried under retries.

### Dashboard

Reworked around the two things that were missing:

- **Evidence is reachable.** Ids were dead text under each verdict; they now
  expand to the raw JSON the AWS API returned. Traceability is the product, so
  it has to be one click away.
- **Severity, SOC 2 refs and drift** are shown, and controls sort worst-first,
  with a headline stating the account's actual posture.

Every tool now logs to the audit trail, not only control-flow ones. The
timeline went from `save_evidence -> ev-dcbed19e33` to
`list_s3_encryption_status -> 14 buckets, 12 without a customer-managed key,
1 unreadable (9133ms)`.

Test count: 97.
