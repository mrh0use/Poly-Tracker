#!/usr/bin/env python3
"""Compare blockchain timestamps vs. bot ingestion time for recent trades.

This script reads the latest rows from the ``seen_transactions`` table, fetches
block timestamps via Polygon's JSON-RPC, and prints the delay (in minutes)
between block confirmation and when the bot recorded the trade.
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional

import psycopg2

DEFAULT_RPC_URL = "https://polygon-rpc.com"

DEFAULT_TABLE_LIMIT = 10
JSONRPC_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": "PolyTracker-DelayProbe/1.0",
}


@dataclass
class SeenTrade:
    tx_hash: str
    seen_at: datetime
    chain_time: Optional[datetime]

    @property
    def delay_minutes(self) -> Optional[float]:
        if self.chain_time is None:
            return None
        delta = self.seen_at - self.chain_time
        return delta.total_seconds() / 60


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_TABLE_LIMIT,
        help=f"Number of rows to inspect (default: {DEFAULT_TABLE_LIMIT}).",
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL"),
        help="PostgreSQL connection string. Defaults to $DATABASE_URL.",
    )
    parser.add_argument(
        "--rpc-url",
        default=os.environ.get("POLYGON_RPC_URL", DEFAULT_RPC_URL),
        help=f"Polygon JSON-RPC endpoint (default: {DEFAULT_RPC_URL}).",
    )
    parser.add_argument(
        "--format",
        choices={"table", "json"},
        default="table",
        help="Output format (table or json).",
    )
    parser.add_argument(
        "--sort",
        choices={"seen_at", "chain_time", "delay"},
        default="seen_at",
        help="Field used to sort the output (default: seen_at).",
    )
    parser.add_argument(
        "--sort-direction",
        choices={"asc", "desc"},
        default="desc",
        help="Sort direction for the selected field (default: desc).",
    )
    parser.add_argument(
        "--rpc-sleep",
        type=float,
        default=0.75,
        help="Seconds to sleep between RPC calls to avoid rate limits (default: 0.2).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="HTTP timeout for RPC calls in seconds (default: 10).",
    )
    return parser.parse_args()


def rpc_call(
    rpc_url: str,
    method: str,
    params: List,
    timeout: float,
    ctx: ssl.SSLContext,
    sleep_interval: float,
) -> Dict:
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(rpc_url, data=payload, headers=JSONRPC_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        data = json.loads(resp.read())
    if sleep_interval > 0:
        time.sleep(sleep_interval)
    return data


def fetch_chain_time(
    tx_hash: str,
    rpc_url: str,
    timeout: float,
    ctx: ssl.SSLContext,
    block_cache: Dict[str, Optional[datetime]],
    sleep_interval: float,
) -> Optional[datetime]:
    try:
        receipt = rpc_call(rpc_url, "eth_getTransactionReceipt", [tx_hash], timeout, ctx, sleep_interval)
    except urllib.error.URLError as exc:
        raise RuntimeError(f"RPC error fetching receipt for {tx_hash}: {exc}") from exc

    if receipt.get("error"):
        raise RuntimeError(f"RPC error for {tx_hash}: {receipt['error']}")

    result = receipt.get("result")
    block_number = result.get("blockNumber") if result else None
    if not block_number:
        return None

    if block_number not in block_cache:
        block_resp = rpc_call(
            rpc_url,
            "eth_getBlockByNumber",
            [block_number, False],
            timeout,
            ctx,
            sleep_interval,
        )
        if block_resp.get("error"):
            raise RuntimeError(f"Block fetch error for {block_number}: {block_resp['error']}")
        block = block_resp.get("result") or {}
        timestamp_hex = block.get("timestamp")
        block_cache[block_number] = (
            datetime.fromtimestamp(int(timestamp_hex, 16), tz=timezone.utc)
            if timestamp_hex
            else None
        )

    return block_cache[block_number]


def fetch_seen_transactions(conn, limit: int) -> List[Dict[str, datetime]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT tx_hash, seen_at FROM seen_transactions ORDER BY seen_at DESC LIMIT %s",
            (limit,),
        )
        return cur.fetchall()


def ensure_timezone(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def format_table(trades: List[SeenTrade]) -> str:
    headers = ["tx_hash", "chain_time", "seen_at", "delay_min"]
    rows = []
    for t in trades:
        rows.append(
            [
                t.tx_hash,
                t.chain_time.strftime("%Y-%m-%d %H:%M:%S") if t.chain_time else "N/A",
                t.seen_at.strftime("%Y-%m-%d %H:%M:%S %Z"),
                f"{t.delay_minutes:.2f}" if t.delay_minutes is not None else "N/A",
            ]
        )

    widths = [max(len(h), *(len(row[idx]) for row in rows)) for idx, h in enumerate(headers)]
    lines = [
        " ".join(h.ljust(widths[idx]) for idx, h in enumerate(headers)),
        "-" * (sum(widths) + len(widths) - 1),
    ]
    for row in rows:
        lines.append(" ".join(cell.ljust(widths[idx]) for idx, cell in enumerate(row)))
    return "\n".join(lines)


def sort_trades(trades: List[SeenTrade], key_name: str, descending: bool) -> List[SeenTrade]:
    reverse = descending

    def key_fn(trade: SeenTrade):
        if key_name == "chain_time":
            return trade.chain_time or datetime.min.replace(tzinfo=timezone.utc)
        if key_name == "delay":
            return trade.delay_minutes if trade.delay_minutes is not None else float("-inf")
        return trade.seen_at

    sorted_trades = sorted(trades, key=key_fn, reverse=reverse)
    return sorted_trades


def main() -> int:
    args = parse_args()
    if not args.database_url:
        print("--database-url not provided and $DATABASE_URL is unset", file=sys.stderr)
        return 1

    conn = psycopg2.connect(args.database_url)
    rows = fetch_seen_transactions(conn, args.limit)
    conn.close()

    ctx = ssl.create_default_context()
    block_cache: Dict[str, Optional[datetime]] = {}
    trades: List[SeenTrade] = []

    for tx_hash, seen_at in rows:
        seen_at = ensure_timezone(seen_at)
        try:
            chain_time = fetch_chain_time(
                tx_hash,
                args.rpc_url,
                args.timeout,
                ctx,
                block_cache,
                args.rpc_sleep,
            )
        except RuntimeError as exc:
            print(f"[WARN] {exc}", file=sys.stderr)
            chain_time = None
        trades.append(SeenTrade(tx_hash=tx_hash, seen_at=seen_at, chain_time=chain_time))

    trades = sort_trades(trades, key_name=args.sort, descending=(args.sort_direction == "desc"))

    if args.format == "json":
        payload = [
            {
                "tx_hash": t.tx_hash,
                "chain_time": t.chain_time.isoformat() if t.chain_time else None,
                "seen_at": t.seen_at.isoformat(),
                "delay_minutes": t.delay_minutes,
            }
            for t in trades
        ]
        print(json.dumps(payload, indent=2))
    else:
        print(format_table(trades))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
