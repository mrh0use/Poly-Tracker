import aiohttp
import asyncio
import websockets
from websockets.protocol import State as WSState
import json
import time
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Callable


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
        self._wallet_history_cache: Dict[str, bool] = {}
        self._wallet_history_updated: Dict[str, datetime] = {}
        self._top_traders_cache: List[Dict[str, Any]] = []
        self._top_traders_updated: Optional[datetime] = None
    
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
                        events = market.get('events', [])
                        event_slug = events[0].get('slug', '') if events else ''
                        if condition_id:
                            self._market_cache[condition_id] = {
                                'slug': market.get('slug', ''),
                                'title': market.get('question', market.get('title', '')),
                                'tags': market.get('tags', []),
                                'groupSlug': market.get('groupSlug', ''),
                                'eventSlug': event_slug,
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
                                    'eventSlug': event_slug,
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
    
    def is_sports_market(self, trade: Dict[str, Any]) -> bool:
        market_info = self.get_market_info(trade)
        if not market_info:
            return False
        
        tags = market_info.get('tags', [])
        for tag in tags:
            if isinstance(tag, dict):
                tag_id = tag.get('id', '').lower()
                tag_label = tag.get('label', '').lower()
                if tag_id in self._sports_tag_ids or tag_label in self.SPORTS_SLUGS:
                    return True
            elif isinstance(tag, str):
                if tag.lower() in self._sports_tag_ids or tag.lower() in self.SPORTS_SLUGS:
                    return True
        
        slug = market_info.get('slug', '').lower()
        event_slug = market_info.get('eventSlug', '').lower()
        group_slug = market_info.get('groupSlug', '').lower()
        
        for check_slug in [slug, event_slug, group_slug]:
            if any(sport in check_slug for sport in self.SPORTS_SLUGS):
                return True
        
        return False
    
    async def get_wallet_pnl_stats(self, wallet_address: str) -> Dict[str, Any]:
        cache_key = wallet_address.lower()
        now = datetime.utcnow()
        
        if cache_key in self._wallet_stats_cache:
            last_updated = self._wallet_stats_updated.get(cache_key)
            if last_updated and (now - last_updated).total_seconds() < 600:
                return self._wallet_stats_cache[cache_key]
        
        await self.ensure_session()
        try:
            async with self.session.get(
                f"https://lb-api.polymarket.com/leaderboard",
                params={"address": wallet_address}
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data and len(data) > 0:
                        stats = {
                            'pnl': data[0].get('profit', 0),
                            'volume': data[0].get('volumeTraded', 0),
                            'rank': data[0].get('rank')
                        }
                        self._wallet_stats_cache[cache_key] = stats
                        self._wallet_stats_updated[cache_key] = now
                        return stats
        except Exception as e:
            print(f"Error fetching wallet stats for {wallet_address}: {e}")
        
        return {'pnl': None, 'volume': 0, 'rank': None}
    
    async def check_wallet_has_history(self, wallet_address: str) -> bool:
        cache_key = wallet_address.lower()
        now = datetime.utcnow()
        
        if cache_key in self._wallet_history_cache:
            last_updated = self._wallet_history_updated.get(cache_key)
            if last_updated and (now - last_updated).total_seconds() < 3600:
                return self._wallet_history_cache[cache_key]
        
        trades = await self.get_wallet_trades(wallet_address, limit=1)
        has_history = len(trades) > 0
        
        self._wallet_history_cache[cache_key] = has_history
        self._wallet_history_updated[cache_key] = now
        
        return has_history
    
    async def get_top_traders(self) -> List[Dict[str, Any]]:
        now = datetime.utcnow()
        
        if self._top_traders_cache and self._top_traders_updated:
            age = (now - self._top_traders_updated).total_seconds()
            if age < 600:
                return self._top_traders_cache
        
        await self.ensure_session()
        try:
            async with self.session.get(
                "https://lb-api.polymarket.com/leaderboard",
                params={"limit": 25}
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    self._top_traders_cache = data if isinstance(data, list) else []
                    self._top_traders_updated = now
                    return self._top_traders_cache
        except Exception as e:
            print(f"Error fetching top traders: {e}")
        
        return []
    
    def is_top_trader(self, wallet_address: str) -> Optional[Dict[str, Any]]:
        wallet_lower = wallet_address.lower()
        for trader in self._top_traders_cache:
            if trader.get('address', '').lower() == wallet_lower:
                return trader
        return None
    
    async def get_trending_markets(self, limit: int = 10, sports_only: bool = False) -> List[Dict[str, Any]]:
        await self.ensure_session()
        params = {
            "limit": limit,
            "order": "volume24hr",
            "ascending": "false",
            "active": "true",
            "closed": "false"
        }
        
        try:
            async with self.session.get(f"{self.GAMMA_BASE_URL}/markets", params=params) as resp:
                if resp.status == 200:
                    markets = await resp.json()
                    if sports_only:
                        sports_markets = []
                        for market in markets:
                            tags = market.get('tags', [])
                            is_sports = False
                            for tag in tags:
                                if isinstance(tag, dict):
                                    tag_id = tag.get('id', '').lower()
                                    tag_label = tag.get('label', '').lower()
                                    if tag_id in self._sports_tag_ids or tag_label in self.SPORTS_SLUGS:
                                        is_sports = True
                                        break
                                elif isinstance(tag, str):
                                    if tag.lower() in self._sports_tag_ids or tag.lower() in self.SPORTS_SLUGS:
                                        is_sports = True
                                        break
                            if is_sports:
                                sports_markets.append(market)
                        return sports_markets[:limit]
                    return markets
        except Exception as e:
            print(f"Error fetching trending markets: {e}")
        
        return []
    
    async def search_markets(self, keywords: str, limit: int = 10) -> List[Dict[str, Any]]:
        await self.ensure_session()
        try:
            async with self.session.get(
                f"{self.GAMMA_BASE_URL}/markets",
                params={
                    "limit": limit,
                    "active": "true",
                    "closed": "false"
                }
            ) as resp:
                if resp.status == 200:
                    all_markets = await resp.json()
                    keywords_lower = keywords.lower()
                    matches = []
                    for market in all_markets:
                        title = market.get('question', market.get('title', '')).lower()
                        if keywords_lower in title:
                            matches.append(market)
                            if len(matches) >= limit:
                                break
                    return matches
        except Exception as e:
            print(f"Error searching markets: {e}")
        
        return []
    
    async def get_market_orderbook(self, condition_id: str) -> Dict[str, Any]:
        await self.ensure_session()
        try:
            async with self.session.get(
                f"https://clob.polymarket.com/book",
                params={"token_id": condition_id}
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
        except Exception as e:
            print(f"Error fetching orderbook: {e}")
        
        return {}


class PolymarketWebSocket:
    """
    Production-ready WebSocket client for Polymarket RTDS feed.
    
    KEY CHANGE: Does NOT rely on ping/pong for health monitoring.
    Instead uses data activity timeout which works reliably across all platforms.
    """
    
    RTDS_URL = "wss://ws-live-data.polymarket.com"
    
    # CRITICAL: Removed aggressive ping/pong logic
    # Data timeout is the ONLY health check needed
    DATA_TIMEOUT = 120  # 2 minutes of no data = reconnect
    MAX_CONNECTION_AGE = 900  # 15 minutes = proactive reconnect (prevents 20min freeze bug)
    
    DEBUG_MODE = False  # Set to True to see detailed WebSocket logs
    DEBUG_LOG_FIRST_N = 20  # How many initial messages to log in detail
    
    def __init__(self, on_trade_callback: Optional[Callable] = None):
        self.on_trade_callback = on_trade_callback
        self._running = False
        self._reconnect_delay = 2
        self._max_reconnect_delay = 30
        
        self._primary_ws = None
        self._backup_ws = None
        
        self._last_data_time = time.time()
        self._connection_start_time = time.time()
        self._total_trades = 0
        
        self._debug_msg_count = 0
        self._debug_trade_count = 0
        self._debug_non_trade_count = 0
        self._debug_empty_count = 0
        self._debug_error_count = 0
        self._debug_last_msg_time = None
        self._debug_topics_seen = set()
        self._debug_types_seen = set()
        
        self._backup_task = None
        self._monitor_task = None
    
    def _is_ws_open(self, ws) -> bool:
        """Check if a WebSocket connection is open."""
        if ws is None:
            return False
        try:
            return ws.state == WSState.OPEN
        except:
            return False
    
    async def _create_connection(self, name: str):
        """Create and subscribe a new WebSocket connection."""
        try:
            # CRITICAL: ping_interval=None means library won't send automatic pings
            # This prevents the ping timeout issues in production
            ws = await websockets.connect(
                self.RTDS_URL,
                ping_interval=None,  # No automatic pings
                ping_timeout=None,   # No ping timeout enforcement
                close_timeout=10
            )
            
            subscription = {
                "action": "subscribe",
                "subscriptions": [{"topic": "activity", "type": "trades"}]
            }
            await ws.send(json.dumps(subscription))
            
            if self.DEBUG_MODE:
                print(f"[WS {name.upper()}] Connected and subscribed", flush=True)
            
            return ws
        except Exception as e:
            print(f"[WS {name.upper()}] Connection failed: {e}", flush=True)
            return None
    
    async def _maintain_backup(self):
        """Maintain a backup connection ready to take over."""
        while self._running:
            try:
                await asyncio.sleep(30)
                
                if not self._is_ws_open(self._backup_ws):
                    if self.DEBUG_MODE:
                        print("[WS BACKUP] Creating backup connection...", flush=True)
                    self._backup_ws = await self._create_connection("backup")
                    
                    if self._backup_ws:
                        try:
                            # Verify backup is receiving data
                            await asyncio.wait_for(self._backup_ws.recv(), timeout=5)
                            if self.DEBUG_MODE:
                                print("[WS BACKUP] Backup connection verified", flush=True)
                        except asyncio.TimeoutError:
                            pass
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[WS BACKUP] Error maintaining backup: {e}", flush=True)
    
    async def _monitor_health(self):
        """
        Monitor connection health using ONLY data activity timeout.
        This is reliable across all platforms unlike ping/pong.
        """
        while self._running:
            try:
                await asyncio.sleep(10)  # Check every 10 seconds
                
                now = time.time()
                data_age = now - self._last_data_time
                connection_age = now - self._connection_start_time
                
                # ONLY health check: Has data stopped flowing?
                if data_age > self.DATA_TIMEOUT:
                    print(f"[WS MONITOR] No data for {data_age:.0f}s (>{self.DATA_TIMEOUT}s) - switching to backup", flush=True)
                    await self._switch_to_backup()
                
                # Proactive reconnect to avoid 20-minute freeze bug
                elif connection_age > self.MAX_CONNECTION_AGE:
                    print(f"[WS MONITOR] Connection age {connection_age:.0f}s > {self.MAX_CONNECTION_AGE}s - proactive reconnect", flush=True)
                    await self._switch_to_backup()
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[WS MONITOR] Error: {e}", flush=True)
    
    async def _switch_to_backup(self):
        """Switch from primary to backup connection."""
        try:
            if self._is_ws_open(self._backup_ws):
                old_ws = self._primary_ws
                self._primary_ws = self._backup_ws
                self._backup_ws = None
                self._connection_start_time = time.time()
                self._last_data_time = time.time()
                
                print(f"[WS SWITCH] Switched to backup connection", flush=True)
                
                if old_ws:
                    try:
                        await old_ws.close()
                    except:
                        pass
            else:
                print(f"[WS SWITCH] No backup available - creating new primary", flush=True)
                if self._primary_ws:
                    try:
                        await self._primary_ws.close()
                    except:
                        pass
                self._primary_ws = await self._create_connection("primary")
                self._connection_start_time = time.time()
                self._last_data_time = time.time()
        except Exception as e:
            print(f"[WS SWITCH] Error: {e}", flush=True)
    
    async def connect(self):
        """Main connection loop with backup WebSocket support."""
        self._running = True
        reconnect_delay = self._reconnect_delay
        
        while self._running:
            try:
                print("[WebSocket] Connecting to Polymarket RTDS...", flush=True)
                
                self._primary_ws = await self._create_connection("primary")
                if not self._primary_ws:
                    await asyncio.sleep(reconnect_delay)
                    reconnect_delay = min(reconnect_delay * 2, self._max_reconnect_delay)
                    continue
                
                self._connection_start_time = time.time()
                self._last_data_time = time.time()
                reconnect_delay = self._reconnect_delay
                
                self._first_message_logged = False
                self._debug_msg_count = 0
                self._debug_trade_count = 0
                self._debug_non_trade_count = 0
                self._debug_empty_count = 0
                self._debug_error_count = 0
                self._debug_topics_seen = set()
                self._debug_types_seen = set()
                
                # Start background tasks (NO ping task!)
                self._backup_task = asyncio.create_task(self._maintain_backup())
                self._monitor_task = asyncio.create_task(self._monitor_health())
                
                if self.DEBUG_MODE:
                    print(f"[WS DEBUG] Started: data timeout {self.DATA_TIMEOUT}s, max age {self.MAX_CONNECTION_AGE}s", flush=True)
                    print(f"[WS DEBUG] NO PING/PONG - using data activity only for health", flush=True)
                
                while self._running:
                    try:
                        ws = self._primary_ws
                        if not self._is_ws_open(ws):
                            print("[WS] Primary connection lost, reconnecting...", flush=True)
                            break
                        
                        # Wait for messages with 30s timeout
                        message = await asyncio.wait_for(ws.recv(), timeout=30)
                        self._last_data_time = time.time()  # Update activity timestamp
                        
                        if not self._first_message_logged:
                            print(f"[WebSocket] Receiving messages...", flush=True)
                            self._first_message_logged = True
                        
                        await self._handle_message(message)
                        await asyncio.sleep(0)  # Yield CPU
                        
                    except asyncio.TimeoutError:
                        # 30s timeout on recv() is normal, just continue
                        continue
                    except websockets.exceptions.ConnectionClosed:
                        print("[WS] Connection closed, switching to backup...", flush=True)
                        await self._switch_to_backup()
                        
            except Exception as e:
                print(f"[WebSocket] Error: {e}. Reconnecting in {reconnect_delay}s...", flush=True)
            finally:
                # Clean up background tasks
                for task in [self._backup_task, self._monitor_task]:
                    if task:
                        task.cancel()
                        try:
                            await task
                        except asyncio.CancelledError:
                            pass
                self._backup_task = None
                self._monitor_task = None
            
            if self._running:
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, self._max_reconnect_delay)
    
    async def _handle_message(self, raw_message: str):
        now = time.time()
        
        try:
            if not raw_message or not raw_message.strip():
                self._debug_empty_count += 1
                if self.DEBUG_MODE:
                    print(f"[WS DEBUG] Empty message #{self._debug_empty_count}", flush=True)
                return
            
            self._debug_msg_count += 1
            
            if self.DEBUG_MODE and self._debug_msg_count <= self.DEBUG_LOG_FIRST_N:
                preview = raw_message[:500] if len(raw_message) > 500 else raw_message
                print(f"[WS DEBUG] Message #{self._debug_msg_count} (len={len(raw_message)}): {preview}", flush=True)
            
            if self._debug_last_msg_time and self.DEBUG_MODE:
                gap = now - self._debug_last_msg_time
                if self._debug_msg_count <= 20 or gap > 5:
                    print(f"[WS DEBUG] Time since last msg: {gap:.2f}s", flush=True)
            self._debug_last_msg_time = now
            
            message = json.loads(raw_message)
            
            topic = message.get('topic', 'unknown')
            msg_type = message.get('type', 'unknown')
            self._debug_topics_seen.add(topic)
            self._debug_types_seen.add(msg_type)
            
            if self.DEBUG_MODE and self._debug_msg_count <= self.DEBUG_LOG_FIRST_N:
                print(f"[WS DEBUG] topic={topic}, type={msg_type}, has_payload={message.get('payload') is not None}", flush=True)
            
            payload = message.get('payload')
            if payload and self.on_trade_callback:
                trade = self._normalize_trade(payload)
                if trade:
                    self._debug_trade_count += 1
                    if self._debug_trade_count % 1000 == 0:
                        print(f"[WS] Trades processed: {self._debug_trade_count}", flush=True)
                        if self.DEBUG_MODE:
                            print(f"[WS DEBUG STATS] msgs={self._debug_msg_count}, trades={self._debug_trade_count}, non_trades={self._debug_non_trade_count}, errors={self._debug_error_count}", flush=True)
                            print(f"[WS DEBUG STATS] topics_seen={self._debug_topics_seen}, types_seen={self._debug_types_seen}", flush=True)
                    await self.on_trade_callback(trade)
                    await asyncio.sleep(0)
            else:
                self._debug_non_trade_count += 1
                if self.DEBUG_MODE and self._debug_non_trade_count <= 5:
                    print(f"[WS DEBUG] Non-trade message #{self._debug_non_trade_count}: topic={topic}, type={msg_type}", flush=True)
                        
        except json.JSONDecodeError as e:
            self._debug_error_count += 1
            if self.DEBUG_MODE:
                preview = raw_message[:200] if raw_message and len(raw_message) > 200 else raw_message
                print(f"[WS DEBUG] JSON decode error #{self._debug_error_count}: {e}, msg={preview}", flush=True)
        except Exception as e:
            self._debug_error_count += 1
            print(f"[WebSocket] Error handling message #{self._debug_msg_count}: {e}", flush=True)
    
    def _normalize_trade(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            size = float(payload.get('size', 0) or 0)
            price = float(payload.get('price', 0) or 0)
            
            return {
                'proxyWallet': payload.get('proxyWallet', ''),
                'side': payload.get('side', 'BUY'),
                'asset': payload.get('asset', ''),
                'conditionId': payload.get('conditionId', ''),
                'size': size,
                'price': price,
                'timestamp': payload.get('timestamp', 0),
                'title': payload.get('title', ''),
                'slug': payload.get('slug', ''),
                'icon': payload.get('icon', ''),
                'eventSlug': payload.get('eventSlug', ''),
                'outcome': payload.get('outcome', 'Yes'),
                'outcomeIndex': payload.get('outcomeIndex', 0),
                'name': payload.get('name', ''),
                'pseudonym': payload.get('pseudonym', ''),
                'transactionHash': payload.get('transactionHash', ''),
            }
        except Exception as e:
            print(f"[WebSocket] Error normalizing trade: {e}")
            return None
    
    async def disconnect(self):
        self._running = False
        for ws in [self._primary_ws, self._backup_ws]:
            if ws:
                try:
                    await ws.close()
                except:
                    pass
        self._primary_ws = None
        self._backup_ws = None
        print("[WebSocket] Disconnected")


polymarket_client = PolymarketClient()
