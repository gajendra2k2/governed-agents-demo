"""Human-in-the-loop approval coordination.

Flow:
  1. Agent calls a tier-5 tool (e.g. issue_refund) without an approval_id.
     The server creates a pending approval record, returns awaiting_approval.
  2. Operator runs `make approve ID=<approval_id>` in another terminal.
  3. The agent's client loop polls `check_approval(approval_id)` and, once
     approved, calls the same tool again with the approval_id to finalize.

A `pending_approval` event also lands on the `approvals` Kafka topic so the
audit story stays streaming-native.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from confluent_kafka import Producer

from ..config import SETTINGS
from ..topics import APPROVALS
from . import state

_producer = Producer({"bootstrap.servers": SETTINGS.kafka_bootstrap, "client.id": "approvals-writer"})


def request(identity: str, tool: str, args: dict[str, Any]) -> str:
    approval_id = f"A-{uuid.uuid4().hex[:8].upper()}"
    state.create_approval(approval_id, identity, tool, args)
    event = {
        "kind": "pending_approval",
        "approval_id": approval_id,
        "identity": identity,
        "tool": tool,
        "args": args,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    _producer.produce(APPROVALS, key=approval_id.encode(), value=json.dumps(event).encode())
    _producer.poll(0)
    return approval_id


def decide(approval_id: str, status: str, approver: str) -> bool:
    decided_at = datetime.now(timezone.utc).isoformat()
    ok = state.decide_approval(approval_id, status, approver, decided_at)
    if ok:
        event = {
            "kind": "approval_decision",
            "approval_id": approval_id,
            "status": status,
            "approver": approver,
            "ts": decided_at,
        }
        _producer.produce(APPROVALS, key=approval_id.encode(), value=json.dumps(event).encode())
        _producer.flush(2)
    return ok
