# Polymarket Discord Bot - Product Requirements Document (PRD)

## 1. Product Overview

### 1.1 Purpose
A Discord bot that monitors Polymarket prediction market activity and delivers real-time alerts to Discord channels. The bot helps traders identify significant market movements, whale activity, new market participants, and volatility events.

### 1.2 Target Users
- Polymarket traders wanting real-time trade alerts
- Discord server owners running trading communities
- Users tracking specific wallets or market movements

---

## 2. Alert Types & Routing Logic

### 2.1 Alert Channel Hierarchy

Each alert type has a dedicated channel. **Alerts should ONLY go to their designated channel - no cross-posting.**

| Alert Type | Channel | Threshold | Description |
|------------|---------|-----------|-------------|
| Whale Alerts | `#whale-tracker` | $10,000+ | Large trades on non-sports, non-bond markets |
| Fresh Wallet Alerts | `#fresh-wallets` | $1,000+ | First-ever trade from a Polymarket account |
| Sports Alerts | `#sports` | $3,000+ | Any trade on sports/esports markets |
| Volatility Alerts | `#volatility` | 5+ points | Markets with significant price swings |
| Bonds Alerts | `#bonds` | $5,000+ | Trades on high-certainty markets (≥95% price) |
| Top Trader Alerts | `#top-traders` | $3,000+ | Trades from top 25 all-time PnL leaders |
| Tracked Wallet Alerts | `#whale-tracker` | Any | Custom tracked wallets (user-configured) |

### 2.2 Alert Routing Rules (CRITICAL)

```
FOR EACH TRADE:

1. IF wallet is in tracked wallets list:
   → Send to tracked wallet channel
   → CONTINUE (don't skip other checks)

2. IF market is SPORTS:
   → IF trader is top 25 AND value >= top_trader_threshold:
      → Send to top-traders channel ONLY
      → SKIP to next trade
   → ELSE IF value >= sports_threshold:
      → Send to sports channel ONLY
      → SKIP to next trade
   → ELSE:
      → No alert

3. IF market is NOT SPORTS:
   → IF trader is top 25 AND value >= top_trader_threshold:
      → Send to top-traders channel ONLY
      → SKIP to next trade
   → IF price >= 0.95 (bond) AND value >= $5,000:
      → Send to bonds channel ONLY
      → SKIP to next trade
   → IF wallet is FRESH AND value >= fresh_wallet_threshold:
      → Send to fresh-wallets channel ONLY
      → SKIP to next trade
   → IF value >= whale_threshold:
      → Send to whale channel ONLY
      → SKIP to next trade
```

**Key Rules:**
- Sports trades NEVER go to whale channel
- Bond trades NEVER go to whale channel
- Fresh wallet trades NEVER go to whale channel
- Each trade triggers AT MOST one alert type (except tracked wallets)
- Top trader status takes priority over all other alert types

---

## 3. Fresh Wallet Detection

### 3.1 Definition
A "fresh wallet" is a Polymarket account making its **first-ever trade**. This is valuable alpha because it may indicate insider activity or new money entering a market.

### 3.2 Detection Logic

```python
async def is_fresh_wallet(wallet_address, current_trade_timestamp):
    """
    Returns True if this is the wallet's first-ever trade.
    Returns False if wallet has any prior trading history.
    """

    # 1. Fetch recent trades for this wallet from Polymarket API
    trades = await fetch_trades(wallet_address, limit=10)

    # 2. Filter out the current trade (by timestamp)
    prior_trades = [t for t in trades if t.timestamp < current_trade_timestamp]

    # 3. If NO prior trades exist, this is a fresh wallet
    return len(prior_trades) == 0
```

### 3.3 Important Considerations
- Must pass the current trade's timestamp to avoid counting it
- Use limit=10 (not limit=2) for accurate results
- Cache results for 5 minutes max (fresh status can change)
- If API times out, assume NOT fresh (to avoid false positives)

### 3.4 Database Tracking
The `wallet_activity` table tracks wallets we've seen:
- First time seeing a wallet → check API for fresh status
- Subsequent trades → skip API check (wallet is known)

**Note:** This is for optimization only. Fresh determination MUST come from the API, not from whether the wallet exists in our database.

---

## 4. Sports Market Detection

