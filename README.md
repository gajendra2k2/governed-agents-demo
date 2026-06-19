# governed-agents-demo

Companion repo for the talk **"Feeding the Agents — and Fencing Them: Engineering Governed Agentic Systems on Real-Time Data."**

The thesis: **governance is not a policy layer around agents; it is a property of the data layer and the tool layer.** This demo makes that thesis runnable.

A real Claude Opus 4.7 agent is handed one open-ended goal — *"investigate possible fraud on customer C010 and act"* — and runs autonomously against an MCP server that fences what it's allowed to do. The audience watches the agent:

1. **Read** a live Kafka stream of orders and notice a suspicious burst.
2. **Try** to take strong action (`freeze_customer_account`) and **get denied by the server** — not by its prompt.
3. **Adapt**: fall back to `flag_order_for_review` and request a refund via an approval-gated tool.
4. **Pause** for a real human to approve in another terminal.
5. **Resume**, finalize the refund, summarize what it found.

The audit topic captures every decision — including the denied attempt — as structured events on Kafka. That same primitive (the streaming lineage from the data layer) becomes auditability at the tool layer.

> The repo is small enough to read in one sitting. Every governance primitive lives in its own ~40-line file under `src/governed_agents/server/`, so when the agent demo finishes, anyone who wants to understand the trick can.

## What's different about this demo

| Common mistake in "agent demos"                                                                            | What this repo does instead                                                                                                      |
|------------------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------|
| "The agent did X" — but X was hardcoded in the script                                                      | A real Opus 4.7 agent picks tools, reasons, and adapts. Every tool call you watch is a real model decision.                       |
| Identity is passed as an argument the LLM controls                                                         | Identity travels in an HTTP header set on the MCP transport. The model literally can't read or change it.                         |
| The streaming data layer is decoration — the "agent" never reacts to it                                    | The producer seeds a deterministic fraud burst for C010. The agent must read the stream to do its job.                            |
| Beats are pressed-Enter slides made of JSON                                                                | One continuous run. Reasoning streams live; tool calls and results render in real time; denials force visible course-correction.  |
| Multi-model routing is a contrived demo step                                                               | The `assess_fraud_risk` tool routes Haiku/Sonnet/Opus internally by signal. The audience sees it in the audit log, no extra beat. |
| Human-in-the-loop is hand-waved                                                                            | The agent really pauses. You really approve in another terminal. The audit log really records both.                                |

## Architecture

```
                       ┌────────────────────────────┐
                       │  producer (fraud scenario) │
                       └──────────────┬─────────────┘
                                      │ orders topic
                                      ▼
   agent (Opus 4.7) ──MCP/HTTP──▶  FastMCP server  ──consume──▶  SQLite order ledger
   header: x-agent-identity        │
                                   ├── identity.py (HTTP-header → policy)
                                   ├── shadow.py   (simulate writes)
                                   ├── approvals.py (HITL coordination)
                                   ├── routing.py  (Haiku/Sonnet/Opus)
                                   └── audit.py ──▶ audit topic ──▶ audit_viewer.py
                                                         ▲
                                                         │
                                       scripts/approve.py (human in 2nd terminal)
```

## Prerequisites

- **Python ≥ 3.11** — macOS ships 3.9; install a newer one (instructions below).
- **Docker** (Docker Desktop on macOS/Windows, or Docker Engine on Linux).
- **`make`** — preinstalled on macOS via Xcode CLI tools; on Linux via your package manager.
- **Anthropic API key** for the live run (`ANTHROPIC_API_KEY=sk-ant-...`).
  Not needed for `make agent-replay` (replays a saved transcript with no API call).

### macOS first-time setup

The system `python` command doesn't exist by default (only `python3` → 3.9). Install a modern Python:

```bash
brew install python@3.12
brew install --cask docker      # if Docker Desktop not already installed
```

### Linux first-time setup

```bash
sudo apt-get install -y python3.12 python3.12-venv make docker.io   # Debian/Ubuntu
```

## Quickstart

```bash
git clone https://github.com/gajendra2k2/governed-agents-demo
cd governed-agents-demo
cp .env.example .env                            # add your ANTHROPIC_API_KEY

# Venv with Python 3.12 specifically (system python3 may be too old).
/opt/homebrew/bin/python3.12 -m venv .venv      # macOS — adjust path on Linux
source .venv/bin/activate
make install
make test                                       # smoke tests (no Kafka needed yet)
```

If you ever see `command not found: python`, your venv isn't activated. Either `source .venv/bin/activate`, or invoke as `.venv/bin/python`.

## Running the demo — four terminals

Each terminal: `source .venv/bin/activate` first.

```bash
# T1 — Kafka + the fraud-seeded order stream
make up && make producer-fraud

# T2 — MCP server (reads x-agent-identity header on every request)
make server

# T3 — color tail of the audit topic (Phase 3 reveal)
make audit

# T4 — the live agent run; reasons, picks tools, hits a denial, requests approval
make agent

# When the agent pauses for approval, it prints  >>>  make approve ID=A-XXXXXXXX
# Run that in ANY terminal (or T1 after Ctrl+C the producer) and answer y.
# The agent resumes automatically.
```

