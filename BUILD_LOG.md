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
