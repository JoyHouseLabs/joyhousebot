# Production Eval suites

`suites/` contains immutable, versioned business acceptance datasets. They cover three
release-critical outcomes:

- evidence-backed research that distinguishes facts, inference, and uncertainty;
- governed execution with explicit approval and side-effect boundaries;
- outputs that become named, versionable, shareable Works instead of disappearing in chat.

Bootstrap the checked-in suites, create an Eval run for an exact candidate revision, execute it,
and then bind it to a release gate:

```bash
joyhousebot eval-bootstrap

# Create the Eval run through POST /v1/admin/eval-runs, then execute it:
joyhousebot eval-execute evalrun_<id>
```

Automated Agent Evals can execute a draft revision only when the Run is tied to a matching active
Eval record. The exact revision is frozen into `run_execution_snapshots`. Every result preserves
the source Run, event types, artifacts, verification records, latency, and cost. Re-running an
interrupted Eval skips already recorded cases and finishes the remaining cases.

For a production release gate, require all three suites for the exact Agent revision and keep the
evidence age at 24 hours or less. Model/provider upgrades should create new Eval runs even when the
Agent revision itself did not change.