### 4.1 Definition
Sports markets include: NBA, NFL, MLB, NHL, UFC, MMA, Boxing, Soccer, Tennis, Golf, F1, Esports, College Sports, and all related events.

### 4.2 Detection Priority

1. **Official Tags (Most Reliable)**
   - Check market's `groupSlug` against known sports slugs
   - Check market's `tags` array for sports tag IDs
   - Use tag IDs from Polymarket's `/sports` endpoint

2. **Keyword Matching (Fallback)**
   - Check market title/slug for sports team names
   - Check for athlete names
   - Check for league names (NBA, NFL, etc.)
   - Check patterns like "X vs Y", "will X win on DATE"

3. **Exclusion Rules**
   - If market has finance/economy/crypto/geopolitics tags → NOT sports
   - Avoid false positives on words like "trade" (NBA trade vs stock trade)

### 4.3 Sports Keywords List
Includes: All major league names, team names (full names to avoid conflicts), athlete names, tournament names, and sports-specific patterns.

---

## 5. Top Trader Detection

### 5.1 Definition
Top traders are the top 25 wallets by all-time profit on Polymarket's leaderboard.

### 5.2 Detection Logic

```python
def is_top_trader(wallet_address):
    """Check if wallet is in top 25 all-time PnL."""

    # 1. Check cached top traders list (refreshed every 10 min)
    if wallet in top_traders_cache:
        return top_traders_cache[wallet]

    # 2. For trades >= $5k, do on-demand lookup
    trader_info = await lookup_leaderboard(wallet)
    if trader_info and trader_info.rank <= 25:
        return trader_info

    # 3. Cache negative results for 24 hours
    return None
```

### 5.3 Alert Behavior
- Top trader alerts take PRIORITY over whale/fresh/sports alerts
- Include trader's username, rank, and PnL in the alert
- Threshold: $3,000+ (configurable per server)

---

## 6. Volatility Detection

### 6.1 Definition
Volatility alerts trigger when a market's price moves significantly within a time window, confirmed by trading volume.

### 6.2 Detection Parameters

| Parameter | Value |
|-----------|-------|
| Price Change Threshold | 5+ absolute percentage points (e.g., 50% → 55%) |
| Time Windows | 5 minutes, 15 minutes, 60 minutes |
| Minimum Volume | $2,000 USD |
| Minimum Trades | 3 trades in window |
| Relative Volume | 1.3x average (volume spike confirmation) |
| Cooldown | 15 minutes per market per timeframe |
| Warmup Period | 5 minutes after bot restart |

### 6.3 VWAP Calculation
Uses Volume-Weighted Average Price for accurate price tracking:
```
VWAP = Σ(Price × Volume) / Σ(Volume)
```

### 6.4 Alert Urgency Formatting
- 🚨 **RAPID** (5min window) - Very fast price movement
- ⚡ **Fast** (15min window) - Quick price movement  
- 📊 **Swing** (60min window) - Sustained price movement

### 6.5 Category Blacklist
Users can exclude categories from volatility alerts:
- Politics, Sports, Crypto, Finance, Geopolitics
- Earnings, Tech, Culture, World, Economy
- Climate & Science, Elections, Mentions

---

## 7. Bond Detection

### 7.1 Definition
"Bonds" are markets trading at very high certainty (≥95% price). These represent near-guaranteed outcomes where traders are locking in small gains.

### 7.2 Detection Logic
```python
is_bond = trade.price >= 0.95
```

### 7.3 Alert Behavior
- Bonds ONLY go to bonds channel
- Threshold: $5,000+ (fixed)
- Excludes from whale channel to reduce noise

---

## 8. Database Schema

### 8.1 Tables

**server_configs**
- `guild_id` (PK) - Discord server ID
- `whale_channel_id` - Channel for whale alerts
- `fresh_wallet_channel_id` - Channel for fresh wallet alerts
- `sports_channel_id` - Channel for sports alerts
- `volatility_channel_id` - Channel for volatility alerts
- `bonds_channel_id` - Channel for bond alerts
- `top_trader_channel_id` - Channel for top trader alerts
- `tracked_wallet_channel_id` - Channel for tracked wallet alerts
- `whale_threshold` - Default: 10000.0
- `fresh_wallet_threshold` - Default: 1000.0
- `sports_threshold` - Default: 5000.0
- `volatility_threshold` - Default: 5.0
- `top_trader_threshold` - Default: 2500.0
- `volatility_blacklist` - Comma-separated category list
- `is_paused` - Boolean to pause all alerts

