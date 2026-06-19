# Talk outline — slide ↔ demo mapping

**Title:** Feeding the Agents — and Fencing Them: Engineering Governed Agentic Systems on Real-Time Data
**Throughline:** *"An agent is only as good as the data you feed it and the fences you give it — and both are infrastructure you engineer, not features you bolt on."*
**Slot:** 50 min total · 32 min content · 13 min demo · 5 min Q&A

## Part 0 — Framing (4 min)

- The industry has moved from "AI feature" to "AI-native." But most AI-native talks stop at the model and the prompt.
- The two things that decide whether an agentic system is trustworthy at scale are **invisible in demos**: what data reaches the agent, and what the agent is allowed to do with it.
- Thesis: governance is not a wrapper around agents. It is a property of the data layer and the tool layer. Today we engineer both.

## Part 1 — FEED: the real-time data layer (10 min)

Engineering anchor, vendor-neutral.

- Why agents quietly fail on stale data: confidently wrong, won't show in eval, shows in production.
- Batch / RAG-only is insufficient for agents that *act*.
- Three properties the data layer must give an agent:
  - **Freshness** — event time, not batch.
  - **Consistency** — idempotent writes, no double-action.
  - **Lineage** — every fact the agent saw is traceable. **← Bridge to Part 2: lineage is already an auditability primitive.**
- One abstracted field story (timeout / offset / consistency).

## Part 2 — FENCE: the agent–tool boundary as a governance surface (10 min) *— heart of the talk*

- LLM that talks → prompt safety. Agent that *acts* → **tool governance.** Different problem.
- MCP reframed: not "a way to give Claude tools" but **a typed, auditable, access-controlled contract for what an agent may touch.**
- **The model is untrusted; the server is the trust boundary.** That inverts where you put your engineering effort.
- Map governance concepts → concrete MCP mechanisms (this slide previews what the audience is about to watch the agent encounter):

| Governance concept    | MCP mechanism                                       | Lives in repo                            | What the agent will do live |
|-----------------------|-----------------------------------------------------|------------------------------------------|------------------------------|
| Identity & access     | Server reads identity from HTTP header, not from a tool arg | `server/identity.py` (`from_http`)       | Try `freeze_customer_account` → **denied** |
| Use-case tiering      | Tools partitioned by tier in `policy.yaml`          | `policy.yaml`, `server/identity.py`      | Discover its tier at runtime via denial |
| Shadow mode           | Write tools simulate + log instead of executing     | `server/shadow.py`                       | Available but not used in this run |
| Human oversight       | Approval-gated tools suspend until human confirms   | `server/approvals.py` + `scripts/approve.py` | Request refund → pause → you approve live |
| Auditability          | Every call → structured event on the audit Kafka topic | `server/audit.py` + `scripts/audit_viewer.py` | Audit reveal at the end |

## Part 3 — SCALE: what breaks (8 min)

- **Multi-model routing** by cost/risk with logged routing decisions. → Visible in the agent's `assess_fraud_risk` call: the routing tool picks Opus when the signal is high, Sonnet when medium, Haiku when low.
- Tool-call latency, tracing agent→tool→data, timeout/retry, idempotency under retries.
- Encoding operational expertise as reusable agent capability (Skills/subagents).
- Close: **governance is engineering — it lives in the data layer and the tool layer, not in a policy deck.**

## Demo (13 min) — one story, three phases

See [`DEMO_SCRIPT.md`](DEMO_SCRIPT.md) for the staged version with what to say at each phase.

The agent is given **one** open-ended goal:

> *"Customer C010 was just flagged by a downstream signal as possibly fraudulent. Investigate, take any action you are authorized to take, and escalate the rest."*

Then it runs autonomously while the audience watches:

1. **Phase 1 — Investigate.** Agent calls `list_recent_orders`, `get_order_details`, then `assess_fraud_risk`. The signal is high (5+ high-value orders across 2 countries in an hour). The routing tool sends it to Opus. The agent now has a real basis for action.
2. **Phase 2 — Try, fence, adapt.** Agent attempts `freeze_customer_account`. Server returns **denied** (tier 3 — humans only). The audience sees the model receive an access-control response and visibly course-correct: it falls back to `flag_order_for_review` for the in-flight orders and requests `issue_refund` for the one already shipped.
3. **Phase 3 — Human approval + audit reveal.** Refund hits the approval queue. You approve in a second terminal. Agent resumes, finalizes the refund, summarizes findings. Then flip to the audit viewer: **every** decision the agent made is there — including the denied attempt — as structured events on a Kafka topic. Same primitive as the orders stream. Part 1 and Part 2 meet here.

## Q&A (5 min)

Likely questions to be ready for:

- *Why MCP and not just function-calling?* MCP makes the boundary a process with its own auth, observability, and lifecycle. Function-calling collapses it into the LLM call.
- *Couldn't the agent just lie about its identity?* No — identity is in the HTTP header set on the transport at connection time. The model never sees it, can't read it, can't write to it. Demo this fact: search the agent's reasoning for the word "fraud_investigator" — it's never there.
- *Won't the agent ignore your prompt and try the denied tool anyway?* It might. That's the entire point — the server denies regardless of what the model decided. Watch the run; even if Opus tries something forbidden, the audit log shows the denial.
- *Doesn't shadow mode mean the agent learns to expect success?* Yes — that's why you keep shadow and execute structurally identical, and analyze would-be effects separately.
- *Cost of multi-model routing?* Budgeted per tier. See "Production hardening" in README.
- *Why Kafka vs. just a database?* Lineage is the through-line. The audit log IS a stream, by design — same primitive as the data layer.
- *What about prompt injection?* Out of scope for this talk. Prompt injection threatens what the agent *says*; the server fences what the agent *does*. Both matter — they're different talks.
