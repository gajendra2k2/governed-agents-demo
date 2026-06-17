# governed-agents-demo

Companion repo for the talk **"Feeding the Agents — and Fencing Them: Engineering Governed Agentic Systems on Real-Time Data."**

The thesis: **governance is not a policy layer around agents; it is a property of the data layer and the tool layer.** This demo makes that thesis runnable. The MCP server is the trust boundary. Every governance primitive (access control, shadow mode, human-in-the-loop, auditability, multi-model routing) is a small module you can read in under a minute.

> The repo is intentionally small enough to read in one sitting. Each governance primitive lives in its own file under `src/governed_agents/server/`.

## What the demo shows

Six beats, each one re-running a Part 2/3 slide as real code:

| Beat | What happens                                            | Slide it makes concrete                |
|------|---------------------------------------------------------|----------------------------------------|
| 1    | Agent reads live orders from the stream.                | "Agents on real-time data"             |
| 2    | Agent attempts a tier-3 tool → **server denies**.       | Access control & use-case tiering      |
| 3    | High-tier agent calls `cancel_order` → **shadow mode**. | Shadow validation                      |
| 4    | Agent calls `issue_refund` → suspends → operator approves. | Human-in-the-loop                    |
| 5    | Flip to audit viewer: every call is a structured event. | Auditability as a streaming primitive  |
| 6    | `assess_fraud_risk` routes Haiku/Sonnet/Opus by signal. | Scalable multi-model architecture      |

## Architecture (at a glance)

```
                       ┌────────────────────────────┐
                       │   producer.py (synthetic)  │
                       └──────────────┬─────────────┘
                                      │ orders topic
                                      ▼
 demo client ──MCP/HTTP──▶  FastMCP server  ──consume──▶  SQLite order ledger
                              │                          │
                              ├── identity.py (policy enforcement)
                              ├── shadow.py   (simulate writes)
                              ├── approvals.py (HITL coordination)
                              ├── routing.py  (multi-model)
                              └── audit.py ──▶ audit topic ──▶ audit_viewer.py
                                                    ▲
                                                    │
                                  scripts/approve.py (human)
```

## Prerequisites

- **Python ≥ 3.11** — macOS ships 3.9; install a newer one (instructions below).
- **Docker** (Docker Desktop on macOS/Windows, or Docker Engine on Linux).
- **`make`** — preinstalled on macOS via Xcode CLI tools; on Linux via your package manager.

### macOS first-time setup

The system `python` command doesn't exist by default (only `python3` → 3.9). Install a modern Python:

```bash
brew install python@3.12
```

This gives you `/opt/homebrew/bin/python3.12`. Use that to create the venv below.

### Linux first-time setup

```bash
sudo apt-get install -y python3.12 python3.12-venv make docker.io   # Debian/Ubuntu
# or use your distro's equivalent
```

## Quickstart

```bash
git clone https://github.com/gajendra2k2/governed-agents-demo
cd governed-agents-demo
cp .env.example .env                            # edit if you have an Anthropic key

# Create the venv with Python 3.12 specifically (system python3 may be too old).
/opt/homebrew/bin/python3.12 -m venv .venv      # macOS — adjust path on Linux
source .venv/bin/activate
make install                                    # pip install -e .
make test                                       # smoke tests — no Kafka needed yet
```

If you ever see `command not found: python`, your venv isn't activated. Either run `source .venv/bin/activate` again, or invoke directly with `.venv/bin/python`.

### Running the demo (four terminals)

Each terminal must `source .venv/bin/activate` first.

```bash
make up                             # T1 — docker compose up Kafka (KRaft, no ZK)
make producer                       # T1 — start the order stream (leave running)
make server                         # T2 — start the MCP server
make audit                          # T3 — audit viewer (Beat 5 reveal)
make demo                           # T4 — drive the six beats
# during Beat 4, in any terminal:   make approve ID=<approval_id printed by the demo>
```

For the live talk, see [`DEMO_SCRIPT.md`](DEMO_SCRIPT.md) — every beat, every command, every line to say.

### Troubleshooting

| Symptom                                            | Fix                                                                          |
|----------------------------------------------------|------------------------------------------------------------------------------|
| `command not found: python`                        | Venv isn't activated. `source .venv/bin/activate`, or use `.venv/bin/python`.|
| `ERROR: Package requires a different Python: 3.9.x`| Your venv used system Python. Recreate with `python3.12 -m venv .venv`.      |
| `make up` hangs / Kafka container restarts         | Docker Desktop isn't running, or port 9092 is taken. Stop the conflict, retry.|
| Beat 6 fails with auth error                       | Set `OFFLINE_MODE=true` in `.env` and restart the server.                    |
| `make demo` says "No orders found"                 | Producer isn't running. `make producer` in another terminal, then rerun.     |

## Offline mode (for unreliable conference Wi-Fi)

Set `OFFLINE_MODE=true` in `.env`. Beats 1–5 never call an LLM, so they're already offline. Beat 6 (multi-model routing) returns canned per-model responses so the demo never fails on the network.

## Repo layout

```
src/governed_agents/
  config.py        # env config
  topics.py        # Kafka topic names
  policy.yaml      # identity → tier → tools (THE governance contract)
  producer.py      # synthetic order event stream
  llm.py           # Anthropic wrapper with offline canned-response mode
  server/
    app.py         # FastMCP server entry + orders→ledger consumer
    identity.py    # AccessDenied lives here
    audit.py       # writes structured events to the audit Kafka topic
    shadow.py      # simulate writes, return would-be effect
    approvals.py   # request / decide approval, SQLite + Kafka
    routing.py     # Haiku/Sonnet/Opus by risk signal
    state.py       # SQLite store (orders + approvals)
    tools.py       # the six tools, each wrapped through identity.check
  client/
    demo.py        # six-beat scripted client
scripts/
  approve.py       # operator CLI for Beat 4
  audit_viewer.py  # color tail of the audit topic
```

## Production hardening (what this demo deliberately doesn't do)

This is a teaching artifact, not a production reference. The real-system path is:

- **Identity is passed as a tool arg here** — clear for the audience, wrong for production. Real systems carry identity in transport (OAuth/OIDC, mTLS, signed headers) so the model never sees it and can't impersonate.
- **No tenant isolation.** A real multi-tenant deployment partitions Kafka topics, SQLite (→ a proper DB), and approval queues by tenant.
- **Approvals coordination via SQLite.** Fine for one box; a real deployment uses a durable queue + signed approval tokens.
- **No rate limiting / cost ceilings on routing.** Real multi-model routing needs per-tier budgets and circuit breakers.

These are good Q&A material — and a slide in Part 3.

## License

MIT — see [LICENSE](LICENSE).
