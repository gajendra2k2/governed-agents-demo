"""FastMCP server — the governance surface.

Identity arrives on the inbound HTTP request as `x-agent-identity`. The model
never sees it, can't pass it, can't change it. The 7 MCP tool signatures below
contain ONLY the business arguments — that is the trust boundary in code.

Also runs a background thread that consumes the `orders` Kafka stream into the
local order ledger so the agent's read tools see fresh data.
"""
from __future__ import annotations

import json
import threading

from confluent_kafka import Consumer
from fastmcp import FastMCP

from ..config import SETTINGS
from ..topics import ORDERS
from . import identity, state, tools

mcp = FastMCP("governed-agents")


def _actor() -> str:
    """Pull identity from the HTTP request header, or raise."""
    try:
        return identity.from_http()
    except identity.MissingIdentity as e:
        # Surface as an unambiguous tool error to the agent (and audit it via the tool path).
        raise RuntimeError(str(e)) from e


@mcp.tool
def list_recent_orders(customer_id: str) -> dict:
    """List recent orders for a customer (most recent first, up to 20).
    Use this to investigate customer activity patterns."""
    return tools.list_recent_orders(_actor(), customer_id)


@mcp.tool
def get_order_details(order_id: str) -> dict:
    """Fetch the full record for a single order by its order_id."""
    return tools.get_order_details(_actor(), order_id)


@mcp.tool
def assess_fraud_risk(customer_id: str) -> dict:
    """Run a fraud-risk assessment on a customer using their recent activity.
    Internally routes Haiku/Sonnet/Opus by signal strength. Returns
    risk_label, assessment text, model_used, and routing_reason."""
    return tools.assess_fraud_risk(_actor(), customer_id)


@mcp.tool
def flag_order_for_review(order_id: str, reason: str) -> dict:
    """Mark an order for human review. Reason is recorded in the audit log."""
    return tools.flag_order_for_review(_actor(), order_id, reason)


@mcp.tool
def freeze_customer_account(customer_id: str, reason: str) -> dict:
    """Freeze a customer account — irreversible. Restricted to human operators
    only. Agents will receive an access-denied response."""
    return tools.freeze_customer_account(_actor(), customer_id, reason)


@mcp.tool
def cancel_order(order_id: str, reason: str) -> dict:
    """Cancel an order. Runs in shadow mode by default (simulated, not committed).
    The response indicates shadow mode in `mode`."""
    return tools.cancel_order(_actor(), order_id, reason)


@mcp.tool
def issue_refund(order_id: str, amount: float, approval_id: str | None = None) -> dict:
    """Issue a refund. Two-step: first call without approval_id returns
    `awaiting_approval` with an approval_id. A human operator must approve it
    out of band. Then call again with the approval_id to execute."""
    return tools.issue_refund(_actor(), order_id, amount, approval_id)


@mcp.tool
def check_approval(approval_id: str) -> dict:
    """Check the current status of a pending approval (pending/approved/rejected)."""
    return tools.check_approval(_actor(), approval_id)


def _consume_orders() -> None:
    consumer = Consumer({
        "bootstrap.servers": SETTINGS.kafka_bootstrap,
        "group.id": "orders-ingest-server",
        "auto.offset.reset": "earliest",
        "enable.auto.commit": True,
    })
    consumer.subscribe([ORDERS])
    print(f"[server] consuming '{ORDERS}' → order ledger")
    try:
        while True:
            msg = consumer.poll(0.5)
            if msg is None or msg.error():
                continue
            try:
                order = json.loads(msg.value())
                state.upsert_order(order)
            except Exception as e:
                print(f"[server] bad order event: {e}")
    finally:
        consumer.close()


def main() -> None:
    state.init_db()
    t = threading.Thread(target=_consume_orders, daemon=True)
    t.start()
    print(f"[server] FastMCP on http://{SETTINGS.mcp_host}:{SETTINGS.mcp_port}/mcp")
    print(f"[server] offline_mode={SETTINGS.offline_mode}  kafka={SETTINGS.kafka_bootstrap}")
    print("[server] identity comes from HTTP header 'x-agent-identity'")
    mcp.run(transport="streamable-http", host=SETTINGS.mcp_host, port=SETTINGS.mcp_port)


if __name__ == "__main__":
    main()
