"""Utilities for building hashed identifiers for Polymarket trades."""
from __future__ import annotations

import hashlib
from typing import Any, Dict, Optional


def annotate_tx_hash(trade: Dict[str, Any]) -> str:
    """Return the normalized tx hash and ensure trade['txHash'] is populated."""
    tx_hash = trade.get('transactionHash') or trade.get('txHash') or trade.get('hash') or ''
    if tx_hash and not trade.get('txHash'):
        trade['txHash'] = tx_hash
    return tx_hash


def _coerce_timestamp(value: Any) -> str:
    if value is None:
        return ''
    if isinstance(value, (int, float)):
        return str(value)
    return str(value)


def build_fill_key(trade: Dict[str, Any], wallet: Optional[str] = None) -> Optional[str]:
    """Build a SHA-256 hash that uniquely identifies a trade fill."""
    tx_hash = annotate_tx_hash(trade)
    if not tx_hash:
        return None

    timestamp = trade.get('timestamp') or trade.get('created_at') or trade.get('blockTime')
    asset = trade.get('asset') or trade.get('conditionId') or trade.get('condition_id') or ''
    normalized_wallet = wallet or trade.get('proxyWallet') or trade.get('maker') or trade.get('taker') or ''

    payload = f"{tx_hash}_{_coerce_timestamp(timestamp)}_{normalized_wallet.lower()}_{(asset or '')[:20]}"
    return hashlib.sha256(payload.encode()).hexdigest()
