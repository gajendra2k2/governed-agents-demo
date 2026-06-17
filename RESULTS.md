# Demo run — results & inference

A captured walkthrough from an end-to-end run on macOS, Python 3.12, Apache Kafka 3.7.1 (KRaft), FastMCP 3.4.2. Reproducible with `make up && make producer & make server & make audit & make demo`.

## Setup snapshot

- Kafka on `localhost:9092`, FastMCP on `127.0.0.1:8000/mcp`.
- Topics created automatically on first produce: `orders`, `audit`, `approvals`.
- `OFFLINE_MODE=true` — Beat 6 used canned per-model responses; Beats 1–5 are pure code paths with no LLM in the loop.
- Sample order from the captured run: `O-4F4D878B`, $39, customer `C001`.

## Results — what each beat actually did

| Beat | Identity         | Tool                                                | Outcome              | What the audit log captured                                                                                       |
|------|------------------|-----------------------------------------------------|----------------------|-------------------------------------------------------------------------------------------------------------------|
| 1    | `agent_basic`    | `list_recent_orders(C001)`                          | `ok` (count=2)       | tier 1, args + count                                                                                              |
| 2    | `agent_basic`    | `cancel_order`                                      | **denied**           | tier 3, reason: *"identity 'agent_basic' not authorized for tier-3 tool 'cancel_order'"*                          |
| 3    | `agent_advanced` | `cancel_order(O-4F4D878B, "customer changed mind")` | **shadow**           | `would_apply=true, would_become_status="cancelled", would_refund_amount=39.0` — order **not actually cancelled**  |
| 4a   | `agent_advanced` | `issue_refund(O-4F4D878B, $49.99)`                  | **awaiting_approval**| `approval_id=A-A62C61D0`                                                                                          |
| 4b   | (human)          | `approve A-A62C61D0`                                | approved by `gajendra` | —                                                                                                               |
| 4c   | `agent_advanced` | `issue_refund(... approval_id=A-A62C61D0)`          | **executed**         | `approver=gajendra, amount=49.99`                                                                                 |
| 6a   | `agent_advanced` | `assess_fraud_risk(C001)`                           | `ok`                 | score 0.33 → routed to **Sonnet** ("medium")                                                                      |
| 6b   | `agent_advanced` | `assess_fraud_risk(C010)`                           | `ok`                 | score 1.00 → routed to **Opus** ("high"); $1591 across 2 countries                                                |

The audit topic captured **7 structured events** with color-coded outcomes (denied=red, shadow=yellow, executed=bold green, awaiting_approval=magenta).

## Inference — what each beat proves

### Beat 1 — *"Agents on real-time data."*
The agent read orders that were produced into Kafka seconds earlier. The data is fresh; the read path is unremarkable on purpose — it's the baseline against which the governance beats become visible.

### Beat 2 — *"The model didn't decide this. The server did."*
The same code path the agent uses for any tool got denied **before any tool logic ran**, by `identity.check()` reading `policy.yaml`. The model never saw the policy. The denial itself was logged. **This is the talk's central inversion: the trust boundary is the server, not the prompt.**

### Beat 3 — *"Shadow mode is a structural property, not a flag the model knows about."*
The agent received a structurally identical response (`ok=true`), with `mode=shadow` only visible to the auditor. The would-be effect (cancel + $39 refund) was computed and logged but never committed. The next time you check the order, it's still `status=placed`. **This is how you validate a tier-3 capability in production traffic safely.**

### Beat 4 — *"Human-in-the-loop is a code primitive, not a Slack message."*
The server suspended the call (returned `awaiting_approval`), waited for an out-of-band human signal (the approver CLI updating SQLite + publishing to the `approvals` topic), then **resumed the same tool call** when re-invoked with the `approval_id`. The audit log shows both the suspension and the execution, with the approver's name. There is no in-LLM-context approval flow; it happens at the server.

### Beat 5 — *"Lineage and auditability are the same primitive at two layers."*
The Kafka `audit` topic carries every tool call as a structured event — same shape as the `orders` topic carries business facts. Part 1 said data-layer lineage; Part 2 said tool-layer auditability; **the demo proves they are one engineering pattern.** Any downstream consumer (compliance dashboard, anomaly detector, replay tool) is a plain Kafka consumer.

### Beat 6 — *"Multi-model routing is an auditable decision, not a vendor brag."*
`C001` (low-spend, single country) → Sonnet. `C010` ($1591 across 2 countries, 2 high-value orders) → Opus. The **routing reason is in the audit log**, so tomorrow you can answer *"why did we spend Opus tokens on C010?"* in one query. This is what "scalable multi-model architecture" looks like as a one-file primitive (`server/routing.py` is 50 lines).

## The thesis, validated

Each beat ran a slide as code. The fence works because:

1. **Policy is one YAML file** that the server reads — not 12 layers of middleware. See [`src/governed_agents/policy.yaml`](src/governed_agents/policy.yaml).
2. **The model is untrusted by design.** It can't pass identity to itself, can't bypass tiers, can't tell shadow from real.
3. **Every governance decision is a Kafka event.** Same shape as your business data. Same tools. Same primitive.

That's the engineering claim: **governance is not a wrapper around agents. It's the data layer and the tool layer doing their jobs.** The repo is the proof.

## Reproducing this run

```bash
make up
# four terminals (each: source .venv/bin/activate):
make producer
make server
make audit
make demo
# during Beat 4, in any terminal:
make approve ID=<the printed approval_id>
```

Total wall-clock from `make demo` start to "Demo complete": ~30 seconds on a M-series Mac (no API calls).

See [`DEMO_SCRIPT.md`](DEMO_SCRIPT.md) for the live-talk version with what to say at each beat, and [`TALK.md`](TALK.md) for the slide ↔ demo mapping table.
