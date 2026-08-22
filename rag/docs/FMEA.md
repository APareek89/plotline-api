# FMEA — complete Plotline RAG flow

**Analyzed at:** `uncommitted:923e098ae54d` (application-content fingerprint; the user reserves commits)
**Scan scope:** 35 project files, 2,077 lines · **Product context:** local-first citable retrieval within a strict free-tier fence (`README.md`)
**Failure modes found:** 4 (0 P0, 0 P1, 4 P2)

| # | Component | Failure Mode | Effect | Root Cause | S | O | D | RPN | Priority |
|---|---|---|---|---|---:|---:|---:|---:|---|
| 1 | `data/best_practice.jsonl` | Development seeds are used as production evidence before replacement. | Agents cite development-only guidance. | The separate validated KB output was unavailable. | 8 | 2 | 2 | 32 | 🟢 P2 |
| 2 | `schema.py:104` | A validator request near 100 IDs can be throttled at 1 RCU. | The endpoint returns a logged 503 and the agent output fails closed. | Strongly consistent single-item reads favor correctness over high batch throughput. | 5 | 2 | 4 | 40 | 🟢 P2 |
| 3 | `Makefile:5` | Another local process owns port 8787. | Default startup fails immediately. | The frozen contract uses a fixed default local port. | 3 | 5 | 1 | 15 | 🟢 P2 |
| 4 | `README.md:37` | Project credentials cannot enforce bucket-level Public Access Block. | Defense in depth depends on the account default and the absence of a future admin-added public policy. | The deliberate IAM fence excludes `s3:PutBucketPublicAccessBlock`. | 7 | 1 | 3 | 21 | 🟢 P2 |

## 🟢 P2 — tracked mitigations

1. Replace the seed JSONL with validated `plotline-kb` output before production; the seed tier, URN URLs, README warning, and Handoff pending item make misuse visible.
2. Keep validator payloads small in the orchestrator; if real outputs approach dozens of citations, add a paced consistent `BatchGetItem` implementation rather than increasing RCU.
3. Stop the unrelated listener or run `make serve PORT=8788`; the service contract remains 8787 by default.
4. Keep all bucket/object writes private and do not add ACL/policy mutation to this identity. Public-access-block configuration remains an account-admin control.

**Coverage:** 12/12 categories checked. Nothing found in `unhandled_error_paths`, `external_dependency_failures`, `race_conditions_and_state`, `data_integrity_partial_writes`, `billing_credit_mismatches`, or `retry_idempotency_issues` after fixes. Findings above cover resource exhaustion, security/access control, observability, scale/load, config drift, and the missing-production-corpus edge case.