**tracked_wallets**
- `id` (PK)
- `guild_id` (FK)
- `wallet_address`
- `label` - User-friendly name
- `added_by` - Discord user ID
- `added_at` - Timestamp

**wallet_activity**
- `wallet_address` (PK)
- `first_seen` - Timestamp
- `transaction_count` - Number of trades seen

**seen_transactions**
- `tx_hash` (PK) - Unique trade identifier
- `seen_at` - Timestamp

**volatility_alerts**
- `id` (PK)
- `condition_id` - Market ID
- `alerted_at` - Timestamp
- `price_change` - Percentage change

---

## 9. Discord Commands

| Command | Description | Permissions |
|---------|-------------|-------------|
| `/setup` | Configure all channels at once | Admin |
| `/whale_channel` | Set whale alert channel | Admin |
| `/fresh_wallet_channel` | Set fresh wallet channel | Admin |
| `/sports` | Set sports alert channel | Admin |
| `/volatility` | Set volatility alert channel | Admin |
| `/bonds` | Set bonds alert channel | Admin |
| `/top_trader_channel` | Set top trader channel | Admin |
| `/tracked_wallet_channel` | Set tracked wallet channel | Admin |
| `/threshold` | Set whale threshold | Admin |
| `/fresh_wallet_threshold` | Set fresh wallet threshold | Admin |
| `/sports_threshold` | Set sports threshold | Admin |
| `/volatility_threshold` | Set volatility threshold | Admin |
| `/top_trader_threshold` | Set top trader threshold | Admin |
| `/volatility_blacklist` | Exclude categories from volatility | Admin |
| `/track <wallet> [label]` | Track a wallet | Admin |
| `/untrack` | Stop tracking a wallet | Admin |
| `/rename <wallet> <name>` | Rename tracked wallet | Admin |
| `/list` | Show current settings | Anyone |
| `/positions` | View tracked wallet positions | Anyone |
| `/trending` | Show trending markets | Anyone |
| `/sports_trending` | Show trending sports markets | Anyone |
| `/search <query>` | Search markets | Anyone |
| `/pause` | Pause all alerts | Admin |
| `/resume` | Resume alerts | Admin |
| `/help` | Show commands | Anyone |

---

## 10. Technical Architecture

### 10.1 Components

```
┌─────────────────────────────────────────────────────────────┐
│                     Discord Bot (bot.py)                     │
├─────────────────────────────────────────────────────────────┤
│  • Slash command handlers                                    │
│  • WebSocket trade processor                                 │
│  • Alert routing logic                                       │
│  • Volatility tracker (in-memory)                           │
└─────────────────────────┬───────────────────────────────────┘
                          │
        ┌─────────────────┼─────────────────┐
        ▼                 ▼                 ▼
┌───────────────┐ ┌───────────────┐ ┌───────────────┐
│ polymarket_   │ │  database.py  │ │   alerts.py   │
│ client.py     │ │               │ │               │
├───────────────┤ ├───────────────┤ ├───────────────┤
│ • API calls   │ │ • SQLAlchemy  │ │ • Embed       │
│ • WebSocket   │ │ • Models      │ │   formatting  │
│ • Caching     │ │ • Sessions    │ │ • Button      │
│ • Sports      │ │               │ │   views       │
│   detection   │ │               │ │               │
└───────────────┘ └───────────────┘ └───────────────┘
```

### 10.2 Data Flow

```
Polymarket WebSocket
        │
        ▼
   Trade received
        │
        ▼
   Filter: BUY only, >= $1k (or tracked)
        │
        ▼
   Deduplicate (seen_transactions table)
        │
        ▼
   Enrich: Get market info, wallet stats
        │
        ▼
   Classify: is_sports? is_bond? is_fresh? is_top_trader?
        │
        ▼
   Route to correct channel(s)
        │
        ▼
   Send Discord embed with buttons
```

### 10.3 Caching Strategy

