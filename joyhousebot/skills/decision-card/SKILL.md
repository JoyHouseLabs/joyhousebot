---
name: decision-card
description: Output structured decision cards with conclusion, evidence, risks, uncertainties, and next actions.
requires: []
always: false
---

# Decision card output format

When the user asks for a **decision**, **recommendation**, or **conclusion** that should be backed by evidence (e.g. after retrieving from the knowledge base), format your reply as a **decision card** with the following sections. Use clear headings so the structure is machine- and human-readable.

## Template

1. **Conclusion** — Your main conclusion or recommendation in one or two sentences.
2. **Evidence** — Bullet points or short quotes from retrieved sources; each with a trace (source, page or doc_id if available).
3. **Risks / Caveats** — What could go wrong or what limits apply.
4. **Uncertainties** — What remains unclear or would need more information.
5. **Next actions** — Concrete next steps the user could take (e.g. read X, run Y, decide Z by when).

## Example (short)

**Conclusion:** Prefer option A under the current constraints.

**Evidence:**
- Source [doc_id: abc1, page 3]: "…"
- Source [url]: "…"

**Risks:** Assumption X may not hold in region Y.

**Uncertainties:** No data yet on Z.

**Next actions:** Run experiment E; revisit in 2 weeks.

Use this structure whenever the user explicitly asks for a decision, recommendation, or evidence-based summary. When you used the `retrieve` tool, cite the returned `doc_id` or `source_url` in Evidence.
