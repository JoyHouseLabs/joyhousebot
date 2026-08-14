# JoyhouseBot Rerank Capability

`capability-rerank` is an optional, provider-neutral capability for reordering
**already authorized** retrieval candidates. It never searches a knowledge base,
opens a database connection, stores candidate text, or calls a vendor API.

The built-in `lexical-v1` scorer is a transparent local baseline: it scores query
term coverage, candidate term coverage, and exact-query containment. It is useful
for local deployments and as an auditable fallback, not as a claim of semantic
ranking quality.

## Capability contract

```text
retrieval.rerank@0.1.0
input:  query + up to 50 {candidate_id, text} entries
output: ranked {candidate_id, score, rank} entries only
permission: context.read
side effect: read
```

Candidate scope is enforced by the retrieval caller (normally Context Assets and
the Runtime's `user_id + agent_id + root_run_id` scope). This extension is not a
second retrieval system and must never accept a source URI, SQL expression, or
arbitrary search filter.

Install and allow it explicitly:

```bash
uv pip install -e extensions/capability-rerank
```

```json
{
  "extensions": {
    "allowedIds": ["capability-rerank"],
    "initiallyActive": ["capability-rerank"]
  }
}
```

For a remote neural reranker, ship a separate provider-specific extension with the
same narrow input/output contract. It must record its provider/model/version and
usage in Capability output/Trace, and must be approved under the deployment
allowlist before it receives private candidates.
