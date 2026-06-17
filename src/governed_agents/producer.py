"""Synthetic e-commerce order stream producer.

Run modes:
  python -m governed_agents.producer                    # steady normal stream
  python -m governed_agents.producer --scenario fraud   # seed C010 burst, then steady stream

The `fraud` scenario front-loads 6 high-value orders for customer C010 across
mismatched geos in the last hour — the deterministic pattern the agent will
discover when it investigates. After the burst it returns to a normal stream
so the rest of the ledger still feels alive.
"""
from __future__ import annotations

import argparse
import json
import random
import signal
import time
import uuid
from datetime import datetime, timedelta, timezone

from confluent_kafka import Producer

from .config import SETTINGS
from .topics import ORDERS

CUSTOMERS = [f"C{i:03d}" for i in range(1, 21)]
ITEMS = [
    ("SKU-001", "Wireless Mouse", 29.99),
    ("SKU-002", "Mechanical Keyboard", 119.00),
    ("SKU-003", "27\" Monitor", 349.00),
    ("SKU-004", "USB-C Hub", 49.50),
    ("SKU-005", "Noise-Canceling Headphones", 279.00),
    ("SKU-006", "Webcam 1080p", 89.00),
    ("SKU-007", "Desk Lamp", 39.00),
    ("SKU-008", "Standing Desk Mat", 59.99),
]
COUNTRIES = ["US", "CA", "GB", "DE", "IN", "JP"]


def _build_order(customer: str | None = None, country: str | None = None,
                  item_idx: int | None = None, ts: datetime | None = None,
                  status: str = "placed") -> dict:
    cust = customer or random.choice(CUSTOMERS)
    item = ITEMS[item_idx] if item_idx is not None else random.choice(ITEMS)
    qty = random.randint(1, 3)
    return {
        "order_id": f"O-{uuid.uuid4().hex[:8].upper()}",
        "customer_id": cust,
        "ts": (ts or datetime.now(timezone.utc)).isoformat(),
        "sku": item[0],
        "name": item[1],
        "qty": qty,
        "amount": round(item[2] * qty, 2),
        "country": country or random.choice(COUNTRIES),
        "status": status,
    }


def _seed_fraud_scenario(producer: Producer) -> list[dict]:
    """Front-load 6 high-value orders for C010 across two countries in the last hour.

    Returns the orders so the caller can log them. The pattern: same SKU
    (high-value monitor + headphones), 4 from US, 2 from GB, 1 already
    'shipped' (the one the agent will need to refund rather than cancel).
    """
    random.seed(42)  # deterministic across runs
    now = datetime.now(timezone.utc)
    orders = []
    layout = [
        # (minutes_ago, country, item_idx, status)
        (55, "US", 2, "placed"),     # $349 monitor
        (47, "US", 4, "placed"),     # $279 headphones
        (40, "GB", 2, "placed"),     # $349 monitor (geo shift)
        (33, "US", 4, "shipped"),    # $279 headphones — already shipped → refund path
        (20, "GB", 2, "placed"),     # $349 monitor again
        (10, "US", 4, "placed"),     # $279 headphones
    ]
    for mins_ago, country, item_idx, status in layout:
        o = _build_order(customer="C010", country=country, item_idx=item_idx,
                          ts=now - timedelta(minutes=mins_ago), status=status)
        producer.produce(ORDERS, key=b"C010", value=json.dumps(o).encode())
        orders.append(o)
    producer.flush(5)
    return orders


def _stream_normal(producer: Producer, running: dict) -> None:
    n = 0
    while running["v"]:
        order = _build_order()
        producer.produce(ORDERS, key=order["customer_id"].encode(), value=json.dumps(order).encode())
        producer.poll(0)
        n += 1
        if n % 10 == 0:
            print(f"[producer] {n} steady-state orders sent — latest {order['order_id']} ${order['amount']}", flush=True)
        time.sleep(random.uniform(0.4, 1.2))
    producer.flush(5)
    print(f"[producer] stopped after {n} steady-state orders", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="Synthetic order stream producer.")
    ap.add_argument("--scenario", choices=["none", "fraud"], default="none",
                    help="Seed a deterministic scenario before the steady stream.")
    args = ap.parse_args()

    producer = Producer({"bootstrap.servers": SETTINGS.kafka_bootstrap, "client.id": "orders-producer"})
    running = {"v": True}

    def _stop(*_a):
        running["v"] = False
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    if args.scenario == "fraud":
        seeded = _seed_fraud_scenario(producer)
        print(f"[producer] seeded fraud scenario: {len(seeded)} orders for C010 "
              f"across {len({o['country'] for o in seeded})} countries", flush=True)
        for o in seeded:
            print(f"[producer]   {o['order_id']}  {o['country']}  ${o['amount']:>7.2f}  {o['status']}", flush=True)

    print(f"[producer] now streaming to topic '{ORDERS}' at {SETTINGS.kafka_bootstrap} (Ctrl+C to stop)", flush=True)
    _stream_normal(producer, running)


if __name__ == "__main__":
    main()
