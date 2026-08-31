# Architecture

## The shape of it

One Strands agent, a set of tools, and two places where control leaves the model
and enters code: the approval gate and the redaction boundary.

```mermaid
flowchart TB
    subgraph triggers[Triggers]
        EB[EventBridge Scheduler<br/>nightly]
        API_T[POST /runs]
        CLI[CLI: attest sweep]
    end

    subgraph agentloop[Strands agent loop]
        AG["Agent<br/>Claude Sonnet 4.5 on Bedrock<br/>SlidingWindowConversationManager"]
    end

    subgraph tools[Tools the agent chooses from]
        direction LR
        EV["10 evidence tools<br/>read-only boto3"]
        CF["control flow<br/>save_evidence, record_finding,<br/>request_approval, packet"]
        RM["remediation<br/>approval-gated writes"]
    end

    subgraph redaction[Redaction boundary]
        RD["pseudonymize<br/>tool results"]
    end

    subgraph aws[AWS read surface]
        IAM[IAM]
        S3R[S3]
        CT[CloudTrail]
        GD[GuardDuty]
        EC2[EC2]
        CFG[Config]
    end

    subgraph store[State]
        DDB[("DynamoDB<br/>runs · controls · evidence<br/>approvals TTL 24h · audit_log")]
        S3E[("S3<br/>evidence · trust packets")]
    end

    subgraph human[Human]
        SES[SES email]
        DASH[React dashboard<br/>polling]
        H((Approve / Reject))
    end

    EB & API_T & CLI --> AG
    AG <-->|"model decides which,<br/>in what order"| tools
    EV --> aws
    aws --> RD --> AG
    CF --> DDB & S3E
    CF -->|request_approval| SES --> H
    DASH --> H
    H -->|"APPROVED record"| DDB
    DDB -->|"checked in code"| RM
    RM -->|"write, then re-read"| S3R
    RM -->|verified state| AG
    DDB & S3E --> DASH

    classDef gate fill:#c62828,stroke:#8e0000,color:#fff
    classDef safe fill:#0a7d33,stroke:#065a24,color:#fff
    class RM gate
    class RD safe
```

## Why the agent is load-bearing

`controls/catalog.yaml` lists `candidate_tools` per control. It does not define
an order, and no Python anywhere iterates it to "run the sweep". The agent reads
the catalog, decides which tools to call, notices that one credential report
answers two controls, falls back to a different tool when one fails, and decides
when it has enough to judge each control.

The clearest evidence that this matters is partial observability. One seeded
bucket denies `s3:GetEncryptionConfiguration`. The tool returns
`encrypted: null` with an error code rather than raising, and the agent has to
decide that this specific bucket is unobservable — marking the control
`INDETERMINATE` rather than reporting `PASS` for a bucket it could not read, or
aborting the sweep. That judgment is not expressible as a fixed sequence.

## The two boundaries

### 1. Approval gate — control leaving the model

```mermaid
sequenceDiagram
    participant A as Agent
    participant D as DynamoDB
    participant H as Human
    participant T as Remediation tool
    participant S3 as S3

    A->>D: request_approval(action, resource)
    D-->>A: approval_id, PENDING
    Note over A: does not wait —<br/>records failing verdict, continues sweep
    D->>H: SES email
    H->>D: APPROVED (dashboard or CLI)
    D->>A: re-invoke with the decision
    A->>T: enable_s3_kms_encryption(bucket, approval_id, key)
    T->>T: guard_resource(bucket) — demo prefix only
    T->>D: check(approval_id, action, resource)
    D-->>T: APPROVED · bound · unexpired · unused
    T->>S3: put_bucket_encryption
    T->>S3: get_bucket_encryption (verify own work)
    S3-->>T: aws:kms, customer-managed
    T->>D: mark_applied — burn the approval
    T-->>A: observed post-state
    A->>D: record_finding(PASS, fresh evidence)
```

Both checks live in the tool body. A model asserting approval is not
authorization, and an approved request for a resource outside the demo prefix is
still refused. Approvals are bound to one `(action, resource)` pair, expire
after 24h, and are single-use.

### 2. Redaction boundary — identities leaving AWS

Pseudonymization is applied *inside* `@tool`, so the model never receives a real
IAM user name or email. Identities therefore cannot reach the conversation
transcript, the dashboard timeline, a screenshot, or a demo recording — not just
the stored evidence.

It is deterministic, so the same principal maps to the same pseudonym across
runs and drift comparison still works; structure-preserving, so counts and
cardinality survive; and reversible only through a local, gitignored map.

## Data model

| Table | Key | Holds |
|---|---|---|
| `attest_runs` | `run_id` | one sweep: status, trigger, summary |
| `attest_controls` | `run_id` + `control_id` | verdict, rationale, cited evidence ids |
| `attest_evidence` | `run_id` + `evidence_id` | pointer to raw JSON in S3, plus a copy |
| `attest_approvals` | `approval_id` | action, resource, status, `expires_at` (TTL) |
| `attest_audit_log` | `run_id` + `seq` | append-only record of every tool call |

Evidence is stored as a JSON string rather than a native DynamoDB map. That
sidesteps float/Decimal conversion entirely and keeps a byte-exact copy of what
the tool returned — which is what "cite the evidence" requires.

## Deployment

The agent is importable and runtime-agnostic: nothing in `agent/attest.py` knows
how it was invoked. The same container serves the local CLI, AgentCore Runtime,
or Lambda behind API Gateway. That choice is deferred to Phase 6 and is a
deployment decision only.

Two IAM roles, separated by intent:

- **`EvidenceRole`** — `ReadOnlyAccess` / `SecurityAudit`. Used for all sweeps.
- **`RemediationRole`** — only the specific write actions the remediation tools
  need, assumable only through the approval-checked code path.
