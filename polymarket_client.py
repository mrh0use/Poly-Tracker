import aiohttp
import asyncio
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any


class PolymarketClient:
    DATA_API_BASE_URL = "https://data-api.polymarket.com"
    GAMMA_BASE_URL = "https://gamma-api.polymarket.com"
    
    SPORTS_SLUGS = {'sports', 'nba', 'nfl', 'mlb', 'nhl', 'soccer', 'football', 'basketball', 
                   'baseball', 'hockey', 'tennis', 'golf', 'ufc', 'mma', 'boxing', 'f1', 
                   'formula-1', 'cricket', 'esports', 'league-of-legends', 'dota', 'csgo',
                   'valorant', 'nba-games', 'nfl-games', 'epl', 'premier-league', 'champions-league'}
    
    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self._known_wallets: set = set()
        self._sports_tag_ids: set = set()
        self._market_cache: Dict[str, Dict[str, Any]] = {}
        self._cache_last_updated: Optional[datetime] = None
        self._wallet_stats_cache: Dict[str, Dict[str, Any]] = {}
        self._wallet_stats_updated: Dict[str, datetime] = {}
    
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
    
    async def fetch_sports_tags(self) -> set:
        await self.ensure_session()
        try:
            async with self.session.get(f"{self.GAMMA_BASE_URL}/sports") as resp:
                if resp.status == 200:
                    sports_data = await resp.json()
                    tag_ids = set()
                    for sport in sports_data:
                        tags_str = sport.get('tags', '')
                        if tags_str:
                            for tag_id in tags_str.split(','):
                                tag_ids.add(tag_id.strip())
                    self._sports_tag_ids = tag_ids
                    return tag_ids
        except Exception as e:
            print(f"Error fetching sports tags: {e}")
        return set()
    
    async def refresh_market_cache(self, force: bool = False) -> None:
        now = datetime.utcnow()
        if not force and self._cache_last_updated:
            age = (now - self._cache_last_updated).total_seconds()
            if age < 300:
                return
        
        await self.ensure_session()
        try:
            async with self.session.get(
                f"{self.GAMMA_BASE_URL}/markets",
                params={"limit": 1000, "active": "true", "closed": "false"}
            ) as resp:
                if resp.status == 200:
                    markets = await resp.json()
                    for market in markets:
                        condition_id = market.get('conditionId', market.get('condition_id', ''))
                        if condition_id:
                            self._market_cache[condition_id] = {
                                'slug': market.get('slug', ''),
                                'title': market.get('question', market.get('title', '')),
                                'tags': market.get('tags', []),
                                'groupSlug': market.get('groupSlug', ''),
                            }
                        tokens = market.get('tokens', [])
                        for token in tokens:
                            token_id = token.get('token_id', '')
                            if token_id:
                                self._market_cache[token_id] = {
                                    'slug': market.get('slug', ''),
                                    'title': market.get('question', market.get('title', '')),
                                    'tags': market.get('tags', []),
                                    'groupSlug': market.get('groupSlug', ''),
                                }
                    self._cache_last_updated = now
                    print(f"Market cache refreshed: {len(self._market_cache)} entries")
        except Exception as e:
            print(f"Error refreshing market cache: {e}")
    
    def get_market_info(self, trade: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        asset = trade.get('asset', '')
        if asset and asset in self._market_cache:
            return self._market_cache[asset]
        
        condition_id = trade.get('conditionId', trade.get('condition_id', ''))
        if condition_id and condition_id in self._market_cache:
            return self._market_cache[condition_id]
        
        return None
    
    def is_sports_market(self, trade_or_event: Dict[str, Any]) -> bool:
        market_info = self.get_market_info(trade_or_event)
        if market_info:
            group_slug = market_info.get('groupSlug', '').lower()
            if group_slug in self.SPORTS_SLUGS:
                return True
            
            tags = market_info.get('tags', [])
            if isinstance(tags, list):
                for tag in tags:
                    if isinstance(tag, dict):
                        slug = tag.get('slug', '').lower()
                        tag_id = str(tag.get('id', ''))
                        if slug in self.SPORTS_SLUGS or tag_id in self._sports_tag_ids:
                            return True
                    elif isinstance(tag, str):
                        if tag.lower() in self.SPORTS_SLUGS or tag in self._sports_tag_ids:
                            return True
            
            slug = market_info.get('slug', '').lower()
            title = market_info.get('title', '').lower()
            
            sports_terms = ['nba', 'nfl', 'mlb', 'nhl', 'ufc', 'boxing', 'soccer', 
                           'basketball', 'baseball', 'hockey', 'tennis', 'golf', 
                           'f1', 'epl', 'premier-league', 'super-bowl', 'world-series', 
                           'stanley-cup', 'esports', 'league-of-legends', 'dota', 'csgo',
                           'valorant', 'champions-league', 'mma', 'cricket', 'fifa',
                           'world-cup', 'olympics', 'ncaa', 'college-football', 'college-basketball']
            
            for term in sports_terms:
                if term in slug or term in title:
                    return True
        
        tags = trade_or_event.get('tags', [])
        if isinstance(tags, list):
            for tag in tags:
                if isinstance(tag, dict):
                    slug = tag.get('slug', '').lower()
                    tag_id = str(tag.get('id', ''))
                    if slug in self.SPORTS_SLUGS or tag_id in self._sports_tag_ids:
                        return True
                elif isinstance(tag, str):
                    if tag.lower() in self.SPORTS_SLUGS or tag in self._sports_tag_ids:
                        return True
        
        slug = trade_or_event.get('slug', '').lower()
        title = trade_or_event.get('title', '').lower()
        outcome = trade_or_event.get('outcome', '').lower()
        
        all_text = f"{slug} {title} {outcome}"
        
        sports_terms = ['nba', 'nfl', 'mlb', 'nhl', 'ufc', 'boxing', 'soccer', 
                       'basketball', 'baseball', 'hockey', 'tennis', 'golf', 
                       'f1', 'epl', 'premier-league', 'super-bowl', 'world-series', 
                       'stanley-cup', 'esports', 'league-of-legends', 'dota', 'csgo',
                       'valorant', 'champions-league', 'mma', 'cricket', 'fifa',
                       'world-cup', 'olympics', 'ncaa', 'college-football', 'college-basketball',
                       'warriors', 'lakers', 'celtics', 'nets', 'bulls', 'knicks',
                       'patriots', 'chiefs', 'cowboys', 'eagles', 'packers', '49ers',
                       'yankees', 'dodgers', 'red sox', 'cubs', 'mets', 'braves',
                       'lebron', 'curry', 'durant', 'mahomes', 'brady', 'ronaldo', 'messi']
        
        for term in sports_terms:
            if term in all_text:
                return True
        
        return False
    
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
    
    async def get_wallet_activity(self, wallet_address: str, activity_type: str = "REDEEM", limit: int = 20) -> List[Dict[str, Any]]:
        await self.ensure_session()
        try:
            async with self.session.get(
                f"{self.DATA_API_BASE_URL}/activity",
                params={"user": wallet_address, "type": activity_type, "limit": limit}
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data if isinstance(data, list) else []
                return []
        except Exception as e:
            print(f"Error fetching activity for {wallet_address}: {e}")
            return []
    
    async def get_wallet_positions(self, wallet_address: str) -> List[Dict[str, Any]]:
        await self.ensure_session()
        try:
            async with self.session.get(
                f"{self.DATA_API_BASE_URL}/positions",
                params={"user": wallet_address}
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data if isinstance(data, list) else []
                return []
        except Exception as e:
            print(f"Error fetching positions for {wallet_address}: {e}")
            return []
    
    async def get_wallet_pnl_stats(self, wallet_address: str, force_refresh: bool = False) -> Dict[str, Any]:
        wallet_lower = wallet_address.lower()
        now = datetime.utcnow()
        
        if not force_refresh and wallet_lower in self._wallet_stats_cache:
            last_updated = self._wallet_stats_updated.get(wallet_lower)
            if last_updated and (now - last_updated).total_seconds() < 600:
                return self._wallet_stats_cache[wallet_lower]
        
        await self.ensure_session()
        stats = {'pnl': 0.0, 'win_rate': 0.0, 'total_positions': 0, 'winning_positions': 0}
        
        try:
            async with self.session.get(
                f"{self.GAMMA_BASE_URL}/positions",
                params={"user": wallet_address}
            ) as resp:
                if resp.status == 200:
                    positions = await resp.json()
                    if isinstance(positions, list):
                        total_pnl = 0.0
                        resolved_count = 0
                        winning_count = 0
                        
                        for pos in positions:
                            realized_pnl = float(pos.get('realizedPnl', 0) or 0)
                            current_value = float(pos.get('currentValue', 0) or 0)
                            size = float(pos.get('size', 0) or 0)
                            
                            is_closed = size == 0 or current_value == 0
                            
                            total_pnl += realized_pnl
                            
                            if is_closed and realized_pnl != 0:
                                resolved_count += 1
                                if realized_pnl > 0:
                                    winning_count += 1
                        
                        win_rate = (winning_count / resolved_count * 100) if resolved_count > 0 else 0.0
                        
                        stats = {
                            'pnl': total_pnl,
                            'win_rate': win_rate,
                            'total_positions': resolved_count,
                            'winning_positions': winning_count
                        }
        except Exception as e:
            print(f"Error fetching PnL stats for {wallet_address}: {e}")
        
        self._wallet_stats_cache[wallet_lower] = stats
        self._wallet_stats_updated[wallet_lower] = now
        return stats
    
    def get_market_slug(self, trade_or_activity: Dict[str, Any]) -> Optional[str]:
        slug = trade_or_activity.get('slug') or trade_or_activity.get('marketSlug')
        if slug:
            return slug
        
        market_info = self.get_market_info(trade_or_activity)
        if market_info:
            return market_info.get('slug')
        
        return None
    
    def get_market_url(self, trade_or_activity: Dict[str, Any]) -> str:
        slug = self.get_market_slug(trade_or_activity)
        if slug:
            clean_slug = slug.split('?')[0].strip('/')
            return f"https://polymarket.com/event/{clean_slug}"
        
        condition_id = trade_or_activity.get('conditionId', trade_or_activity.get('condition_id', ''))
        if condition_id:
            return f"https://polymarket.com/markets?condition_id={condition_id}"
        
        return "https://polymarket.com"
    
    def get_unique_activity_id(self, activity: Dict[str, Any]) -> str:
        import hashlib
        tx_hash = activity.get('transactionHash', '')
        timestamp = str(activity.get('timestamp', ''))
        activity_type = activity.get('type', '')
        user = activity.get('proxyWallet', activity.get('user', ''))
        condition_id = activity.get('conditionId', '')[:20]
        raw_key = f"{tx_hash}_{timestamp}_{activity_type}_{user}_{condition_id}"
        hashed = hashlib.sha256(raw_key.encode()).hexdigest()[:64]
        return f"A_{hashed}"
    
    async def get_active_markets_prices(self, limit: int = 200) -> List[Dict[str, Any]]:
        await self.ensure_session()
        try:
            async with self.session.get(
                f"{self.GAMMA_BASE_URL}/markets",
                params={"limit": limit, "active": "true", "closed": "false"}
            ) as resp:
                if resp.status == 200:
                    markets = await resp.json()
                    result = []
                    for m in markets:
                        if self.is_sports_market(m):
                            continue
                        condition_id = m.get('conditionId')
                        if not condition_id:
                            continue
                        
                        outcome_prices = m.get('outcomePrices', [0.5, 0.5])
                        if isinstance(outcome_prices, str):
                            try:
                                outcome_prices = outcome_prices.strip('[]').split(',')
                                yes_price = float(outcome_prices[0]) if outcome_prices else 0.5
                            except (ValueError, IndexError):
                                yes_price = 0.5
                        elif isinstance(outcome_prices, list) and len(outcome_prices) > 0:
                            try:
                                yes_price = float(outcome_prices[0])
                            except (ValueError, TypeError):
                                yes_price = 0.5
                        else:
                            yes_price = 0.5
                        
                        result.append({
                            'condition_id': condition_id,
                            'title': m.get('question', m.get('title', 'Unknown')),
                            'slug': m.get('slug', ''),
                            'yes_price': yes_price,
                            'volume': float(m.get('volume', 0) or 0)
                        })
                    return result
                return []
        except Exception as e:
            print(f"Error fetching market prices: {e}")
            return []


polymarket_client = PolymarketClient()