| Cache | TTL | Purpose |
|-------|-----|---------|
| Server configs | 5 min | Reduce DB queries |
| Tracked wallets | 5 min | Fast lookup on every trade |
| Market cache | 5 min | Market titles, slugs, tags |
| Wallet history | 5 min | Fresh wallet determination |
| Wallet PnL stats | 10 min | Trader statistics |
| Top traders list | 10 min | Top 25 leaderboard |
| Negative top trader | 24 hr | Skip API for known non-top-25 |

### 10.4 Performance Requirements

- Process 1000+ trades per minute
- Alert latency < 2 seconds from trade to Discord
- WebSocket reconnection < 5 seconds
- API timeout: 2-3 seconds max

---

## 11. Current Issues & Fixes Needed

### 11.1 Issue: False Fresh Wallet Alerts
**Problem:** Wallets with trading history are being marked as "fresh"
**Root Cause:** 
- Only fetching 2 trades (should be 10)
- Not filtering out current trade by timestamp
- Cache TTL too long (1 hour → should be 5 min)

**Fix:** Update `has_prior_activity()` in polymarket_client.py

### 11.2 Issue: Sports Trades in Whale Channel
**Problem:** Sports market trades appearing in whale channel
**Root Cause:** `is_sports_market()` returning False incorrectly
**Debug:** Add logging to see why sports detection fails
**Fix:** Ensure market cache is populated before checking

### 11.3 Issue: Multiple Bot Replicas
**Problem:** Railway running 2+ replicas causing duplicate alerts
**Fix:** Scale to exactly 1 replica in Railway dashboard

### 11.4 Issue: Wrong Threshold Display
**Problem:** Fresh wallet threshold showing $100 instead of $1000
**Fix:** Run `/fresh_wallet_threshold 1000` in Discord

---

## 12. Deployment

### 12.1 Environment Variables

```
DISCORD_BOT_TOKEN=xxx        # Production bot token
DEV_DISCORD_BOT_TOKEN=xxx    # Development bot token
DATABASE_URL=postgres://xxx  # PostgreSQL connection string
PORT=8080                    # Health check port (Railway)
REPLIT_DEPLOYMENT=1          # Set to 1 for production
```

### 12.2 Railway Configuration
- **Replicas:** 1 (CRITICAL - never more than 1)
- **Health check:** GET / or /health returns 200
- **Region:** Closest to users

### 12.3 Development vs Production
- Dev uses `DEV_DISCORD_BOT_TOKEN`
- Prod uses `DISCORD_BOT_TOKEN`
- Check `REPLIT_DEPLOYMENT` env var

---

## 13. Testing Checklist

### 13.1 Fresh Wallet Detection
- [ ] New wallet (0 prior trades) → triggers fresh alert
- [ ] Wallet with 1+ prior trades → NO fresh alert
- [ ] Same wallet's second trade → NO fresh alert

### 13.2 Sports Routing
- [ ] NBA game trade → sports channel ONLY
- [ ] NFL trade → sports channel ONLY
- [ ] Non-sports $15k trade → whale channel ONLY
- [ ] Sports $15k trade → sports channel (NOT whale)

### 13.3 Bond Routing
- [ ] 96% price, $6k trade → bonds channel ONLY
- [ ] 96% price, $15k trade → bonds channel (NOT whale)
- [ ] 50% price, $15k trade → whale channel

### 13.4 Top Trader Routing
- [ ] Top 25 trader, $5k trade → top-traders channel ONLY
- [ ] Top 25 trader, $5k sports trade → top-traders (NOT sports)

### 13.5 No Duplicates
- [ ] Each trade produces exactly ONE alert (except tracked)
- [ ] Same trade doesn't alert twice (deduplication working)

---

## 14. Glossary

| Term | Definition |
|------|------------|
| Fresh Wallet | Account making its first-ever Polymarket trade |
| Whale | Large trade ($10k+ by default) |
| Bond | Trade on high-certainty market (≥95% price) |
| VWAP | Volume-Weighted Average Price |
| Proxy Wallet | Smart contract wallet used by Polymarket accounts |
| Top Trader | Wallet in top 25 all-time profit leaderboard |
| Condition ID | Unique identifier for a Polymarket market |
| Asset ID | Token identifier for a market outcome |
