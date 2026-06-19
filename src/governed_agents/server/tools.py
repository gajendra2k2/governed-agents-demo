"""The seven governance-checked tools the agent can call, plus `check_approval`
(a non-gated meta-tool used only to poll approval state).

Every governance-checked tool routes through identity.check first, then
dispatches based on the policy declaration for that tool. Identity is sourced
from the HTTP request header `x-agent-identity` — it never appears in any
LLM-visible argument. The model literally cannot pass identity to the server,
can't read its own, can't forge another.
"""
from __future__ import annotations

from typing import Any

from . import approvals, audit, identity, routing, shadow, state


def _audit_denied(actor: str, tool: str, args: dict, e: identity.AccessDenied) -> dict:
    tier = identity.POLICY.tools.get(tool).tier if tool in identity.POLICY.tools else -1
    audit.emit(identity=actor, tool=tool, tier=tier, outcome="denied",
               args=args, detail=str(e))
    return {"ok": False, "denied": True, "reason": str(e)}


# Tier 1 -------------------------------------------------------------------

def list_recent_orders(actor: str, customer_id: str) -> dict[str, Any]:
    args = {"customer_id": customer_id}
    try:
        tp = identity.check(actor, "list_recent_orders")
    except identity.AccessDenied as e:
        return _audit_denied(actor, "list_recent_orders", args, e)
    orders = state.list_recent_orders(customer_id, limit=20)
    audit.emit(identity=actor, tool="list_recent_orders", tier=tp.tier, outcome="ok",
               args=args, result={"count": len(orders)})
    return {"ok": True, "orders": orders}


def get_order_details(actor: str, order_id: str) -> dict[str, Any]:
    args = {"order_id": order_id}
    try:
        tp = identity.check(actor, "get_order_details")
    except identity.AccessDenied as e:
        return _audit_denied(actor, "get_order_details", args, e)
    order = state.get_order(order_id)
    audit.emit(identity=actor, tool="get_order_details", tier=tp.tier,
               outcome="ok" if order else "not_found", args=args, result={"found": bool(order)})
    return {"ok": bool(order), "order": order}


def assess_fraud_risk(actor: str, customer_id: str) -> dict[str, Any]:
    args = {"customer_id": customer_id}
    try:
        tp = identity.check(actor, "assess_fraud_risk")
    except identity.AccessDenied as e:
        return _audit_denied(actor, "assess_fraud_risk", args, e)
    decision, signals, result = routing.route(customer_id)
    audit.emit(
        identity=actor, tool="assess_fraud_risk", tier=tp.tier, outcome="ok",
        args=args,
        result={
            "model": decision.model,
            "tier_label": decision.tier_label,
            "routing_reason": decision.reason,
            "offline": result.offline,
        },
    )
    return {
        "ok": True,
        "model_used": decision.model,
        "risk_label": decision.tier_label,
        "routing_reason": decision.reason,
        "signals": signals,
        "assessment": result.text,
    }


# Tier 2 -------------------------------------------------------------------

def flag_order_for_review(actor: str, order_id: str, reason: str) -> dict[str, Any]:
    args = {"order_id": order_id, "reason": reason}
    try:
        tp = identity.check(actor, "flag_order_for_review")
    except identity.AccessDenied as e:
        return _audit_denied(actor, "flag_order_for_review", args, e)
    changed = state.flag_order(order_id)
    audit.emit(identity=actor, tool="flag_order_for_review", tier=tp.tier,
               outcome="ok" if changed else "not_found", args=args, result={"flagged": changed})
    return {"ok": changed, "flagged": changed}


# Tier 3 (irreversible — agents not authorized) ----------------------------

def freeze_customer_account(actor: str, customer_id: str, reason: str) -> dict[str, Any]:
    """Freezes a customer account. Tier 3 — out of scope for any agent.
    The denial here is the central 'fence' moment when the agent tries it."""
    args = {"customer_id": customer_id, "reason": reason}
    try:
        tp = identity.check(actor, "freeze_customer_account")
    except identity.AccessDenied as e:
        return _audit_denied(actor, "freeze_customer_account", args, e)
    # If a human operator ever calls this with sufficient identity, it would execute.
    # The agent never reaches this path — that's the point.
    audit.emit(identity=actor, tool="freeze_customer_account", tier=tp.tier,
               outcome="executed", args=args, result={"customer_id": customer_id})
    return {"ok": True, "frozen": True, "customer_id": customer_id}


# Tier 4 (shadow by default) -----------------------------------------------

def cancel_order(actor: str, order_id: str, reason: str) -> dict[str, Any]:
    args = {"order_id": order_id, "reason": reason}
    try:
        tp = identity.check(actor, "cancel_order")
    except identity.AccessDenied as e:
        return _audit_denied(actor, "cancel_order", args, e)
    sim = shadow.cancel_order_shadow(order_id, reason)
    audit.emit(identity=actor, tool="cancel_order", tier=tp.tier,
               outcome="shadow", args=args, result=sim,
               detail="executed in shadow mode per policy")
    return {"ok": True, "mode": "shadow", "simulated": sim}


# Tier 5 (approval-gated) --------------------------------------------------

def issue_refund(actor: str, order_id: str, amount: float, approval_id: str | None = None) -> dict[str, Any]:
    args = {"order_id": order_id, "amount": amount, "approval_id": approval_id}
    try:
        tp = identity.check(actor, "issue_refund")
    except identity.AccessDenied as e:
        return _audit_denied(actor, "issue_refund", args, e)
    if approval_id is None:
        new_id = approvals.request(actor, "issue_refund", {"order_id": order_id, "amount": amount})
        audit.emit(identity=actor, tool="issue_refund", tier=tp.tier, outcome="awaiting_approval",
                   args=args, result={"approval_id": new_id})
        return {
            "ok": False,
            "status": "awaiting_approval",
            "approval_id": new_id,
            "instructions": (
                "A human operator must approve this refund. The approval_id has been "
                "registered. Stop here, signal that human input is required, and wait "
                "for the operator to approve before retrying with this approval_id."
            ),
        }
    record = state.get_approval(approval_id)
    if record is None:
        audit.emit(identity=actor, tool="issue_refund", tier=tp.tier, outcome="error",
                   args=args, detail="unknown approval_id")
        return {"ok": False, "error": "unknown approval_id"}
    if record["status"] == "pending":
        return {"ok": False, "status": "still_pending", "approval_id": approval_id}
    if record["status"] != "approved":
        audit.emit(identity=actor, tool="issue_refund", tier=tp.tier, outcome="rejected",
                   args=args, result={"approver": record.get("approver")})
        return {"ok": False, "status": record["status"], "approver": record.get("approver")}
    state.set_status(order_id, "refunded")
    audit.emit(identity=actor, tool="issue_refund", tier=tp.tier, outcome="executed",
               args=args, result={"order_id": order_id, "amount": amount, "approver": record.get("approver")})
    return {"ok": True, "status": "executed", "approver": record.get("approver"), "amount": amount}


def check_approval(actor: str, approval_id: str) -> dict[str, Any]:
    record = state.get_approval(approval_id)
    if record is None:
        return {"ok": False, "error": "unknown approval_id"}
    return {"ok": True, "status": record["status"], "approver": record.get("approver")}
