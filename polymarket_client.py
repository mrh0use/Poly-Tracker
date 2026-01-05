import aiohttp
import asyncio
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any


class PolymarketClient:
    GAMMA_BASE_URL = "https://gamma-api.polymarket.com"
    CLOB_BASE_URL = "https://clob.polymarket.com"
    
    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self._known_wallets: set = set()
    
    async def ensure_session(self):
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
    
    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()
    
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
    
    async def get_market_by_id(self, market_id: str) -> Optional[Dict[str, Any]]:
        await self.ensure_session()
        try:
            async with self.session.get(f"{self.GAMMA_BASE_URL}/markets/{market_id}") as resp:
                if resp.status == 200:
                    return await resp.json()
                return None
        except Exception as e:
            print(f"Error fetching market {market_id}: {e}")
            return None
    
    async def get_recent_trades(self, limit: int = 100) -> List[Dict[str, Any]]:
        await self.ensure_session()
        try:
            async with self.session.get(f"{self.CLOB_BASE_URL}/trades", params={"limit": limit}) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data if isinstance(data, list) else data.get('data', [])
                return []
        except Exception as e:
            print(f"Error fetching trades: {e}")
            return []
    
    async def get_trades_for_market(self, token_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        await self.ensure_session()
        params = {
            "token_id": token_id,
            "limit": limit
        }
        try:
            async with self.session.get(f"{self.CLOB_BASE_URL}/trades", params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data if isinstance(data, list) else data.get('data', [])
                return []
        except Exception as e:
            print(f"Error fetching trades for market {token_id}: {e}")
            return []
    
    async def get_order_book(self, token_id: str) -> Optional[Dict[str, Any]]:
        await self.ensure_session()
        try:
            async with self.session.get(f"{self.CLOB_BASE_URL}/book", params={"token_id": token_id}) as resp:
                if resp.status == 200:
                    return await resp.json()
                return None
        except Exception as e:
            print(f"Error fetching order book: {e}")
            return None
    
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
    
    def is_fresh_wallet(self, wallet_address: str) -> bool:
        return wallet_address.lower() not in self._known_wallets
    
    def mark_wallet_seen(self, wallet_address: str):
        self._known_wallets.add(wallet_address.lower())
    
    def get_wallet_from_trade(self, trade: Dict[str, Any]) -> Optional[str]:
        return trade.get('maker') or trade.get('taker') or trade.get('owner')


polymarket_client = PolymarketClient()
