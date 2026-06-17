"""Real Claude agent — a tool loop driven by Opus 4.7.

The agent is handed a single goal at start and uses the MCP server's tools
to investigate, reason, and act — discovering its own authorization
boundaries through the server's responses, just like a real production agent.

What the audience watches happen on stage:
  * Reasoning text streams live with each turn (rich formatting).
  * Each tool call is rendered in a cyan box BEFORE it runs.
  * Each tool result is rendered immediately AFTER, color-coded by outcome.
  * Denials are rendered in red — the agent then visibly adapts.
  * When `issue_refund` returns awaiting_approval, the agent pauses and
    surfaces the approval id; you run `make approve ID=...` in another
    terminal; the agent resumes automatically.

Every run dumps to `transcripts/<ts>.jsonl` so you have a perfect-run replay
for talk-day safety. See `client/replay.py`.
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
DEFAULT_GOAL = (
    "Customer C010 was just flagged by a downstream signal as possibly fraudulent. "
    "Investigate their recent activity, take any action you are authorized to take "
    "to protect the business, and escalate the rest via the appropriate mechanism. "
    "Be decisive but careful — irreversible actions should not be taken lightly. "
    "When you're done, summarize what you found and what you did."
)

SYSTEM_PROMPT = (
    "You are an autonomous fraud-investigation agent operating in a production "
    "e-commerce system. You have a set of tools available through MCP. Some "
    "tools require human approval (you will see `awaiting_approval` in the "
    "response — that is normal and you should signal it clearly so a human can "
    "act). Some tools may be denied by the server based on your authorization "
    "tier — when this happens, ADAPT: find another lawful path to accomplish "
    "your goal rather than retrying the denied tool. Always end with a clear "
    "natural-language summary of your investigation and actions."
)

SERVER_URL = f"http://{SETTINGS.mcp_host}:{SETTINGS.mcp_port}/mcp/"
TRANSCRIPTS_DIR = SETTINGS.state_dir.parent / "transcripts"
APPROVAL_POLL_INTERVAL = 2.0
APPROVAL_POLL_TIMEOUT = 120.0


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


def render_tool_result(name: str, result) -> None:
    data = result.data if hasattr(result, "data") else result
    outcome = "ok"
    if isinstance(data, dict):
        if data.get("denied"):
            outcome = "denied"
        elif data.get("status") == "awaiting_approval":
            outcome = "awaiting_approval"
        elif data.get("mode") == "shadow":
            outcome = "shadow"
        elif data.get("ok") is False:
            outcome = "error"
    style = {
        "ok": "dim white",
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


# ── core agent loop ─────────────────────────────────────────────────────────

async def _list_mcp_tools(mcp: Client) -> list[dict]:
    tools = await mcp.list_tools()
    schemas = []
    for t in tools:
        schema = t.inputSchema or {"type": "object", "properties": {}}
        schemas.append({
            "name": t.name,
            "description": t.description or "",
            "input_schema": schema,
        })
    return schemas


async def _wait_for_approval(mcp: Client, approval_id: str) -> str:
    """Poll the server until approval_id is decided or we time out. Returns status."""
    deadline = time.time() + APPROVAL_POLL_TIMEOUT
    last_logged = ""
    while time.time() < deadline:
        res = await mcp.call_tool("check_approval", {"approval_id": approval_id})
        data = res.data if hasattr(res, "data") else res
        status = data.get("status", "unknown")
        if status != last_logged:
            console.print(f"  [dim]approval poll: status={status}[/]")
            last_logged = status
        if status in ("approved", "rejected"):
            return status
        await asyncio.sleep(APPROVAL_POLL_INTERVAL)
    return "timeout"


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

            response = anthropic.messages.create(
                model=model,
                max_tokens=2048,
                system=SYSTEM_PROMPT,
                tools=tool_schemas,
                messages=messages,
            )

            # Render reasoning text
            text_blocks = [b.text for b in response.content if b.type == "text"]
            for t in text_blocks:
                render_thinking(t)

            # Build the assistant message exactly as Claude returned it (for the next turn)
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

            # Execute tool calls, render, build tool_result messages
            tool_results = []
            for use in tool_uses:
                render_tool_call(use.name, use.input)
                res = await mcp.call_tool(use.name, use.input)
                render_tool_result(use.name, res)
                data = res.data if hasattr(res, "data") else res

                transcript[-1].setdefault("tool_results", []).append({
                    "tool_use_id": use.id, "name": use.name, "data": data,
                })

                # Approval-gating: detect awaiting_approval and pause here
                if isinstance(data, dict) and data.get("status") == "awaiting_approval":
                    approval_id = data.get("approval_id", "")
                    render_approval_prompt(approval_id)
                    status = await _wait_for_approval(mcp, approval_id)
                    if status == "approved":
                        # Inject a synthetic tool result that nudges the agent to resume
                        # with the approval_id on its next refund call.
                        data = dict(data)
                        data["status"] = "approved"
                        data["instructions"] = (
                            f"The refund approval {approval_id} has been APPROVED by a human "
                            "operator. Call issue_refund again with the SAME order_id and amount, "
                            f"passing approval_id='{approval_id}' to finalize."
                        )
                    elif status == "rejected":
                        data = dict(data)
                        data["status"] = "rejected"
                        data["instructions"] = (
                            "The refund was REJECTED by a human operator. Do not retry. "
                            "Document this in your final summary and stop."
                        )
                    else:
                        data = dict(data)
                        data["status"] = "timeout"
                        data["instructions"] = (
                            "Approval timed out. Stop the investigation and report what you found."
                        )
                    transcript[-1]["tool_results"][-1]["data"] = data

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": use.id,
                    "content": json.dumps(data, default=str),
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
        console.print("[red]ANTHROPIC_API_KEY not set. Either set it in .env, or use `make demo-replay` for offline.[/]")
        return 2

    record = asyncio.run(run_agent(args.model, args.identity, args.goal, args.max_turns))
    path = save_transcript(record)
    console.print(f"\n[dim]transcript saved: {path}[/]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
