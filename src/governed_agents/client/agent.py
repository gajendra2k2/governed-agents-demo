"""Real Claude agent — a tool loop driven by Opus 4.7.

The agent is handed a single goal at start and uses the MCP server's tools to
investigate, reason, and act — discovering its own authorization boundaries
through the server's responses, just like a real production agent.

What the audience watches on stage:
  * Reasoning text streams live with each turn (rich-formatted green panels).
  * Each tool call is rendered in a cyan box BEFORE it runs.
  * Each tool result is rendered immediately AFTER, color-coded by outcome.
  * Denials are rendered in red — the agent then visibly adapts.
  * When `issue_refund` returns awaiting_approval, the agent pauses and
    surfaces the approval id; you run `make approve ID=...` in another
    terminal; the agent resumes automatically.

Every run dumps to `transcripts/agent-<ts>.json` so you have a perfect-run
replay for talk-day safety. See `client/replay.py`.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from anthropic import Anthropic
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from rich.console import Console
from rich.json import JSON
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text

from ..config import SETTINGS

console = Console()

DEFAULT_MODEL = "claude-opus-4-7"
DEFAULT_IDENTITY = "fraud_investigator"

# Goal phrasing is deliberately open-ended: no hint that some actions might be
# denied, no recommendation about which tool to start with. The whole point is
# to watch the agent encounter its boundaries at runtime, not be told about
# them. If the goal said "stay within your authorization" the agent would skip
# the central denial moment.
DEFAULT_GOAL = (
    "Customer C010 was just flagged by a downstream signal as possibly "
    "fraudulent. Investigate their recent activity, take the actions you "
    "believe are appropriate to protect the business, and document what "
    "you did. End with a clear summary."
)

# Likewise the system prompt teaches one principle — adapt when a tool fails —
# without telegraphing what kind of failure to expect. This makes the agent's
# eventual encounter with a server-side denial feel discovered, not staged.
SYSTEM_PROMPT = (
    "You are an autonomous fraud-investigation agent operating in a "
    "production e-commerce system. You have tools available through MCP. "
    "Investigate first, then act. If a tool returns an error or denial, "
    "ADAPT: find another path to accomplish your goal rather than retrying "
    "the same call. If a tool returns `awaiting_approval`, that is normal "
    "and means a human must approve out of band — clearly signal what's "
    "needed and stop your current turn so a human can act. Always end with "
    "a clear natural-language summary of your investigation and actions."
)

SERVER_URL = f"http://{SETTINGS.mcp_host}:{SETTINGS.mcp_port}/mcp/"
TRANSCRIPTS_DIR = SETTINGS.state_dir.parent / "transcripts"
APPROVAL_POLL_INTERVAL = 2.0
APPROVAL_POLL_TIMEOUT = 180.0


# ── rendering helpers ────────────────────────────────────────────────────────

def render_header(model: str, identity: str, goal: str) -> None:
    console.print()
    console.print(Panel.fit(
        Text.assemble(
            ("Governed Agents — Live Investigation\n\n", "bold cyan"),
            ("model:    ", "dim"), (model + "\n", "yellow"),
            ("identity: ", "dim"), (identity + "  ", "yellow"),
            ("(sent in HTTP header — never visible to the model)\n", "dim italic"),
            ("goal:\n  ", "dim"), (goal, "white"),
        ),
        border_style="cyan",
    ))
    console.print()


def render_turn_marker(turn: int) -> None:
    console.print(Rule(f"[bold]Turn {turn}[/]", style="dim"))


def render_thinking(text: str) -> None:
    if not text.strip():
        return
    console.print(Panel(Text(text.strip(), style="white"),
                        title="agent reasoning", border_style="green",
                        title_align="left"))


def render_tool_call(name: str, args: dict) -> None:
    body = Text.assemble(
        ("call  ", "dim"), (name, "bold cyan"),
        ("\nargs  ", "dim"), (json.dumps(args), "white"),
    )
    console.print(Panel(body, border_style="cyan", title="tool call", title_align="left"))


def render_tool_result(name: str, data) -> None:
    if hasattr(data, "data"):
        data = data.data
    outcome = "ok"
    if isinstance(data, dict):
        if data.get("denied"):
            outcome = "denied"
        elif data.get("status") == "awaiting_approval":
            outcome = "awaiting_approval"
        elif data.get("status") == "executed":
            outcome = "executed"
        elif data.get("mode") == "shadow":
            outcome = "shadow"
        elif data.get("ok") is False:
            outcome = "error"
    style = {
        "ok": "dim white",
        "executed": "bold green",
        "denied": "bold red",
        "awaiting_approval": "magenta",
        "shadow": "yellow",
        "error": "red",
    }[outcome]
    console.print(Panel(JSON(json.dumps(data, default=str)),
                        title=f"result ({outcome})", border_style=style, title_align="left"))


def render_approval_prompt(approval_id: str) -> None:
    console.print()
    console.print(Panel.fit(
        Text.assemble(
            ("⏸  Agent is waiting for human approval.\n\n", "bold magenta"),
            ("In another terminal, run:\n  ", "white"),
            (f"make approve ID={approval_id}\n\n", "bold yellow"),
            ("The agent will resume automatically.", "dim white"),
        ),
        border_style="magenta",
    ))
    console.print()


def render_approval_decision(approval_id: str, status: str, approver: str | None) -> None:
    if status == "approved":
        msg = f"✓ {approval_id} approved by {approver or '(unknown)'}"
        border = "bold green"
    elif status == "rejected":
        msg = f"✗ {approval_id} rejected by {approver or '(unknown)'}"
        border = "bold red"
    else:
        msg = f"⏱ {approval_id} timed out"
        border = "yellow"
    console.print(Panel.fit(Text(msg, style="white"), border_style=border))


# ── core agent loop ─────────────────────────────────────────────────────────

async def _list_mcp_tools(mcp: Client) -> list[dict]:
    raw = await mcp.list_tools()
    schemas = []
    for t in raw:
        schema = t.inputSchema or {"type": "object", "properties": {}}
        schemas.append({
            "name": t.name,
            "description": t.description or "",
            "input_schema": schema,
        })
    return schemas


async def _wait_for_approval(mcp: Client, approval_id: str) -> tuple[str, str | None]:
    """Poll the server until approval_id is decided or we time out.
    Returns (status, approver)."""
    deadline = time.time() + APPROVAL_POLL_TIMEOUT
    last_logged = ""
    while time.time() < deadline:
        res = await mcp.call_tool("check_approval", {"approval_id": approval_id})
        data = res.data if hasattr(res, "data") else res
        status = data.get("status", "unknown")
        approver = data.get("approver")
        if status != last_logged:
            console.print(f"  [dim]approval poll: status={status}[/]")
            last_logged = status
        if status in ("approved", "rejected"):
            return status, approver
        await asyncio.sleep(APPROVAL_POLL_INTERVAL)
    return "timeout", None


async def run_agent(model: str, identity: str, goal: str, max_turns: int = 14) -> dict:
    render_header(model, identity, goal)

    anthropic = Anthropic(api_key=SETTINGS.anthropic_api_key)
    transport = StreamableHttpTransport(SERVER_URL, headers={"x-agent-identity": identity})

    transcript: list[dict] = []
    final_summary = ""

    async with Client(transport) as mcp:
        tool_schemas = await _list_mcp_tools(mcp)
        console.print(f"[dim]connected to MCP server, {len(tool_schemas)} tools available[/]\n")

        messages: list[dict] = [{"role": "user", "content": goal}]

        for turn in range(1, max_turns + 1):
            render_turn_marker(turn)

            try:
                response = anthropic.messages.create(
                    model=model,
                    max_tokens=2048,
                    system=SYSTEM_PROMPT,
                    tools=tool_schemas,
                    messages=messages,
                )
            except Exception as e:
                console.print(Panel(
                    Text.assemble(
                        ("Anthropic API call failed:\n  ", "bold red"),
                        (str(e), "red"),
                        ("\n\nIf this is a credits/quota issue, top up at\n"
                         "https://console.anthropic.com/settings/billing and rerun.\n"
                         "Or use `make agent-replay` to show a recorded run.", "dim white"),
                    ),
                    border_style="red",
                ))
                final_summary = f"(API error: {e})"
                break

            text_blocks = [b.text for b in response.content if b.type == "text"]
            for t in text_blocks:
                render_thinking(t)

            assistant_content = []
            tool_uses = []
            for b in response.content:
                if b.type == "text":
                    assistant_content.append({"type": "text", "text": b.text})
                elif b.type == "tool_use":
                    assistant_content.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
                    tool_uses.append(b)
            messages.append({"role": "assistant", "content": assistant_content})

            transcript.append({
                "turn": turn,
                "stop_reason": response.stop_reason,
                "text": "\n".join(text_blocks),
                "tool_uses": [{"id": b.id, "name": b.name, "input": b.input} for b in tool_uses],
                "ts": datetime.now(timezone.utc).isoformat(),
            })

            if response.stop_reason != "tool_use":
                final_summary = "\n".join(text_blocks).strip()
                break

            tool_results = []
            for use in tool_uses:
                render_tool_call(use.name, use.input)
                res = await mcp.call_tool(use.name, use.input)
                initial_data = res.data if hasattr(res, "data") else res
                render_tool_result(use.name, initial_data)

                # Default: the data the model receives is what the server returned.
                model_data = initial_data
                approval_event = None

                # If the server suspended on an approval, pause for the human, then
                # mutate the tool_result content the model sees so it can resume
                # naturally with the approval_id.
                if isinstance(initial_data, dict) and initial_data.get("status") == "awaiting_approval":
                    approval_id = initial_data.get("approval_id", "")
                    render_approval_prompt(approval_id)
                    status, approver = await _wait_for_approval(mcp, approval_id)
                    render_approval_decision(approval_id, status, approver)
                    approval_event = {"approval_id": approval_id, "status": status, "approver": approver}

                    model_data = dict(initial_data)
                    if status == "approved":
                        model_data["status"] = "approved"
                        model_data["approver"] = approver
                        model_data["instructions"] = (
                            f"Approval {approval_id} has been APPROVED by {approver}. "
                            "Call issue_refund again with the SAME order_id and amount, "
                            f"passing approval_id='{approval_id}' to finalize."
                        )
                    elif status == "rejected":
                        model_data["status"] = "rejected"
                        model_data["instructions"] = (
                            "The refund was REJECTED by a human operator. "
                            "Do not retry — note this in your final summary and stop."
                        )
                    else:
                        model_data["status"] = "timeout"
                        model_data["instructions"] = (
                            "Approval timed out. Stop the investigation and report what you found."
                        )

                # Persist both states so replay can re-show the full drama.
                tr_entry = {
                    "tool_use_id": use.id,
                    "name": use.name,
                    "data": initial_data,
                }
                if approval_event is not None:
                    tr_entry["approval"] = approval_event
                    tr_entry["resolved_data"] = model_data
                transcript[-1].setdefault("tool_results", []).append(tr_entry)

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": use.id,
                    "content": json.dumps(model_data, default=str),
                })

            messages.append({"role": "user", "content": tool_results})

        else:
            console.print(f"[red]hit max_turns={max_turns} without natural stop[/]")

    console.print()
    console.print(Rule("[bold green]Investigation complete[/]"))
    if final_summary:
        console.print(Panel(Text(final_summary, style="white"),
                            title="agent summary", border_style="green", title_align="left"))

    return {
        "model": model,
        "identity": identity,
        "goal": goal,
        "turns": transcript,
        "final_summary": final_summary,
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }


def save_transcript(record: dict) -> Path:
    TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = TRANSCRIPTS_DIR / f"agent-{ts}.json"
    path.write_text(json.dumps(record, indent=2, default=str))
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description="Run the governed agent against the MCP server.")
    ap.add_argument("--model", default=DEFAULT_MODEL, help="Claude model id (default: opus 4.7)")
    ap.add_argument("--identity", default=DEFAULT_IDENTITY, help="Agent identity (sent in HTTP header)")
    ap.add_argument("--goal", default=DEFAULT_GOAL, help="Initial goal for the agent")
    ap.add_argument("--max-turns", type=int, default=14)
    args = ap.parse_args()

    if not SETTINGS.anthropic_api_key:
        console.print("[red]ANTHROPIC_API_KEY not set. Set it in .env, or use `make agent-replay` for offline.[/]")
        return 2

    record = asyncio.run(run_agent(args.model, args.identity, args.goal, args.max_turns))
    path = save_transcript(record)
    console.print(f"\n[dim]transcript saved: {path}[/]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
