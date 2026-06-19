"""Terminal audit viewer — tail the Kafka `audit` topic with color.

Run in its own terminal during the demo: `make audit`. Phase 3 of the demo
flips to this window to show the audience that every tool call the agent made
— including the denied one — is a structured, queryable event on a topic that
any consumer can subscribe to.
"""
from __future__ import annotations

import json
import signal
import sys
import uuid

from confluent_kafka import Consumer
from rich.console import Console
from rich.table import Table

from governed_agents.config import SETTINGS
from governed_agents.topics import AUDIT

console = Console()

OUTCOME_STYLE = {
    "ok": "green",
    "executed": "bold green",
    "shadow": "yellow",
    "awaiting_approval": "magenta",
    "denied": "bold red",
    "rejected": "red",
    "error": "red",
    "not_found": "dim",
}


def render(event: dict) -> None:
    outcome = event.get("outcome", "?")
    style = OUTCOME_STYLE.get(outcome, "white")
    table = Table.grid(padding=(0, 1))
    table.add_column(style="dim", justify="right")
    table.add_column()
    table.add_row("ts", event.get("ts", ""))
    table.add_row("identity", f"[yellow]{event.get('identity')}[/]")
    table.add_row("tool", f"[cyan]{event.get('tool')}[/]")
    table.add_row("tier", str(event.get("tier")))
    table.add_row("outcome", f"[{style}]{outcome}[/]")
    table.add_row("args", json.dumps(event.get("args", {})))
    if event.get("result"):
        table.add_row("result", json.dumps(event.get("result"))[:200])
    if event.get("detail"):
        table.add_row("detail", str(event.get("detail")))
    console.print(table)
    console.print("[dim]" + "─" * 60 + "[/]")


def main() -> int:
    # Unique group per startup so topic deletes/recreates between demo runs
    # never leave the viewer stuck on a stale offset.
    consumer = Consumer({
        "bootstrap.servers": SETTINGS.kafka_bootstrap,
        "group.id": f"audit-viewer-{uuid.uuid4().hex[:8]}",
        "auto.offset.reset": "earliest",
        "enable.auto.commit": False,
    })
    consumer.subscribe([AUDIT])
    console.print(f"[bold]audit viewer[/] · topic=[cyan]{AUDIT}[/] · bootstrap={SETTINGS.kafka_bootstrap}")
    console.print("[dim]Ctrl+C to stop[/]\n")

    running = {"v": True}
    signal.signal(signal.SIGINT, lambda *_: running.update(v=False))
    try:
        while running["v"]:
            msg = consumer.poll(0.5)
            if msg is None or msg.error():
                continue
            try:
                event = json.loads(msg.value())
                render(event)
            except Exception as e:
                console.print(f"[red]bad event: {e}[/]")
    finally:
        consumer.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
