"""Transcript replayer — re-renders a saved agent run at stage pace.

Use this when:
  - Conference Wi-Fi is unreliable
  - You want a deterministic run with predictable timing
  - You want to show the demo without paying for an API call

Usage:
  python -m governed_agents.client.replay                # latest transcript
  python -m governed_agents.client.replay path/to/file   # specific transcript
  python -m governed_agents.client.replay --speed 2.0    # 2x faster
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text

from ..config import SETTINGS
from .agent import (
    render_approval_decision,
    render_approval_prompt,
    render_header,
    render_thinking,
    render_tool_call,
    render_tool_result,
    render_turn_marker,
)

console = Console()
TRANSCRIPTS_DIR = SETTINGS.state_dir.parent / "transcripts"

# Realistic pacing between elements.
PAUSE_TURN = 0.6
PAUSE_THINK = 1.3
PAUSE_TOOL_BEFORE = 0.6
PAUSE_TOOL_AFTER = 1.0
PAUSE_APPROVAL_WAIT = 3.5    # the "human is thinking" beat


def _default_transcript() -> Path | None:
    """Prefer canonical.json (the shipped talk-day transcript) if present,
    otherwise fall back to the most recent agent-<ts>.json from a live run."""
    if not TRANSCRIPTS_DIR.exists():
        return None
    canonical = TRANSCRIPTS_DIR / "canonical.json"
    if canonical.exists():
        return canonical
    files = sorted(TRANSCRIPTS_DIR.glob("agent-*.json"))
    return files[-1] if files else None


def replay(path: Path, speed: float) -> int:
    record = json.loads(path.read_text())
    render_header(record["model"], record["identity"], record["goal"])
    console.print(f"[dim]replaying {path.name} at {speed}x speed[/]\n")

    for turn in record["turns"]:
        time.sleep(PAUSE_TURN / speed)
        render_turn_marker(turn["turn"])

        if turn.get("text"):
            time.sleep(PAUSE_THINK / speed)
            render_thinking(turn["text"])

        results_by_id = {r["tool_use_id"]: r for r in turn.get("tool_results", [])}
        for use in turn.get("tool_uses", []):
            time.sleep(PAUSE_TOOL_BEFORE / speed)
            render_tool_call(use["name"], use["input"])
            r = results_by_id.get(use["id"])
            if r is None:
                continue
            time.sleep(PAUSE_TOOL_AFTER / speed)
            # Render the initial server response (e.g., awaiting_approval).
            render_tool_result(use["name"], r["data"])

            # If the live run included an approval handshake, replay the
            # full theatrical arc: prompt → wait → decision.
            ap = r.get("approval")
            if ap is not None:
                render_approval_prompt(ap["approval_id"])
                time.sleep(PAUSE_APPROVAL_WAIT / speed)
                render_approval_decision(ap["approval_id"], ap["status"], ap.get("approver"))

    console.print()
    console.print(Rule("[bold green]Investigation complete (replay)[/]"))
    if record.get("final_summary"):
        console.print(Panel(Text(record["final_summary"], style="white"),
                            title="agent summary", border_style="green", title_align="left"))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Replay a saved agent transcript.")
    ap.add_argument("path", nargs="?", help="Transcript file (default: latest)")
    ap.add_argument("--speed", type=float, default=1.0, help="Playback speed multiplier")
    args = ap.parse_args()

    if args.path:
        p = Path(args.path)
    else:
        p = _default_transcript()
        if p is None:
            console.print(f"[red]no transcripts found in {TRANSCRIPTS_DIR}[/]")
            console.print("[dim]run `make agent` once first to capture one[/]")
            return 1
        console.print(f"[dim]using {p.name}[/]")

    if not p.exists():
        console.print(f"[red]transcript not found: {p}[/]")
        return 1
    return replay(p, args.speed)


if __name__ == "__main__":
    sys.exit(main())
