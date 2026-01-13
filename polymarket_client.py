import aiohttp
import asyncio
import websockets
from websockets.protocol import State as WSState
import json
import time
import re
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Callable


def keyword_matches(keyword: str, text: str) -> bool:
    """
    Check if keyword matches in text with word boundary awareness.
    Short keywords (<=4 chars) require word boundaries to avoid false positives.
    Longer keywords can match as substrings.
    """
    if len(keyword) <= 4:
        pattern = r'\b' + re.escape(keyword) + r'\b'
        return bool(re.search(pattern, text, re.IGNORECASE))
    else:
        return keyword.lower() in text.lower()


class PolymarketClient:
    DATA_API_BASE_URL = "https://data-api.polymarket.com"
    GAMMA_BASE_URL = "https://gamma-api.polymarket.com"
    
    SPORTS_SLUGS = {'sports', 'nba', 'nfl', 'mlb', 'nhl', 'soccer', 'football', 'basketball', 
                   'baseball', 'hockey', 'tennis', 'golf', 'ufc', 'mma', 'boxing', 'f1', 
                   'formula-1', 'cricket', 'esports', 'league-of-legends', 'dota', 'csgo',
                   'valorant', 'nba-games', 'nfl-games', 'epl', 'premier-league', 'champions-league'}
    
    CATEGORY_TAG_MAP = {
        'politics': {'politics', 'political'},
        'sports': {'sports', 'nba', 'nfl', 'mlb', 'nhl', 'soccer', 'football', 'basketball', 
                   'baseball', 'hockey', 'tennis', 'golf', 'ufc', 'mma', 'boxing', 'f1', 
                   'formula-1', 'cricket', 'esports', 'epl', 'premier-league', 'champions-league',
                   'nba-games', 'nfl-games', 'college-football', 'college-basketball'},
        'crypto': {'crypto', 'cryptocurrency', 'bitcoin', 'ethereum', 'btc', 'eth', 'defi', 'nft', 'solana',
                   'crypto-prices', 'price-prediction', 'altcoins', 'memecoin', 'memecoins', 'dogecoin', 
                   'cardano', 'ripple', 'xrp', 'polkadot', 'chainlink', 'avalanche', 'polygon', 'matic',
                   'litecoin', 'binance', 'coinbase', 'web3', 'blockchain'},
        'finance': {'finance', 'financial', 'stocks', 'markets', 'fed', 'interest-rates', 'bonds'},
        'geopolitics': {'geopolitics', 'geopolitical', 'international', 'war', 'conflict'},
        'earnings': {'earnings', 'quarterly-earnings', 'earnings-reports'},
        'tech': {'tech', 'technology', 'ai', 'artificial-intelligence', 'software', 'apple', 'google', 'microsoft'},
        'culture': {'culture', 'pop-culture', 'entertainment', 'celebrities', 'movies', 'music', 'tv', 'awards'},
        'world': {'world', 'world-news', 'global'},
        'economy': {'economy', 'economic', 'gdp', 'inflation', 'unemployment', 'recession'},
        'climate-science': {'climate', 'science', 'climate-science', 'weather', 'environment', 'temperature'},
        'elections': {'elections', 'election', 'voting', '2024-election', '2025-election', '2026-election', 'presidential'},
        'mentions': {'mentions', 'tweet-count', 'tweets', 'social-media', 'x-mentions'},
    }
    
    SPORTS_KEYWORDS = [
        # League/sport names (unique and safe)
        'nba', 'nfl', 'mlb', 'nhl', 'ufc', 'boxing', 'soccer', 'basketball', 'baseball',
        'hockey', 'tennis', 'golf', 'f1', 'epl', 'premier league', 'super bowl', 'world series',
        'stanley cup', 'esports', 'league of legends', 'dota', 'csgo', 'valorant',
        'champions league', 'mma', 'cricket', 'fifa', 'world cup', 'olympics', 'ncaa',
        'college football', 'college basketball', 'la liga', 'serie a', 'bundesliga', 'ligue 1',
        'saudi pro league', 'saudi club', 'saudi league', 'spl',
        'al nassr', 'al hilal', 'al ittihad', 'al ahli', 'al shabab', 'al fateh',
        'al taawoun', 'al ettifaq', 'al khaleej', 'al raed', 'al riyadh', 'al hazem',
        'al feiha', 'al okhdood', 'al orubah', 'al qadsiah', 'al wehda', 'damac',
        'counter-strike', 'cs2', 'e-sports', 'overwatch', 'rocket league',
        'map 1 winner', 'map 2 winner', 'map 3 winner', 'map winner',
        'bayer leverkusen', 'bayer 04 leverkusen', 'vfb stuttgart', 'rb leipzig',
        'eintracht frankfurt', 'sc freiburg', 'union berlin', 'werder bremen',
        'kansas jayhawks', 'west virginia mountaineers', 'duke blue devils',
        'kentucky wildcats', 'north carolina tar heels', 'purdue boilermakers',
        'nba trade', 'nfl trade', 'mlb trade', 'nhl trade',
        'nba playoffs', 'nfl playoffs', 'mlb playoffs', 'nhl playoffs',
        # NBA teams (full names to avoid conflicts)
        'golden state warriors', 'los angeles lakers', 'boston celtics', 'brooklyn nets',
        'chicago bulls', 'new york knicks', 'miami heat', 'milwaukee bucks',
        'philadelphia 76ers', 'phoenix suns', 'denver nuggets', 'la clippers',
        'dallas mavericks', 'houston rockets', 'minnesota timberwolves', 'memphis grizzlies',
        'new orleans pelicans', 'oklahoma city thunder', 'portland trail blazers',
        'sacramento kings', 'atlanta hawks', 'charlotte hornets', 'cleveland cavaliers',
        'detroit pistons', 'indiana pacers', 'orlando magic', 'washington wizards', 'toronto raptors',
        # NFL teams (full names to avoid conflicts like bills, bears, saints)
        'new england patriots', 'kansas city chiefs', 'dallas cowboys', 'philadelphia eagles',
        'green bay packers', 'san francisco 49ers', 'baltimore ravens', 'buffalo bills',
        'miami dolphins', 'new york jets', 'pittsburgh steelers', 'cincinnati bengals',
        'cleveland browns', 'tennessee titans', 'indianapolis colts', 'jacksonville jaguars',
        'houston texans', 'denver broncos', 'las vegas raiders', 'los angeles chargers',
        'seattle seahawks', 'los angeles rams', 'arizona cardinals', 'minnesota vikings',
        'chicago bears', 'detroit lions', 'washington commanders', 'new york giants',
        'new orleans saints', 'atlanta falcons', 'tampa bay buccaneers', 'carolina panthers',
        # MLB teams
        'new york yankees', 'los angeles dodgers', 'boston red sox', 'chicago cubs',
        'new york mets', 'atlanta braves', 'houston astros', 'philadelphia phillies',
        'san diego padres', 'texas rangers', 'baltimore orioles', 'minnesota twins',
        'cleveland guardians', 'seattle mariners', 'tampa bay rays', 'toronto blue jays',
        'milwaukee brewers', 'arizona diamondbacks', 'colorado rockies', 'cincinnati reds',
        'pittsburgh pirates', 'washington nationals', 'miami marlins', 'kansas city royals',
        'detroit tigers', 'chicago white sox', 'los angeles angels', 'oakland athletics',
        # Soccer clubs (fully qualified names)
        'real madrid', 'fc barcelona', 'atletico madrid', 'manchester united', 'man united',
        'manchester city', 'man city', 'liverpool fc', 'chelsea fc', 'arsenal fc',
        'tottenham hotspur', 'bayern munich', 'borussia dortmund', 'juventus fc', 'inter milan',
        'ac milan', 'ssc napoli', 'psg', 'paris saint-germain', 'afc ajax', 'sl benfica', 'fc porto',
        'sevilla fc', 'valencia cf', 'villarreal cf', 'athletic bilbao', 'real sociedad',
        'west ham united', 'newcastle united', 'aston villa fc', 'everton fc', 'wolverhampton wanderers',
        'brighton and hove albion', 'crystal palace fc', 'brentford fc', 'fulham fc', 'afc bournemouth', 'nottingham forest',
        # Athletes (full names only)
        'lebron james', 'stephen curry', 'kevin durant', 'giannis antetokounmpo', 'nikola jokic',
        'joel embiid', 'jayson tatum', 'luka doncic', 'ja morant', 'anthony edwards',
        'devin booker', 'damian lillard', 'james harden',
        'patrick mahomes', 'joe burrow', 'josh allen', 'lamar jackson', 'jalen hurts',
        'justin herbert', 'dak prescott', 'aaron rodgers', 'tom brady', 'travis kelce',
        'tyreek hill', 'jamarr chase', 'justin jefferson', 'stefon diggs', 'derrick henry',
        'aaron judge', 'shohei ohtani', 'mike trout', 'mookie betts', 'juan soto',
        'ronald acuna', 'fernando tatis', 'vladimir guerrero',
        'cristiano ronaldo', 'lionel messi', 'kylian mbappe', 'erling haaland', 'vinicius jr',
        'jude bellingham', 'mohamed salah', 'kevin de bruyne', 'harry kane', 'heung-min son',
        'bukayo saka', 'martin odegaard', 'phil foden', 'cole palmer', 'declan rice',
        # Tournaments (unique enough)
        'copa america', 'euro 2024', 'copa libertadores', 'concacaf', 'eredivisie',
        'nba finals', 'nfl championship', 'grand slam', 'wimbledon', 'us open tennis',
        'french open', 'australian open', 'pga tour', 'ryder cup', 'formula 1', 'grand prix',
        # Combat sports (unique names)
        'jake paul', 'mike tyson', 'canelo alvarez', 'tyson fury', 'oleksandr usyk',
        'anthony joshua', 'conor mcgregor', 'jon jones', 'israel adesanya', 'alex pereira',
        'sean strickland', 'ufc fight', 'boxing match',
    ]
    
    CRYPTO_KEYWORDS = [
        'bitcoin', 'btc', 'ethereum', 'solana', 'crypto', 'cryptocurrency',
        'dogecoin', 'doge', 'cardano', 'ripple', 'xrp', 'polkadot', 'chainlink',
        'avax', 'polygon', 'matic', 'cosmos', 'uniswap', 'litecoin',
        'defi', 'nft', 'blockchain', 'coinbase', 'binance', 'kraken',
        'bitcoin price', 'eth price', 'crypto market', 'altcoin', 'memecoin',
        'satoshi', 'halving', 'staking', 'mining', 'web3',
    ]
    
    POLITICS_KEYWORDS = [
        'trump', 'biden', 'harris', 'election', 'president', 'congress', 'senate', 'governor',
        'republican', 'democrat', 'gop', 'dnc', 'rnc', 'primary', 'electoral', 'vote', 'ballot',
        'impeachment', 'legislation', 'bill', 'law', 'policy', 'administration',
        'white house', 'capitol', 'scotus', 'supreme court', 'federal reserve', 'fed rate',
        'desantis', 'newsom', 'ocasio-cortez', 'pelosi', 'mcconnell', 'schumer',
        'midterm', 'swing state', 'polling', 'approval rating',
        'russia', 'ukraine', 'china', 'taiwan', 'north korea', 'iran', 'israel', 'palestine', 'gaza',
        'nato', 'un', 'g7', 'g20', 'tariff', 'sanction', 'treaty', 'diplomacy',
    ]
    
    ENTERTAINMENT_KEYWORDS = [
        'oscars', 'emmy', 'grammy', 'golden globe', 'academy award', 'netflix', 'disney',
        'marvel', 'dc', 'box office', 'movie', 'film', 'actor', 'actress', 'celebrity',
        'taylor swift', 'beyonce', 'drake', 'kanye', 'kardashian', 'elon musk', 'twitter',
        'tiktok', 'youtube', 'spotify', 'streaming', 'album', 'concert', 'tour',
        'reality tv', 'bachelor', 'survivor', 'american idol', 'the voice',
        'met gala', 'super bowl halftime', 'coachella', 'burning man',
        'video game', 'playstation', 'xbox', 'nintendo', 'steam', 'twitch',
    ]
    
    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self._known_wallets: set = set()
        self._sports_tag_ids: set = set()
        self._sports_team_names: set = set()  # Team names from /teams API
        self._market_cache: Dict[str, Dict[str, Any]] = {}
        self._cache_last_updated: Optional[datetime] = None
        self._wallet_stats_cache: Dict[str, Dict[str, Any]] = {}
        self._wallet_stats_updated: Dict[str, datetime] = {}
        self._wallet_history_cache: Dict[str, bool] = {}
        self._wallet_history_updated: Dict[str, datetime] = {}
        self._top_traders_cache: List[Dict[str, Any]] = []
        self._top_traders_updated: Optional[datetime] = None
        self._proxy_to_trader_map: Dict[str, Dict[str, Any]] = {}
        self._non_top_trader_cache: Dict[str, datetime] = {}  # Negative result cache (24 hour TTL)
    
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
        """
        Fetch sports tag IDs from Polymarket's /sports endpoint.
        These tags are used to identify sports markets reliably via API data.
        """
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
                    print(f"[API] Loaded {len(tag_ids)} sports tag IDs from Polymarket API", flush=True)
                    return tag_ids
        except Exception as e:
            print(f"[API] Error fetching sports tags: {e}", flush=True)
        return set()
    
    async def fetch_sports_teams(self) -> set:
        """
        Fetch team names from Polymarket's /teams endpoint.
        Returns a set of team names (lowercase) for keyword matching.
        """
        await self.ensure_session()
        try:
            async with self.session.get(f"{self.GAMMA_BASE_URL}/teams?limit=1000") as resp:
                if resp.status == 200:
                    teams_data = await resp.json()
                    team_names = set()
                    for team in teams_data:
                        name = team.get('name') or ''
                        alias = team.get('alias') or ''
                        name = name.lower().strip() if name else ''
                        alias = alias.lower().strip() if alias else ''
                        if name:
                            team_names.add(name)
                        if alias:
                            team_names.add(alias)
                    self._sports_team_names = team_names
                    print(f"[API] Loaded {len(team_names)} sports team names from Polymarket API", flush=True)
                    return team_names
        except Exception as e:
            print(f"[API] Error fetching sports teams: {e}", flush=True)
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
                                'marketId': market.get('id', ''),
                            }
                        tokens = market.get('tokens', [])
                        for token in tokens:
                            token_id = token.get('token_id') or token.get('tokenId', '')
                            if token_id:
                                self._market_cache[token_id] = {
                                    'slug': market.get('slug', ''),
                                    'title': market.get('question', market.get('title', '')),
                                    'tags': market.get('tags', []),
                                    'groupSlug': market.get('groupSlug', ''),
                                    'eventSlug': event_slug,
                                    'marketId': market.get('id', ''),
                                }
                        
                        clob_token_ids_raw = market.get('clobTokenIds', [])
                        if isinstance(clob_token_ids_raw, str):
                            try:
                                clob_token_ids = json.loads(clob_token_ids_raw)
                            except:
                                clob_token_ids = []
                        else:
                            clob_token_ids = clob_token_ids_raw if isinstance(clob_token_ids_raw, list) else []
                        
                        for token_id in clob_token_ids:
                            if token_id and token_id not in self._market_cache:
                                self._market_cache[token_id] = {
                                    'slug': market.get('slug', ''),
                                    'title': market.get('question', market.get('title', '')),
                                    'tags': market.get('tags', []),
                                    'groupSlug': market.get('groupSlug', ''),
                                    'eventSlug': event_slug,
                                    'marketId': market.get('id', ''),
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
    
    def get_market_categories(self, asset_id: str, fallback_title: str = "", fallback_slug: str = "") -> set:
        """
        Get the top-level Polymarket categories for a market.
        Returns set of category slugs like {'sports', 'politics', 'crypto'}
        
        Detection priority:
        1. API-provided sports tag IDs (from /sports endpoint) - most reliable
        2. Tag slug matching via CATEGORY_TAG_MAP
        3. API team names (from /teams endpoint)
        4. Keyword matching as fallback
        """
        market_info = self._market_cache.get(asset_id, {})
        tags = market_info.get('tags', [])
        
        market_tag_slugs = set()
        market_tag_ids = set()
        for tag in tags:
            if isinstance(tag, dict):
                slug = tag.get('slug', '').lower()
                tag_id = str(tag.get('id', ''))
                if slug:
                    market_tag_slugs.add(slug)
                if tag_id:
                    market_tag_ids.add(tag_id)
            elif isinstance(tag, str):
                market_tag_slugs.add(tag.lower())
        
        group_slug = market_info.get('groupSlug', '').lower()
        if group_slug:
            market_tag_slugs.add(group_slug)
        
        categories = set()
        for category, related_tags in self.CATEGORY_TAG_MAP.items():
            if market_tag_slugs & related_tags:
                categories.add(category)
        
        if 'sports' not in categories and self._sports_tag_ids:
            if market_tag_ids & self._sports_tag_ids:
                categories.add('sports')
        
        if 'sports' not in categories:
            if group_slug in self.SPORTS_SLUGS:
                categories.add('sports')
        
        title = market_info.get('title', '').lower() or fallback_title.lower()
        slug = market_info.get('slug', '').lower() or fallback_slug.lower()
        text = f"{title} {slug}"
        
        if 'sports' not in categories:
            for kw in self.SPORTS_KEYWORDS:
                if keyword_matches(kw, text):
                    categories.add('sports')
                    break
        
        if 'sports' not in categories and self._sports_team_names:
            for team in self._sports_team_names:
                if len(team) > 3 and team in text:
                    categories.add('sports')
                    break
        
        if 'sports' not in categories:
            if re.search(r'will .+ win on \d{4}-\d{2}-\d{2}', text):
                categories.add('sports')
            elif re.search(r'\bvs\.?\s+\w', text):
                categories.add('sports')
            elif 'end in a draw' in text or 'end in draw' in text:
                categories.add('sports')
        
        if 'crypto' not in categories:
            for kw in self.CRYPTO_KEYWORDS:
                if keyword_matches(kw, text):
                    categories.add('crypto')
                    break
        
        if 'finance' not in categories:
            finance_keywords = ['stock', 'stocks', 'treasury', 'federal reserve', 
                               'interest rate', 'bonds', 'inflation', 
                               'recession', 'trade deal', 'tariff', 'trade war', 'trade policy']
            for kw in finance_keywords:
                if keyword_matches(kw, text):
                    categories.add('finance')
                    break
        
        if 'economy' not in categories:
            economy_keywords = ['economy', 'economic', 'unemployment', 'jobs report', 'gdp growth']
            for kw in economy_keywords:
                if keyword_matches(kw, text):
                    categories.add('economy')
                    break
        
        if 'mentions' not in categories:
            mentions_keywords = ['mention', 'mentions', 'tweet', 'tweets', 'x post', 'x posts',
                                'twitter mention', 'x mention']
            for kw in mentions_keywords:
                if keyword_matches(kw, text):
                    categories.add('mentions')
                    break
        
        return categories
    
    def is_sports_market(self, trade_or_event: Dict[str, Any]) -> bool:
        """
        Detect if a market is sports-related.
        Priority: 1) Official sports tags/slugs, 2) Sports keywords with context check
        Excludes markets that have finance/economy/crypto categories to avoid false positives.
        """
        market_info = self.get_market_info(trade_or_event)
        has_official_sports_tag = False
        
        if market_info:
            group_slug = market_info.get('groupSlug', '').lower()
            if group_slug in self.SPORTS_SLUGS:
                has_official_sports_tag = True
            
            tags = market_info.get('tags', [])
            if isinstance(tags, list):
                for tag in tags:
                    if isinstance(tag, dict):
                        slug = tag.get('slug', '').lower()
                        tag_id = str(tag.get('id', ''))
                        if slug in self.SPORTS_SLUGS or tag_id in self._sports_tag_ids:
                            has_official_sports_tag = True
                            break
                    elif isinstance(tag, str):
                        if tag.lower() in self.SPORTS_SLUGS or tag in self._sports_tag_ids:
                            has_official_sports_tag = True
                            break
        
        if not has_official_sports_tag:
            tags = trade_or_event.get('tags', [])
            if isinstance(tags, list):
                for tag in tags:
                    if isinstance(tag, dict):
                        slug = tag.get('slug', '').lower()
                        tag_id = str(tag.get('id', ''))
                        if slug in self.SPORTS_SLUGS or tag_id in self._sports_tag_ids:
                            has_official_sports_tag = True
                            break
                    elif isinstance(tag, str):
                        if tag.lower() in self.SPORTS_SLUGS or tag in self._sports_tag_ids:
                            has_official_sports_tag = True
                            break
        
        if has_official_sports_tag:
            return True
        
        asset_id = trade_or_event.get('asset', '')
        if asset_id:
            categories = self.get_market_categories(asset_id)
            conflicting_categories = {'finance', 'economy', 'crypto', 'geopolitics'}
            if categories & conflicting_categories:
                return False
        
        title = ''
        slug = ''
        if market_info:
            title = market_info.get('title', '').lower()
            slug = market_info.get('slug', '').lower()
        
        title = title or trade_or_event.get('title', '').lower()
        slug = slug or trade_or_event.get('slug', '').lower()
        outcome = trade_or_event.get('outcome', '').lower()
        all_text = f"{slug} {title} {outcome}"
        
        ambiguous_terms = {'trade', 'trading'}
        for term in self.SPORTS_KEYWORDS:
            term_base = term.split()[0] if ' ' in term else term
            if term_base in ambiguous_terms:
                continue
            if term in all_text:
                return True
        
        return False
    
    def detect_market_category(self, trade_or_event: Dict[str, Any]) -> str:
        """
        Detect the category of a market.
        Returns: 'sports', 'crypto', 'politics', 'entertainment', or 'other'
        """
        if self.is_sports_market(trade_or_event):
            return 'sports'
        
        market_info = self.get_market_info(trade_or_event)
        title = ''
        slug = ''
        
        if market_info:
            title = market_info.get('title', '').lower()
            slug = market_info.get('slug', '').lower()
        
        title = title or trade_or_event.get('title', '').lower()
        slug = slug or trade_or_event.get('slug', '').lower()
        outcome = trade_or_event.get('outcome', '').lower()
        
        all_text = f"{slug} {title} {outcome}"
        
        for term in self.CRYPTO_KEYWORDS:
            if term in all_text:
                return 'crypto'
        
        for term in self.POLITICS_KEYWORDS:
            if term in all_text:
                return 'politics'
        
        for term in self.ENTERTAINMENT_KEYWORDS:
            if term in all_text:
                return 'entertainment'
        
        return 'other'
    
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
    
    async def get_wallet_usdc_balance(self, wallet_address: str) -> Optional[float]:
        """Get USDC.e balance for a wallet on Polygon."""
        await self.ensure_session()
        
        usdc_contract = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
        balance_of_selector = "0x70a08231"
        padded_address = wallet_address.lower().replace("0x", "").zfill(64)
        data = f"{balance_of_selector}{padded_address}"
        
        rpc_payload = {
            "jsonrpc": "2.0",
            "method": "eth_call",
            "params": [
                {"to": usdc_contract, "data": data},
                "latest"
            ],
            "id": 1
        }
        
        try:
            async with self.session.post(
                "https://polygon-rpc.com",
                json=rpc_payload,
                headers={"Content-Type": "application/json"}
            ) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    if "result" in result and result["result"]:
                        balance_hex = result["result"]
                        balance_raw = int(balance_hex, 16)
                        return balance_raw / 1_000_000
                return None
        except Exception as e:
            print(f"Error fetching USDC balance for {wallet_address}: {e}")
            return None
    
    async def _fetch_positions_paginated(self, wallet_address: str) -> List[Dict[str, Any]]:
        await self.ensure_session()
        all_positions = []
        offset = 0
        limit = 500
        
        while offset <= 10000:
            try:
                async with self.session.get(
                    f"{self.DATA_API_BASE_URL}/positions",
                    params={"user": wallet_address, "limit": limit, "offset": offset, "sizeThreshold": 0}
                ) as resp:
                    if resp.status != 200:
                        break
                    positions = await resp.json()
                    if not isinstance(positions, list) or not positions:
                        break
                    all_positions.extend(positions)
                    if len(positions) < limit:
                        break
                    offset += limit
            except Exception as e:
                print(f"Error fetching positions at offset {offset}: {e}")
                break
        
        return all_positions
    
    async def _fetch_closed_positions_paginated(self, wallet_address: str) -> List[Dict[str, Any]]:
        await self.ensure_session()
        all_closed = []
        offset = 0
        limit = 500
        
        while offset <= 10000:
            try:
                async with self.session.get(
                    f"{self.DATA_API_BASE_URL}/closed-positions",
                    params={"user": wallet_address, "limit": limit, "offset": offset}
                ) as resp:
                    if resp.status != 200:
                        break
                    closed = await resp.json()
                    if not isinstance(closed, list) or not closed:
                        break
                    all_closed.extend(closed)
                    if len(closed) < limit:
                        break
                    offset += limit
            except Exception as e:
                print(f"Error fetching closed positions at offset {offset}: {e}")
                break
        
        return all_closed
    
    async def has_prior_activity(self, wallet_address: str) -> Optional[bool]:
        wallet_lower = wallet_address.lower()
        now = datetime.utcnow()
        
        if wallet_lower in self._wallet_history_cache:
            last_updated = self._wallet_history_updated.get(wallet_lower)
            if last_updated and (now - last_updated).total_seconds() < 3600:
                return self._wallet_history_cache[wallet_lower]
        
        await self.ensure_session()
        try:
            async with self.session.get(
                f"{self.DATA_API_BASE_URL}/activity",
                params={"user": wallet_address, "limit": 1}
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    has_history = isinstance(data, list) and len(data) > 0
                    self._wallet_history_cache[wallet_lower] = has_history
                    self._wallet_history_updated[wallet_lower] = now
                    return has_history
                print(f"Activity API returned status {resp.status} for {wallet_address[:10]}...")
        except Exception as e:
            print(f"Error checking wallet activity for {wallet_address}: {e}")
        return None
    
    async def get_wallet_pnl_stats(self, wallet_address: str, force_refresh: bool = False) -> Dict[str, Any]:
        wallet_lower = wallet_address.lower()
        now = datetime.utcnow()
        
        if not force_refresh and wallet_lower in self._wallet_stats_cache:
            last_updated = self._wallet_stats_updated.get(wallet_lower)
            if last_updated and (now - last_updated).total_seconds() < 600:
                return self._wallet_stats_cache[wallet_lower]
        
        await self.ensure_session()
        stats = {'pnl': 0.0, 'volume': 0.0, 'rank': None, 'username': None}
        
        try:
            async with self.session.get(
                f"{self.DATA_API_BASE_URL}/v1/leaderboard",
                params={"user": wallet_address, "timePeriod": "ALL"}
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if isinstance(data, list) and len(data) > 0:
                        user_data = data[0]
                        stats = {
                            'pnl': float(user_data.get('pnl', 0) or 0),
                            'volume': float(user_data.get('vol', 0) or 0),
                            'rank': user_data.get('rank'),
                            'username': user_data.get('userName')
                        }
        except Exception as e:
            print(f"Error fetching leaderboard stats for {wallet_address}: {e}")
        
        self._wallet_stats_cache[wallet_lower] = stats
        self._wallet_stats_updated[wallet_lower] = now
        return stats
    
    async def get_user_proxy_wallet(self, user_address: str) -> Optional[str]:
        """Fetch a user's proxy wallet from gamma API profile."""
        await self.ensure_session()
        
        try:
            async with self.session.get(
                f"{self.GAMMA_BASE_URL}/profiles/{user_address}",
                timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    proxy = data.get('proxyWallet', '').lower()
                    if proxy and proxy != user_address.lower():
                        return proxy
                    funder = data.get('funder', '').lower()
                    if funder and funder != user_address.lower():
                        return funder
        except Exception:
            pass
        
        try:
            async with self.session.get(
                f"{self.DATA_API_BASE_URL}/positions",
                params={"user": user_address},
                timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if isinstance(data, list) and len(data) > 0:
                        proxy = data[0].get('proxyWallet', '').lower()
                        if proxy and proxy != user_address.lower():
                            return proxy
        except Exception:
            pass
        
        return None
    
    async def get_top_traders(self, limit: int = 25, force_refresh: bool = False) -> List[Dict[str, Any]]:
        now = datetime.utcnow()
        
        if not force_refresh and self._top_traders_cache:
            if self._top_traders_updated and (now - self._top_traders_updated).total_seconds() < 600:
                return self._top_traders_cache
        
        await self.ensure_session()
        traders = []
        
        try:
            async with self.session.get(
                f"{self.DATA_API_BASE_URL}/v1/leaderboard",
                params={"timePeriod": "ALL", "limit": limit}
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if isinstance(data, list):
                        for trader in data[:limit]:
                            proxy_wallet = trader.get('proxyWallet', '').lower()
                            traders.append({
                                'address': proxy_wallet,
                                'username': trader.get('userName'),
                                'pnl': float(trader.get('pnl', 0) or 0),
                                'volume': float(trader.get('vol', 0) or 0),
                                'rank': trader.get('rank'),
                                'proxy_wallet': proxy_wallet,
                                'profile_image': trader.get('profileImage'),
                                'x_username': trader.get('xUsername'),
                                'verified': trader.get('verifiedBadge', False)
                            })
                        self._top_traders_cache = traders
                        self._top_traders_updated = now
                        print(f"Top traders cache refreshed: {len(traders)} entries")
                        for t in traders[:5]:
                            print(f"  Top #{t.get('rank', '?')}: {t['address'][:10]}... ({t.get('username', 'Unknown')}) - ${t.get('pnl', 0):,.0f} PnL")
                        
                        self._proxy_to_trader_map = {t['proxy_wallet']: t for t in traders if t['proxy_wallet']}
                        print(f"[TOP TRADERS] Loaded {len(traders)} top traders", flush=True)
        except Exception as e:
            print(f"Error fetching top traders: {e}")
        
        return traders
    
    def is_top_trader(self, wallet_address: str) -> Optional[Dict[str, Any]]:
        wallet_lower = wallet_address.lower()
        
        if wallet_lower in self._proxy_to_trader_map:
            return self._proxy_to_trader_map[wallet_lower]
        
        for trader in self._top_traders_cache:
            if trader['address'] == wallet_lower:
                return trader
            if trader.get('proxy_wallet') and trader['proxy_wallet'] == wallet_lower:
                return trader
        return None
    
    async def lookup_trader_rank(self, wallet_address: str) -> Optional[Dict[str, Any]]:
        """Look up a wallet's leaderboard info - checks if they're in top 25."""
        wallet_lower = wallet_address.lower()
        
        # Check negative cache (24 hour TTL)
        if wallet_lower in self._non_top_trader_cache:
            cache_time = self._non_top_trader_cache[wallet_lower]
            if datetime.now() - cache_time < timedelta(hours=24):
                return None  # Known non-top-25, skip API call
            else:
                del self._non_top_trader_cache[wallet_lower]
        
        await self.ensure_session()
        try:
            async with self.session.get(
                f"{self.DATA_API_BASE_URL}/v1/leaderboard",
                params={"user": wallet_address, "timePeriod": "ALL"},
                timeout=aiohttp.ClientTimeout(total=3)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if isinstance(data, list) and len(data) > 0:
                        user_data = data[0]
                        rank_raw = user_data.get('rank')
                        try:
                            rank = int(rank_raw) if rank_raw is not None else None
                        except (ValueError, TypeError):
                            rank = None
                        username = user_data.get('userName')
                        print(f"[LOOKUP] {wallet_address[:10]}... -> Rank #{rank}, {username}", flush=True)
                        if rank is not None and rank <= 25:
                            proxy_wallet = user_data.get('proxyWallet', '').lower()
                            return {
                                'address': proxy_wallet,
                                'proxy_wallet': proxy_wallet,
                                'username': username,
                                'pnl': float(user_data.get('pnl', 0) or 0),
                                'volume': float(user_data.get('vol', 0) or 0),
                                'rank': rank,
                                'verified': user_data.get('verifiedBadge', False)
                            }
                        else:
                            # Cache negative result (not top 25) for 24 hours
                            self._non_top_trader_cache[wallet_lower] = datetime.now()
        except Exception as e:
            print(f"[LOOKUP] Error for {wallet_address[:10]}...: {e}", flush=True)
        return None
    
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
            return f"https://polymarket.com/market/{clean_slug}"
        
        condition_id = trade_or_activity.get('conditionId', trade_or_activity.get('condition_id', ''))
        if condition_id:
            return f"https://polymarket.com/market/{condition_id}"
        
        return "https://polymarket.com"
    
    def get_event_slug(self, trade_or_activity: Dict[str, Any]) -> str:
        market_info = self.get_market_info(trade_or_activity)
        if market_info:
            event_slug = market_info.get('eventSlug', '')
            if event_slug:
                return event_slug
        
        slug = self.get_market_slug(trade_or_activity)
        if slug:
            return slug.split('?')[0].strip('/')
        
        return ''
    
    def get_event_slug_by_condition(self, condition_id: str, fallback_slug: str = '') -> str:
        if condition_id and condition_id in self._market_cache:
            market_info = self._market_cache[condition_id]
            event_slug = market_info.get('eventSlug', '')
            if event_slug:
                return event_slug
        
        if fallback_slug:
            return fallback_slug.split('?')[0].strip('/')
        
        return ''
    
    async def fetch_and_cache_market(self, condition_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a single market by condition ID and cache it."""
        if not condition_id:
            return None
        
        await self.ensure_session()
        try:
            async with self.session.get(
                f"{self.GAMMA_BASE_URL}/markets",
                params={"condition_ids": condition_id}
            ) as resp:
                if resp.status == 200:
                    markets = await resp.json()
                    if markets and len(markets) > 0:
                        market = markets[0]
                        # DEBUG: Log FULL market response
                        import json
                        print(f"[CACHE DEBUG FULL] {json.dumps(market)[:800]}", flush=True)
                        
                        # Try multiple possible field names for the ID
                        market_id = market.get('market_id') or market.get('marketId') or market.get('_id') or ''
                        
                        # If still no ID, check if 'id' is actually a string
                        if not market_id:
                            raw_id = market.get('id')
                            if raw_id:
                                market_id = str(raw_id)
                        
                        market_data = {
                            'slug': market.get('slug', ''),
                            'title': market.get('question', market.get('title', '')),
                            'tags': market.get('tags', []),
                            'groupSlug': market.get('groupSlug', ''),
                            'eventSlug': '',
                            'marketId': market_id,
                        }
                        # Cache by condition_id
                        self._market_cache[condition_id] = market_data
                        # Also cache by asset/token IDs
                        for token in market.get('tokens', []):
                            token_id = token.get('token_id') or token.get('tokenId', '')
                            if token_id:
                                self._market_cache[token_id] = market_data
                        print(f"[CACHE] Added market {market_id} (slug={market.get('slug', '')[:30]}) for condition {condition_id[:20]}...", flush=True)
                        return market_data
        except Exception as e:
            print(f"[CACHE] Error fetching market for {condition_id[:20]}: {e}", flush=True)
        return None

    def get_market_id(self, trade_or_activity: Dict[str, Any]) -> str:
        """Get the numeric market ID for Telegram deep links (sync version, cache only)."""
        market_info = self.get_market_info(trade_or_activity)
        if market_info:
            market_id = market_info.get('marketId', '')
            if market_id:
                return str(market_id)
        return ''

    async def get_market_id_async(self, trade_or_activity: Dict[str, Any]) -> str:
        """Get the numeric market ID for Telegram deep links."""
        # Try cache first
        market_info = self.get_market_info(trade_or_activity)
        if market_info:
            market_id = market_info.get('marketId', '')
            if market_id:
                return str(market_id)
        
        # Not in cache - fetch from API
        condition_id = trade_or_activity.get('conditionId', trade_or_activity.get('condition_id', ''))
        if condition_id:
            market_data = await self.fetch_and_cache_market(condition_id)
            if market_data:
                market_id = market_data.get('marketId', '')
                if market_id:
                    return str(market_id)
        
        return ''
    
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
    
    async def get_active_markets_prices(self, limit: int = 500, include_sports: bool = True) -> List[Dict[str, Any]]:
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
                        if not include_sports and self.is_sports_market(m):
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


    async def get_trending_markets(self, limit: int = 10, sports_only: bool = False) -> List[Dict[str, Any]]:
        await self.ensure_session()
        try:
            async with self.session.get(
                f"{self.GAMMA_BASE_URL}/markets",
                params={
                    'limit': 100,
                    'active': 'true',
                    'order': 'volume24hr',
                    'ascending': 'false'
                }
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    results = []
                    for m in data:
                        if not isinstance(m, dict):
                            continue
                        
                        volume_24h = float(m.get('volume24hr', 0) or 0)
                        if volume_24h <= 0:
                            continue
                        
                        slug = m.get('slug', '').lower()
                        tags = m.get('tags', []) or []
                        tag_slugs = {t.get('slug', '').lower() for t in tags if isinstance(t, dict)}
                        is_sports = bool(tag_slugs & self.SPORTS_SLUGS) or any(s in slug for s in self.SPORTS_SLUGS)
                        
                        if sports_only and not is_sports:
                            continue
                        if not sports_only and is_sports:
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
                        
                        results.append({
                            'question': m.get('question', 'Unknown'),
                            'slug': m.get('slug', ''),
                            'volume_24h': volume_24h,
                            'yes_price': yes_price,
                            'liquidity': float(m.get('liquidity', 0) or 0)
                        })
                        
                        if len(results) >= limit:
                            break
                    
                    return results
                return []
        except Exception as e:
            print(f"Error fetching trending markets: {e}")
            return []


    async def search_markets(self, query: str, limit: int = 30) -> List[Dict[str, Any]]:
        """Search markets by keyword with pagination to fetch all active markets."""
        await self.ensure_session()
        try:
            all_markets = []
            offset = 0
            page_size = 500
            max_pages = 10
            
            for page in range(max_pages):
                async with self.session.get(
                    f"{self.GAMMA_BASE_URL}/markets",
                    params={
                        "limit": page_size,
                        "offset": offset,
                        "active": "true",
                        "closed": "false"
                    }
                ) as resp:
                    if resp.status == 200:
                        markets = await resp.json()
                        if not markets:
                            break
                        all_markets.extend(markets)
                        offset += page_size
                        if len(markets) < page_size:
                            break
                    else:
                        print(f"Markets API returned {resp.status} at offset {offset}")
                        break
            
            print(f"Search fetched {len(all_markets)} total markets")
            
            query_lower = query.lower()
            keywords = query_lower.split()
            
            matches = []
            for m in all_markets:
                question = m.get('question', '').lower()
                slug = m.get('slug', '').lower()
                
                if all(kw in question or kw in slug for kw in keywords):
                    volume_str = m.get('volume', '0') or '0'
                    liquidity_str = m.get('liquidity', '0') or '0'
                    
                    try:
                        volume = float(volume_str)
                    except (ValueError, TypeError):
                        volume = 0.0
                    
                    try:
                        liquidity = float(liquidity_str)
                    except (ValueError, TypeError):
                        liquidity = 0.0
                    
                    outcomes = m.get('outcomes', ['Yes', 'No'])
                    outcome_prices = m.get('outcomePrices', [0.5, 0.5])
                    
                    if isinstance(outcome_prices, str):
                        try:
                            outcome_prices = [float(p) for p in outcome_prices.strip('[]').split(',')]
                        except (ValueError, IndexError):
                            outcome_prices = [0.5, 0.5]
                    
                    tokens = m.get('tokens', [])
                    token_ids = []
                    for token in tokens:
                        token_ids.append({
                            'outcome': token.get('outcome', ''),
                            'token_id': token.get('token_id', '')
                        })
                    
                    clob_token_ids_raw = m.get('clobTokenIds', [])
                    if isinstance(clob_token_ids_raw, str):
                        try:
                            import json
                            clob_token_ids = json.loads(clob_token_ids_raw)
                        except (json.JSONDecodeError, TypeError):
                            clob_token_ids = []
                    else:
                        clob_token_ids = clob_token_ids_raw or []
                    
                    if not token_ids and clob_token_ids:
                        outcomes_list = outcomes if isinstance(outcomes, list) else ['Yes', 'No']
                        for idx, tid in enumerate(clob_token_ids):
                            outcome_name = outcomes_list[idx] if idx < len(outcomes_list) else f"Outcome {idx}"
                            token_ids.append({
                                'outcome': outcome_name,
                                'token_id': tid
                            })
                    
                    events = m.get('events', [])
                    event_slug = events[0].get('slug', '') if events else m.get('slug', '')
                    
                    matches.append({
                        'question': m.get('question', 'Unknown'),
                        'slug': m.get('slug', ''),
                        'event_slug': event_slug,
                        'condition_id': m.get('conditionId', ''),
                        'volume': volume,
                        'liquidity': liquidity,
                        'outcomes': outcomes if isinstance(outcomes, list) else ['Yes', 'No'],
                        'outcome_prices': outcome_prices,
                        'token_ids': token_ids
                    })
            
            print(f"Search found {len(matches)} matches for '{query}'")
            matches.sort(key=lambda x: x['volume'], reverse=True)
            return matches[:limit]
        except Exception as e:
            print(f"Error searching markets: {e}")
            return []
    
    async def get_orderbook(self, token_id: str) -> Dict[str, Any]:
        """Fetch orderbook from Polymarket CLOB API."""
        await self.ensure_session()
        try:
            async with self.session.get(
                "https://clob.polymarket.com/book",
                params={"token_id": token_id}
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    
                    bids = []
                    asks = []
                    
                    for bid in data.get('bids', []):
                        try:
                            price = float(bid.get('price', 0))
                            size = float(bid.get('size', 0))
                            bids.append({'price': price, 'size': size})
                        except (ValueError, TypeError):
                            continue
                    
                    for ask in data.get('asks', []):
                        try:
                            price = float(ask.get('price', 0))
                            size = float(ask.get('size', 0))
                            asks.append({'price': price, 'size': size})
                        except (ValueError, TypeError):
                            continue
                    
                    bids.sort(key=lambda x: x['price'], reverse=True)
                    asks.sort(key=lambda x: x['price'])
                    
                    best_bid = bids[0]['price'] if bids else 0
                    best_ask = asks[0]['price'] if asks else 1
                    mid = (best_bid + best_ask) / 2 if bids and asks else 0.5
                    spread = (best_ask - best_bid) if bids and asks else 0
                    
                    total_bid_size = sum(b['size'] for b in bids)
                    total_ask_size = sum(a['size'] for a in asks)
                    
                    running_total = 0
                    for bid in bids:
                        running_total += bid['size']
                        bid['total'] = running_total
                    
                    running_total = 0
                    for ask in asks:
                        running_total += ask['size']
                        ask['total'] = running_total
                    
                    return {
                        'bids': bids[:10],
                        'asks': asks[:10],
                        'mid': mid,
                        'spread': spread,
                        'total_bid_size': total_bid_size,
                        'total_ask_size': total_ask_size
                    }
                else:
                    print(f"CLOB API returned {resp.status}")
                    return {'bids': [], 'asks': [], 'mid': 0.5, 'spread': 0, 'total_bid_size': 0, 'total_ask_size': 0}
        except Exception as e:
            print(f"Error fetching orderbook: {e}")
            return {'bids': [], 'asks': [], 'mid': 0.5, 'spread': 0, 'total_bid_size': 0, 'total_ask_size': 0}


class PolymarketWebSocket:
    """
    Production-ready WebSocket client for Polymarket RTDS feed.
    
    KEY CHANGE: Does NOT rely on ping/pong for health monitoring.
    Instead uses data activity timeout which works reliably across all platforms.
    
    RECONNECTION FIX: Uses a flag-based approach to handle reconnection properly
    without breaking out of the main loop prematurely.
    """
    
    RTDS_URL = "wss://ws-live-data.polymarket.com"
    
    DATA_TIMEOUT = 120
    MAX_CONNECTION_AGE = 900
    
    INITIAL_RECONNECT_DELAY = 2
    MAX_RECONNECT_DELAY = 60
    RECONNECT_BACKOFF_FACTOR = 1.5
    
    DEBUG_MODE = False
    DEBUG_LOG_FIRST_N = 20
    
    def __init__(self, on_trade_callback: Optional[Callable] = None, on_reconnect_callback: Optional[Callable] = None):
        self.on_trade_callback = on_trade_callback
        self.on_reconnect_callback = on_reconnect_callback
        self._running = False
        self._reconnect_delay = self.INITIAL_RECONNECT_DELAY
        
        self._primary_ws = None
        self._backup_ws = None
        
        self._last_data_time = time.time()
        self._connection_start_time = time.time()
        self._total_trades = 0
        
        self._connection_switched = False
        self._consecutive_failures = 0
        self._max_consecutive_failures = 10
        
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
    
    async def _create_connection(self, name: str, timeout: float = 30.0):
        """
        Create and subscribe a new WebSocket connection.
        Returns the websocket if successful, None otherwise.
        """
        try:
            print(f"[WS {name.upper()}] Attempting connection...", flush=True)
            
            ws = await asyncio.wait_for(
                websockets.connect(
                    self.RTDS_URL,
                    ping_interval=None,
                    ping_timeout=None,
                    close_timeout=10
                ),
                timeout=timeout
            )
            
            subscription = {
                "action": "subscribe",
                "subscriptions": [{"topic": "activity", "type": "trades"}]
            }
            await ws.send(json.dumps(subscription))
            
            try:
                first_msg = await asyncio.wait_for(ws.recv(), timeout=10)
                print(f"[WS {name.upper()}] ✓ Connected and verified (received data)", flush=True)
                self._consecutive_failures = 0
                return ws
            except asyncio.TimeoutError:
                print(f"[WS {name.upper()}] ⚠ Connected but no initial data (might be slow)", flush=True)
                self._consecutive_failures = 0
                return ws
                
        except asyncio.TimeoutError:
            print(f"[WS {name.upper()}] ✗ Connection timeout after {timeout}s", flush=True)
            self._consecutive_failures += 1
            return None
        except Exception as e:
            print(f"[WS {name.upper()}] ✗ Connection failed: {type(e).__name__}: {e}", flush=True)
            self._consecutive_failures += 1
            return None
    
    async def _maintain_backup(self):
        """Maintain a backup connection ready to take over."""
        while self._running:
            try:
                await asyncio.sleep(30)
                
                if not self._is_ws_open(self._backup_ws):
                    self._backup_ws = await self._create_connection("backup")
                    if self._backup_ws:
                        print("[WS BACKUP] ✓ Backup connection ready", flush=True)
                        
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
                await asyncio.sleep(10)
                
                now = time.time()
                data_age = now - self._last_data_time
                connection_age = now - self._connection_start_time
                
                if data_age > self.DATA_TIMEOUT:
                    print(f"[WS MONITOR] No data for {data_age:.0f}s (>{self.DATA_TIMEOUT}s) - triggering reconnect", flush=True)
                    await self._switch_to_backup()
                
                elif connection_age > self.MAX_CONNECTION_AGE:
                    print(f"[WS MONITOR] Connection age {connection_age:.0f}s > {self.MAX_CONNECTION_AGE}s - proactive reconnect", flush=True)
                    await self._switch_to_backup()
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[WS MONITOR] Error: {e}", flush=True)
    
    async def _switch_to_backup(self):
        """
        Switch from primary to backup connection.
        Sets _connection_switched flag so main loop knows to use new connection.
        """
        try:
            old_ws = self._primary_ws
            
            if self._is_ws_open(self._backup_ws):
                self._primary_ws = self._backup_ws
                self._backup_ws = None
                self._connection_start_time = time.time()
                self._last_data_time = time.time()
                self._connection_switched = True
                
                print(f"[WS SWITCH] ✓ Switched to backup connection", flush=True)
                
            else:
                print(f"[WS SWITCH] No backup available - creating new primary...", flush=True)
                
                if old_ws:
                    try:
                        await old_ws.close()
                    except:
                        pass
                    old_ws = None
                
                retry_delay = self.INITIAL_RECONNECT_DELAY
                for attempt in range(5):
                    new_ws = await self._create_connection("primary")
                    if new_ws and self._is_ws_open(new_ws):
                        self._primary_ws = new_ws
                        self._connection_start_time = time.time()
                        self._last_data_time = time.time()
                        self._connection_switched = True
                        self._reconnect_delay = self.INITIAL_RECONNECT_DELAY
                        print(f"[WS SWITCH] ✓ New primary connection established (attempt {attempt + 1})", flush=True)
                        break
                    else:
                        if attempt < 4:
                            print(f"[WS SWITCH] Retry {attempt + 1}/5 failed, waiting {retry_delay:.1f}s...", flush=True)
                            await asyncio.sleep(retry_delay)
                            retry_delay = min(retry_delay * self.RECONNECT_BACKOFF_FACTOR, self.MAX_RECONNECT_DELAY)
                        else:
                            print(f"[WS SWITCH] ✗ All 5 reconnection attempts failed", flush=True)
                            self._primary_ws = None
            
            if old_ws and old_ws != self._primary_ws:
                try:
                    await old_ws.close()
                except:
                    pass
            
            if self._connection_switched and self.on_reconnect_callback:
                try:
                    self.on_reconnect_callback()
                except Exception as e:
                    print(f"[WS] Reconnect callback error: {e}", flush=True)
                    
        except Exception as e:
            print(f"[WS SWITCH] Error during switch: {type(e).__name__}: {e}", flush=True)
    
    async def connect(self):
        """
        Main connection loop with backup WebSocket support.
        
        KEY FIX: Uses _connection_switched flag to handle reconnection
        without breaking out of the inner loop prematurely.
        """
        self._running = True
        reconnect_delay = self.INITIAL_RECONNECT_DELAY
        
        while self._running:
            try:
                print("[WebSocket] Connecting to Polymarket RTDS...", flush=True)
                
                self._primary_ws = await self._create_connection("primary")
                if not self._primary_ws:
                    print(f"[WebSocket] Initial connection failed, retrying in {reconnect_delay}s...", flush=True)
                    await asyncio.sleep(reconnect_delay)
                    reconnect_delay = min(reconnect_delay * self.RECONNECT_BACKOFF_FACTOR, self.MAX_RECONNECT_DELAY)
                    continue
                
                self._connection_start_time = time.time()
                self._last_data_time = time.time()
                self._connection_switched = False
                reconnect_delay = self.INITIAL_RECONNECT_DELAY
                
                self._first_message_logged = False
                self._debug_msg_count = 0
                self._debug_trade_count = 0
                self._debug_non_trade_count = 0
                self._debug_empty_count = 0
                self._debug_error_count = 0
                self._debug_topics_seen = set()
                self._debug_types_seen = set()
                
                self._backup_task = asyncio.create_task(self._maintain_backup())
                self._monitor_task = asyncio.create_task(self._monitor_health())
                
                print("[WebSocket] Connected - NO PING mode (data activity timeout only)", flush=True)
                
                while self._running:
                    try:
                        if self._connection_switched:
                            self._connection_switched = False
                            self._first_message_logged = False
                            print("[WS] Connection was switched, continuing with new connection...", flush=True)
                        
                        ws = self._primary_ws
                        if not self._is_ws_open(ws):
                            print("[WS] Primary connection not open, attempting recovery...", flush=True)
                            await self._switch_to_backup()
                            if not self._is_ws_open(self._primary_ws):
                                print("[WS] Recovery failed, breaking to outer loop...", flush=True)
                                break
                            continue
                        
                        message = await asyncio.wait_for(ws.recv(), timeout=30)
                        self._last_data_time = time.time()
                        
                        if not self._first_message_logged:
                            print(f"[WebSocket] ✓ Receiving messages...", flush=True)
                            self._first_message_logged = True
                        
                        await self._handle_message(message)
                        await asyncio.sleep(0)
                        
                    except asyncio.TimeoutError:
                        continue
                    except websockets.exceptions.ConnectionClosed as e:
                        print(f"[WS] Connection closed ({e.code if hasattr(e, 'code') else 'unknown'}), attempting recovery...", flush=True)
                        await self._switch_to_backup()
                        if not self._is_ws_open(self._primary_ws):
                            break
                        
            except Exception as e:
                print(f"[WebSocket] Error: {e}. Reconnecting in {reconnect_delay}s...", flush=True)
            finally:
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
                reconnect_delay = min(reconnect_delay * self.RECONNECT_BACKOFF_FACTOR, self.MAX_RECONNECT_DELAY)
    
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


class PolymarketPriceWebSocket:
    """
    WebSocket client for real-time price updates from Polymarket CLOB.
    Subscribes to the market channel to receive price_change events with best_bid/best_ask.
    """
    
    CLOB_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    
    def __init__(self, on_price_callback: Optional[Callable] = None):
        self.on_price_callback = on_price_callback
        self._ws = None
        self._running = False
        self._subscribed_assets: set = set()
        self._asset_metadata: Dict[str, dict] = {}
        self._reconnect_delay = 5
        self._max_reconnect_delay = 60
        self._last_ping_time = 0
        self._ping_interval = 30
    
    async def subscribe_to_markets(self, markets: List[Dict[str, Any]]):
        """
        Subscribe to price updates for a list of markets.
        Handles both API formats: clobTokenIds (array of strings) or tokens (array of objects)
        """
        asset_ids = []
        
        for market in markets:
            title = market.get('question', market.get('title', 'Unknown'))
            slug = market.get('slug', '')
            
            clob_token_ids_raw = market.get('clobTokenIds', [])
            if isinstance(clob_token_ids_raw, str):
                try:
                    clob_token_ids = json.loads(clob_token_ids_raw)
                except:
                    clob_token_ids = []
            else:
                clob_token_ids = clob_token_ids_raw if isinstance(clob_token_ids_raw, list) else []
            
            if clob_token_ids and len(clob_token_ids) > 0:
                yes_token_id = clob_token_ids[0]
                if yes_token_id:
                    asset_ids.append(yes_token_id)
                    self._asset_metadata[yes_token_id] = {
                        'title': title,
                        'slug': slug,
                        'outcome': 'Yes',
                        'outcome_index': 0
                    }
            else:
                tokens = market.get('tokens', [])
                for token in tokens:
                    token_id = token.get('token_id', '')
                    outcome = token.get('outcome', 'Yes')
                    outcome_index = 0 if outcome == 'Yes' else 1
                    
                    if outcome_index == 0 and token_id:
                        asset_ids.append(token_id)
                        self._asset_metadata[token_id] = {
                            'title': title,
                            'slug': slug,
                            'outcome': outcome,
                            'outcome_index': outcome_index
                        }
        
        self._subscribed_assets = set(asset_ids)
        print(f"[PriceWS] Prepared {len(asset_ids)} assets for subscription", flush=True)
        
        if self._ws and self._running:
            await self._send_subscription()
    
    async def _send_subscription(self):
        """Send subscription message for all tracked assets."""
        if not self._ws or not self._subscribed_assets:
            return
        
        subscription = {
            "assets_ids": list(self._subscribed_assets),
            "type": "market"
        }
        
        try:
            await self._ws.send(json.dumps(subscription))
            print(f"[PriceWS] Subscribed to {len(self._subscribed_assets)} assets", flush=True)
        except Exception as e:
            print(f"[PriceWS] Subscription error: {e}", flush=True)
    
    async def connect(self):
        """Main connection loop with automatic reconnection."""
        self._running = True
        reconnect_delay = self._reconnect_delay
        
        while self._running:
            try:
                print(f"[PriceWS] Connecting to {self.CLOB_WS_URL}...", flush=True)
                
                async with websockets.connect(
                    self.CLOB_WS_URL,
                    ping_interval=30,
                    ping_timeout=10,
                    close_timeout=5
                ) as ws:
                    self._ws = ws
                    reconnect_delay = self._reconnect_delay
                    print("[PriceWS] Connected!", flush=True)
                    
                    await self._send_subscription()
                    
                    ping_task = asyncio.create_task(self._ping_loop())
                    
                    try:
                        async for message in ws:
                            await self._handle_message(message)
                    finally:
                        ping_task.cancel()
                        try:
                            await ping_task
                        except asyncio.CancelledError:
                            pass
                        
            except websockets.exceptions.ConnectionClosed as e:
                print(f"[PriceWS] Connection closed: {e}. Reconnecting in {reconnect_delay}s...", flush=True)
            except Exception as e:
                print(f"[PriceWS] Error: {e}. Reconnecting in {reconnect_delay}s...", flush=True)
            
            self._ws = None
            
            if self._running:
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 1.5, self._max_reconnect_delay)
    
    async def _ping_loop(self):
        """Send periodic pings to keep connection alive."""
        while self._running and self._ws:
            try:
                await asyncio.sleep(self._ping_interval)
                if self._ws:
                    pong = await self._ws.ping()
                    await asyncio.wait_for(pong, timeout=10)
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[PriceWS] Ping failed: {e}", flush=True)
                break
    
    async def _handle_message(self, raw_message: str):
        """Process incoming WebSocket messages."""
        try:
            data = json.loads(raw_message)
            
            if isinstance(data, list):
                for item in data:
                    await self._handle_book(item)
            elif isinstance(data, dict):
                event_type = data.get('event_type', '')
                
                if event_type == 'price_change':
                    await self._handle_price_change(data)
                elif event_type == 'book' or 'bids' in data or 'asks' in data:
                    await self._handle_book(data)
                
        except json.JSONDecodeError:
            pass
        except Exception as e:
            print(f"[PriceWS] Message handling error: {e}", flush=True)
    
    async def _handle_price_change(self, data: dict):
        """Handle price_change events - validate spread before recording."""
        price_changes = data.get('price_changes', [])
        timestamp = data.get('timestamp', '')
        
        for change in price_changes:
            asset_id = change.get('asset_id', '')
            best_bid = change.get('best_bid', '0')
            best_ask = change.get('best_ask', '0')
            
            if not asset_id:
                continue
            
            try:
                bid = float(best_bid) if best_bid else 0
                ask = float(best_ask) if best_ask else 0
                
                if bid <= 0 or ask <= 0:
                    continue
                
                if ask <= bid:
                    continue
                
                spread = ask - bid
                if spread > 0.10:
                    continue
                
                midpoint = (bid + ask) / 2
                
                if midpoint <= 0.01 or midpoint >= 0.99:
                    continue
                
                metadata = self._asset_metadata.get(asset_id, {})
                
                title = metadata.get('title', '')
                slug = metadata.get('slug', '')
                
                if not title or title == 'Unknown':
                    main_cache = polymarket_client._market_cache.get(asset_id, {})
                    if main_cache:
                        title = main_cache.get('title', title)
                        slug = main_cache.get('slug', slug)
                
                if self.on_price_callback:
                    await self.on_price_callback({
                        'asset_id': asset_id,
                        'price': midpoint,
                        'best_bid': bid,
                        'best_ask': ask,
                        'spread': spread,
                        'title': title or 'Unknown',
                        'slug': slug,
                        'timestamp': timestamp
                    })
                    
            except (ValueError, TypeError):
                continue
    
    async def _handle_book(self, data: dict):
        """Handle full book updates - validate spread before recording."""
        asset_id = data.get('asset_id', '')
        bids = data.get('bids', [])
        asks = data.get('asks', [])
        
        if not asset_id:
            return
        
        try:
            if not bids or not asks:
                return
                
            best_bid = float(bids[0].get('price', 0))
            best_ask = float(asks[0].get('price', 0))
            
            if best_bid <= 0 or best_ask <= 0:
                return
            
            if best_ask <= best_bid:
                return
            
            spread = best_ask - best_bid
            if spread > 0.10:
                return
            
            midpoint = (best_bid + best_ask) / 2
            
            if midpoint <= 0.01 or midpoint >= 0.99:
                return
            
            metadata = self._asset_metadata.get(asset_id, {})
            
            title = metadata.get('title', '')
            slug = metadata.get('slug', '')
            
            if not title or title == 'Unknown':
                main_cache = polymarket_client._market_cache.get(asset_id, {})
                if main_cache:
                    title = main_cache.get('title', title)
                    slug = main_cache.get('slug', slug)
            
            if self.on_price_callback:
                await self.on_price_callback({
                    'asset_id': asset_id,
                    'price': midpoint,
                    'best_bid': best_bid,
                    'best_ask': best_ask,
                    'spread': spread,
                    'title': title or 'Unknown',
                    'slug': slug,
                    'timestamp': data.get('timestamp', '')
                })
                
        except (ValueError, TypeError, IndexError):
            pass
    
    async def disconnect(self):
        """Disconnect from the WebSocket."""
        self._running = False
        if self._ws:
            try:
                await self._ws.close()
            except:
                pass
        self._ws = None
        print("[PriceWS] Disconnected", flush=True)
    
    def is_connected(self) -> bool:
        return self._ws is not None and self._running


polymarket_client = PolymarketClient()
