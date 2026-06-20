# Demo script — what to say, what to type, what they see

**Total target:** ~13 minutes. Three phases that flow continuously — no Enter-prompts, no scene breaks.

## Pre-flight (do this *before* you start talking)

In four terminal tabs in the repo directory. Each starts with `source .venv/bin/activate`. Test this whole sequence at least once the day before, and again the morning of.

```
T1)  make up && make producer-fraud      # Kafka + seeded fraud burst for C010 + steady stream
T2)  make server                         # MCP server (FastMCP on :8000)
T3)  make audit                          # audit-topic viewer, empty so far
T4)  (leave empty — you'll run `make agent` here on stage)
```

Verify before going on:
- T1 shows `seeded fraud scenario: 6 orders for C010 across 2 countries` then steady-state ticking.
- T2 shows `FastMCP on http://127.0.0.1:8000/mcp` and `identity comes from HTTP header 'x-agent-identity'`.
- T3 shows the audit-viewer banner, no events yet.
- `.venv/bin/python -m pytest -q tests/` passes 10/10.
- The Anthropic billing page shows ≥ $5 remaining.

**Talk-day insurance:** Do one good live run the morning of (with this script). Keep the resulting `transcripts/agent-*.json`. If the live run on stage misbehaves, switch terminals to:
```
make agent-replay
```
The replayer is visually indistinguishable from a live run.

---

## On stage

Open T4. Say:

> *"The slide you just saw mapped governance concepts to MCP mechanisms. The next thirteen minutes is that slide, running. There's one agent. There's one goal. I'm not going to drive the agent — Opus 4.7 will."*

Then:
```
make agent
```

The header panel renders. Say:

> *"The goal is open-ended: a customer was flagged for possible fraud, investigate and take whatever lawful action you can. The model has eight MCP tools available. Watch its identity in the panel — `fraud_investigator`. **That identity is in the HTTP header on the transport. The model literally cannot see it.** If the agent tries to do something this identity isn't authorized for, the server will say no."*

### Phase 1 — Investigate (≈2-3 min)

What the audience sees: agent reasoning streams in a green panel. Then a cyan box: `tool call: list_recent_orders`, args `{"customer_id": "C010"}`. Result panel pops with a list of orders. More reasoning. Maybe `get_order_details` on a specific order. Then `assess_fraud_risk(C010)` — result includes `"model_used": "claude-opus-4-7", "risk_label": "high"`.

What to say (as it happens, with light narration — don't fill silence with talk during the agent's text, let the audience read):

> *"It's reading the orders stream. This isn't a stub — those are real events the producer fed in over the last hour. … Now it's calling `assess_fraud_risk`. **The risk tool internally routes Haiku, Sonnet, or Opus depending on the signal it sees.** The signal for C010 is high, so it picked Opus. That's the 'scalable multi-model architecture' bullet from earlier, running."*

### Phase 2 — Try, fence, adapt (≈2-3 min)

This is the dramatic beat. The agent has decided what to do and is about to act.

What the audience sees: cyan call box for `freeze_customer_account`. Result panel renders in **red**: `denied: true, reason: "identity 'fraud_investigator' not authorized for tier-3 tool 'freeze_customer_account'"`. Then another green reasoning panel — the agent acknowledges the denial and chooses a different path. Followed by `flag_order_for_review` calls (these succeed) and an `issue_refund` call.

What to say at the denial moment:

> *"There it is. The agent decided the right action was to freeze the account. The server said no. **And notice — the model didn't decide to obey. The server decided.** If we changed the prompt to instruct the agent to 'always succeed,' the result would be the same. The governance lives in the server, not the prompt."*

When the agent course-corrects:

> *"And now it's adapting. It can't freeze, but it CAN flag for review — that's tier 2, allowed. And for the order that already shipped, it's requesting a refund through the approval-gated tool. Watch what happens next."*

### Phase 3 — Human approval + audit reveal (≈3-4 min)

What the audience sees: result panel renders in **magenta** with `status: "awaiting_approval", approval_id: "A-XXXXXXXX"`. The agent's reasoning stops. A loud magenta panel appears: `⏸ Agent is waiting for human approval. In another terminal, run: make approve ID=A-XXXXXXXX`. Then a poll: `approval poll: status=pending`...

Switch to T2 (or a fifth terminal you've kept ready):
```
make approve ID=A-XXXXXXXX
```

The approver CLI prints the request — the audience hears the click. Type `y`. Switch back to T4. The agent's poll shows `status=approved`. Reasoning resumes. A final `issue_refund` call with `approval_id=...` returns `status: "executed", approver: "gajendra", amount: 49.99`. Then the agent's natural-language summary streams.

What to say:

> *"Real pause. Real human. I'm not faking this. The agent is blocked on a Kafka-backed approval queue and I'm the human. … Approved. Watch it resume."*

After the agent's summary, switch to T3 (audit viewer):

> *"Now the part that's been quietly happening this whole time. Every single thing you just watched — the reads, the denial, the shadow simulations if there were any, the awaiting-approval, the approval decision, and the final execute — every one of them is a structured event on a Kafka topic. **The audit log IS a stream.** That's the bridge back to Part 1. The same primitive that made the order data lineage-traceable is what makes the agent's behavior auditable. There's no separate observability stack — there's just a topic, and any consumer can join the party."*

Point at the red `denied` event, the green `executed`, the magenta `awaiting_approval`. Say:

> *"Tomorrow's compliance review doesn't need to interview the engineer who built this. They run a query against this topic. **That's what 'governance as engineering' looks like.**"*

---

## Close (≈30s)

Switch back to T4. Say:

> *"One agent. One goal. Eight tools. Three minutes. The agent investigated, the server fenced, a human approved, and the audit log captured everything. **The model never enforced anything. The server did. The data layer did.** That's the move: govern at the layers where you already know how to engineer."*

Repo: github.com/gajendra2k2/feed-and-fence · MIT · one `make agent` away.

Hand off to Q&A.

---

## Failure-mode playbook

| If…                                                | Do this                                                                |
|----------------------------------------------------|------------------------------------------------------------------------|
| Kafka isn't up                                     | `make down && make up`                                                 |
| Producer isn't running                             | Agent's Phase 1 returns no orders → start `make producer-fraud`        |
| Approval doesn't trigger / agent stuck polling     | Run `make approve ID=…` in any terminal                                |
| Anthropic API errors out at Turn 1                 | Switch to replay immediately: `make agent-replay` (it looks live)      |
| Wi-Fi is flaky during Phase 2                      | `make agent-replay` — replays your morning-of recorded run             |
| Agent goes off-script in an unhelpful direction    | Hit Ctrl+C, `make agent-replay`, narrate as if planned                 |
| Audience asks "what about X?"                      | See `TALK.md` Q&A section                                              |

## Why the replay is non-negotiable

Live LLMs are non-deterministic. Across many practice runs the agent will *usually* discover the denial and adapt, but Opus has its own preferences and may occasionally:
- Skip `freeze_customer_account` (no denial drama)
- Try irrelevant tools first
- Be more verbose than your timing allows

The recorded transcript from a **morning-of practice run** is your safety net. Use the replayer on stage if anything looks off — the audience can't tell the difference and the narrative will be the one you rehearsed.
