"""Transcript replayer — replays a saved agent run as if it were live.

Use this when:
  - Conference Wi-Fi is unreliable
  - You want a deterministic run for stage timing
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

from ..config import SETTINGS
from .agent import (
    render_approval_prompt,
    render_header,
    render_thinking,
    render_tool_call,
    render_tool_result,
    render_turn_marker,
)
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text

console = Console()
TRANSCRIPTS_DIR = SETTINGS.state_dir.parent / "transcripts"

# Realistic pacing between elements
PAUSE_TURN = 0.5
PAUSE_THINK = 1.2
PAUSE_TOOL = 0.8
PAUSE_APPROVAL = 3.0


def _latest_transcript() -> Path | None:
    if not TRANSCRIPTS_DIR.exists():
        return None
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
        for use in turn.get("tool_uses", []):
            time.sleep(PAUSE_TOOL / speed)
            render_tool_call(use["name"], use["input"])
            # find the matching result
            results = turn.get("tool_results", [])
            r = next((r for r in results if r["tool_use_id"] == use["id"]), None)
            if r is not None:
                time.sleep(PAUSE_TOOL / speed)
                render_tool_result(use["name"], r["data"])
                # surface approval prompt at the right moment for narration
                if isinstance(r["data"], dict) and r["data"].get("status") in ("approved", "rejected", "awaiting_approval"):
                    if r["data"].get("status") == "approved":
                        # Simulate the wait: show the approval prompt, pause, then show approval
                        time.sleep(PAUSE_APPROVAL / speed)

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
        p = _latest_transcript()
        if p is None:
            console.print(f"[red]no transcripts found in {TRANSCRIPTS_DIR}[/]")
            return 1
        console.print(f"[dim]using latest: {p}[/]")

    if not p.exists():
        console.print(f"[red]transcript not found: {p}[/]")
        return 1
    return replay(p, args.speed)


if __name__ == "__main__":
    sys.exit(main())