Approximate timing on Opus 4.7: 4-6 min agent investigation + ~30s human approval + ~2 min audit reveal + narration = **~8-12 min of live demo**, which fits the ~13 min slot in [`TALK.md`](TALK.md). Faster on Sonnet 4.6 (~5-7 min total) but with slightly less impressive reasoning text on stage.

## Talk-day insurance: replay a recorded run

Every `make agent` writes a JSON transcript to `transcripts/agent-<utc>.json`. If conference Wi-Fi or Anthropic API has a bad moment on talk day, use the replay:

```bash
make agent-replay                       # replays the latest transcript
make agent-replay FILE=transcripts/agent-20260617T120000Z.json   # specific file
```

The replay re-renders the agent's reasoning, tool calls, the approval prompt, and the human's decision at a stage-friendly pace — visually indistinguishable from a live run.

For the talk, do one good live run the morning of, then keep the resulting transcript as your fallback. To ship a canonical run *inside* this repo so anyone can `make agent-replay` without an API key:
```bash
cp transcripts/agent-<your-best-run>.json transcripts/canonical.json
git add -f transcripts/canonical.json   # the gitignore allows this exact file
git commit -m "Add canonical replay transcript" && git push
```

## Repo layout

```
src/governed_agents/
  config.py           # env config
  topics.py           # Kafka topic names
  policy.yaml         # identity → tier → tools (THE governance contract)
  producer.py         # synthetic order stream + --scenario fraud
  llm.py              # multi-model wrapper with offline canned-response mode
  server/
    app.py            # FastMCP server, identity from HTTP header, 8 tools
    identity.py       # AccessDenied + from_http() (reads x-agent-identity)
    audit.py          # writes structured events to the audit Kafka topic
    shadow.py         # simulate writes, return would-be effect
    approvals.py      # request / decide approval, SQLite + Kafka
    routing.py        # Haiku/Sonnet/Opus by risk signal
    state.py          # SQLite store (orders + approvals)
    tools.py          # 7 governance-checked tools + check_approval meta-tool
  client/
    agent.py          # the real agent: Opus 4.7 tool loop with live rendering
    replay.py         # replay a saved transcript at stage pace
scripts/
  approve.py          # operator CLI for human-in-the-loop
  audit_viewer.py     # color tail of the audit topic
```

## Tools & tiers (the governance contract)

| Tool                       | Tier | Identities allowed       | Behavior                                  |
|----------------------------|------|--------------------------|-------------------------------------------|
| `list_recent_orders`       | 1    | viewer, fraud_investigator, ops_human | Read |
| `get_order_details`        | 1    | viewer, fraud_investigator, ops_human | Read |
| `assess_fraud_risk`        | 1    | viewer, fraud_investigator, ops_human | Read — internally routes Haiku/Sonnet/Opus by signal |
| `flag_order_for_review`    | 2    | fraud_investigator, ops_human         | Logged + executed |
| `freeze_customer_account`  | 3    | ops_human (humans only)               | **Denied for any agent** — the central "fence" moment |
| `cancel_order`             | 4    | ops_human                             | Shadow mode by default |
| `issue_refund`             | 5    | fraud_investigator, ops_human         | Approval-gated; agent must wait for human |
| `check_approval`           | —    | (no policy gate — meta-tool)          | Polls approval status — used by the agent to detect when a human has decided |

The `fraud_investigator` identity (used by the agent) gets tiers 1, 2, and 5 — by design it can investigate and request refunds, but **cannot freeze or cancel directly**. That's the gap the agent discovers at runtime.

## Production hardening (what this demo deliberately doesn't do)

A teaching artifact, not a production reference.

- **Identity in HTTP header is realistic in shape but not in form.** Production should sign the header with OAuth/OIDC or mTLS so the agent can't fabricate one. Here the client sets it on connection trust.
- **Single-tenant.** Real multi-tenant deployments partition Kafka topics + DB + approval queues by tenant.
- **SQLite for approval coordination.** Fine for one box; production uses a durable queue + signed approval tokens.
- **No rate limiting / cost ceilings on the routing tool.** Real multi-model needs per-tier budgets and circuit breakers.

These are all good Q&A material — and a slide in the talk's Part 3.

## Troubleshooting

| Symptom                                             | Fix                                                                          |
|-----------------------------------------------------|------------------------------------------------------------------------------|
| `command not found: python`                         | Venv not activated. `source .venv/bin/activate`, or use `.venv/bin/python`.  |
| `ERROR: Package requires a different Python: 3.9.x` | Recreate venv with `python3.12 -m venv .venv`.                                |
| `make up` hangs / Kafka restarts                    | Docker Desktop not running, or port 9092 in use. Stop the conflict, retry.   |
| Agent crashes on Turn 1 with `BadRequestError`      | Check `ANTHROPIC_API_KEY` in `.env`; check Anthropic billing has credits.    |
| `make agent` says "MCP server not reachable"        | The server isn't up — `make server` in another terminal.                     |
| Agent skips reading the stream                      | Producer isn't running. `make producer-fraud`.                                |
| Stuck in approval poll                              | Run `make approve ID=<id>` in any terminal. The agent waits up to 120s.       |

## License

MIT — see [LICENSE](LICENSE).
