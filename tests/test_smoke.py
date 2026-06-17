"""Tests that don't need Kafka — verifies policy + identity logic.

Run with `pytest -q tests/` after `pip install -e '.[dev]'`.
"""
from __future__ import annotations

import pytest

from governed_agents.server import identity


def test_viewer_can_read():
    tp = identity.check("viewer", "list_recent_orders")
    assert tp.tier == 1


def test_viewer_cannot_write():
    with pytest.raises(identity.AccessDenied):
        identity.check("viewer", "flag_order_for_review")


def test_fraud_investigator_can_read_and_flag():
    for tool in ("list_recent_orders", "get_order_details", "assess_fraud_risk", "flag_order_for_review"):
        tp = identity.check("fraud_investigator", tool)
        assert tp.tier in (1, 2)


def test_fraud_investigator_cannot_freeze():
    with pytest.raises(identity.AccessDenied) as exc:
        identity.check("fraud_investigator", "freeze_customer_account")
    assert "tier-3" in str(exc.value)


def test_fraud_investigator_cannot_cancel_order():
    with pytest.raises(identity.AccessDenied) as exc:
        identity.check("fraud_investigator", "cancel_order")
    assert "tier-4" in str(exc.value)


def test_fraud_investigator_can_refund_with_approval():
    tp = identity.check("fraud_investigator", "issue_refund")
    assert tp.tier == 5
    assert tp.approval_required is True


def test_ops_human_has_full_authority():
    for tool in identity.POLICY.tools:
        tp = identity.check("ops_human", tool)
        assert tp.tier >= 1


def test_unknown_identity_denied():
    with pytest.raises(identity.AccessDenied):
        identity.check("ghost", "list_recent_orders")


def test_cancel_order_marked_shadow_by_default():
    tp = identity.check("ops_human", "cancel_order")
    assert tp.shadow_by_default is True


def test_freeze_marked_tier_3():
    tp = identity.check("ops_human", "freeze_customer_account")
    assert tp.tier == 3
