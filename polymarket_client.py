import aiohttp
import asyncio
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any


class PolymarketClient:
    DATA_API_BASE_URL = "https://data-api.polymarket.com"
    GAMMA_BASE_URL = "https://gamma-api.polymarket.com"
    
    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self._known_wallets: set = set()
    
    async def ensure_session(self):
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
    
    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()
    
    async def get_recent_trades(self, limit: int = 100) -> List[Dict[str, Any]]:
        await self.ensure_session()
        try:
            async with self.session.get(
                f"{self.DATA_API_BASE_URL}/trades",
                params={"limit": limit}
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data if isinstance(data, list) else []
                print(f"Trades API returned status {resp.status}")
                return []
        except Exception as e:
            print(f"Error fetching trades: {e}")
            return []
    
    async def get_wallet_trades(self, wallet_address: str, limit: int = 20) -> List[Dict[str, Any]]:
        await self.ensure_session()
        try:
            async with self.session.get(
                f"{self.DATA_API_BASE_URL}/trades",
                params={"user": wallet_address, "limit": limit}
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data if isinstance(data, list) else []
                return []
        except Exception as e:
            print(f"Error fetching wallet trades for {wallet_address}: {e}")
            return []
    
    async def get_markets(self, limit: int = 100, active: bool = True) -> List[Dict[str, Any]]:
        await self.ensure_session()
        params = {
            "limit": limit,
            "active": str(active).lower(),
            "closed": "false"
        }
        try:
            async with self.session.get(f"{self.GAMMA_BASE_URL}/markets", params=params) as resp:
                if resp.status == 200:
                    return await resp.json()
                return []
        except Exception as e:
            print(f"Error fetching markets: {e}")
            return []
    
    async def get_events(self, limit: int = 50, closed: bool = False) -> List[Dict[str, Any]]:
        await self.ensure_session()
        params = {
            "limit": limit,
            "closed": str(closed).lower(),
            "order": "id",
            "ascending": "false"
        }
        try:
            async with self.session.get(f"{self.GAMMA_BASE_URL}/events", params=params) as resp:
                if resp.status == 200:
                    return await resp.json()
                return []
        except Exception as e:
            print(f"Error fetching events: {e}")
            return []
    
    def calculate_trade_value(self, trade: Dict[str, Any]) -> float:
        try:
            size = float(trade.get('size', 0))
            price = float(trade.get('price', 0))
            return size * price
        except (ValueError, TypeError):
            return 0.0
    
    def get_wallet_from_trade(self, trade: Dict[str, Any]) -> Optional[str]:
        return trade.get('proxyWallet') or trade.get('maker') or trade.get('taker')
    
    def get_market_title(self, trade: Dict[str, Any]) -> str:
        return trade.get('title', 'Unknown Market')
    
    def get_unique_trade_id(self, trade: Dict[str, Any]) -> str:
        tx_hash = trade.get('transactionHash', '')
        timestamp = str(trade.get('timestamp', ''))
        wallet = trade.get('proxyWallet', '')
        asset = trade.get('asset', '')[:20]
        return f"{tx_hash}_{timestamp}_{wallet}_{asset}"


polymarket_client = PolymarketClient()
