# Polymarket Discord Bot

## Overview
A Discord bot that monitors Polymarket activity and sends real-time alerts to configured Discord channels. Supports multiple Discord servers with individual configurations.

## Features
- **Whale Alerts**: Notifications for large transactions ($10k+ by default)
- **Fresh Wallet Alerts**: Detect new wallets making their first trades
- **Custom Wallet Tracking**: Monitor specific wallet addresses (any trade amount)
- **Volatility Alerts**: Track markets with 20%+ price swings within 1 hour
- **Top Trader Alerts**: Monitor trades from top 25 all-time profit leaders
- **Sports Channel**: Separate channel for sports/esports market alerts
- **Sell Filtering**: Excludes sells above 99% (position closures)
- **Position Viewing**: View current holdings of tracked wallets with drill-down
- **Market Links**: Clickable links to Polymarket + Trade via Onsight buttons
- **Per-Server Configuration**: Each Discord server has its own settings
- **Slash Commands**: Simple Discord interface for configuration

## Project Architecture

```
/
├── bot.py                 # Main Discord bot with slash commands and monitoring loop
├── database.py            # SQLAlchemy models and database setup
├── polymarket_client.py   # Async Polymarket API client
├── alerts.py              # Discord embed formatters for alerts
├── pyproject.toml         # Python dependencies
└── replit.md              # This file
```

## Database Schema

- **server_configs**: Per-server settings (channels, thresholds, pause state)
- **tracked_wallets**: Custom wallet addresses to monitor per server
- **seen_transactions**: Prevent duplicate alerts
- **wallet_activity**: Track wallet history for fresh wallet detection
- **price_snapshots**: Store periodic market prices for volatility detection
- **volatility_alerts**: Track recently alerted markets to prevent spam

## Discord Commands

| Command | Description | Permissions |
|---------|-------------|-------------|
| `/setup` | Configure all alert channels at once (whale, fresh_wallet, tracked_wallet, volatility, sports) | Admin |
| `/whale_channel #channel` | Set whale alerts channel individually | Admin |
| `/fresh_wallet_channel #channel` | Set fresh wallet alerts channel individually | Admin |
| `/tracked_wallet_channel #channel` | Set tracked wallet alerts channel individually | Admin |
| `/volatility #channel` | Set volatility alerts channel individually | Admin |
| `/sports #channel` | Set sports alerts channel individually | Admin |
| `/top_trader_channel #channel` | Set top 25 trader alerts channel | Admin |
| `/threshold <amount>` | Set USD threshold | Admin |
| `/sports_threshold <amount>` | Set sports USD threshold (default: $5k) | Admin |
| `/fresh_wallet_threshold <amount>` | Set fresh wallet USD threshold (default: $10k) | Admin |
| `/track <wallet> [label]` | Track a wallet | Admin |
| `/untrack <wallet>` | Stop tracking | Admin |
| `/rename <wallet> <name>` | Rename a tracked wallet | Admin |
| `/positions` | View tracked wallets' positions | Anyone |
| `/list` | Show settings | Anyone |
| `/pause` | Pause alerts | Admin |
| `/resume` | Resume alerts | Admin |
| `/trending` | Show top 10 trending markets by 24h volume | Anyone |
| `/sports_trending` | Show top 10 trending sports markets by 24h volume | Anyone |
| `/search <keywords>` | Search markets and view orderbooks | Anyone |
| `/help` | Show commands | Anyone |

## Alert Types

- **Whale Alerts**: Large transactions $10k+ (configurable threshold)
- **Fresh Wallet Alerts**: New wallets making first trades $10k+
- **Tracked Wallet Alerts**: Any activity from tracked wallets (no minimum)
- **Volatility Alerts**: Markets with 20%+ price swings within 1 hour (separate channel)
- **Top Trader Alerts**: Any trade from top 25 all-time profit leaders (separate channel)
- **Sports Alerts**: Sports/esports market activity $5k+ (separate channel, configurable threshold)

Note: Sells above 99% are automatically excluded from all alerts. Sports markets go to their own dedicated channel when configured.

## Environment Variables

- `DISCORD_BOT_TOKEN` - Required Discord bot token
- `DATABASE_URL` - PostgreSQL connection string (auto-configured)

## Recent Changes

- 2026-01-05: **Added Top Trader Alerts** - monitors trades from top 25 all-time profit leaders, configurable via /top_trader_channel
- 2026-01-05: **Improved /untrack command** - now uses dropdown menu to select wallet instead of pasting address
- 2026-01-05: **Fixed Onsight trade button** - now uses correct `event_{slug_with_underscores}` format that Polysight bot expects (hyphens replaced with underscores, prefixed with "event_")
- 2026-01-05: **Fixed market URLs** - changed from /event/{slug} to /market/{slug} format which works for all market types (sports, events, standalone) and auto-redirects correctly
- 2026-01-05: **Added granular channel configuration** - each alert type can now be routed to a specific channel: /whale_channel, /fresh_wallet_channel, /tracked_wallet_channel (plus existing /volatility and /sports). Falls back to /setup channel if not configured.
- 2026-01-05: Added /trending and /sports_trending commands to view top markets by 24h volume
- 2026-01-05: **Fixed fresh wallet detection** - now queries Polymarket API to check if wallet has prior activity (prevents false fresh alerts for experienced traders)
- 2026-01-05: Removed win rate calculation (was not calculating correctly)
- 2026-01-05: Added debug logging to monitor loop to track large trades (shows when $10k+ trades are detected)
- 2026-01-05: **PnL now matches Polymarket exactly** - switched to official v1/leaderboard endpoint which returns Polymarket's calculated PnL, volume, and rank (verified: SeriouslySirius shows $3.9M matching the site)
- 2026-01-05: Switched to paginated Data API calls for accurate PnL matching Polymarket's displayed values - fetches open + closed positions up to 10,000 offset with realized PnL summing
- 2026-01-05: Integrated Goldsky PnL Subgraph for complete wallet statistics - now fetches ALL positions (unlimited) via GraphQL with pagination, eliminating the 500 position limit from Data API
- 2026-01-05: Added PnL and win rate display to /list command for each tracked wallet
- 2026-01-05: Fixed PnL to fetch both open AND closed positions from API for accurate all-time totals (was missing historical closed positions)
- 2026-01-05: Fixed PnL calculation to count all positions with realized PnL (not just fully closed ones) for accurate totals
- 2026-01-05: Fixed tracked wallet alerts to only show trades made AFTER wallet was added to tracking (prevents old historical trades from appearing)
- 2026-01-05: Fixed PnL stats to use Data API endpoint (was using wrong Gamma API endpoint which returned no data)
- 2026-01-05: Improved alert display - shows "BUY Yes" / "SELL No" action format, full wallet addresses (copyable), and better market URL construction with cache lookup and conditionId fallback
- 2026-01-05: Added lifetime PnL and win rate to tracked wallet alerts (fetched from Polymarket API with 10-min caching)
- 2026-01-05: Added /sports_threshold command - configurable threshold for sports market alerts (default $5k)
- 2026-01-05: Added separate sports channel (/sports command) - sports markets now route to dedicated channel instead of being excluded
- 2026-01-05: Fixed sports detection with market metadata caching for accurate identification
- 2026-01-05: Removed redeem alerts and filtered out sells above 99%
- 2026-01-05: Updated trade button to "Trade via Onsight"
- 2026-01-05: Added volatility tracker with separate channel (/volatility command) for 20%+ price swings
- 2026-01-05: Added /positions command with wallet buttons for drill-down views
- 2026-01-05: Added /rename command to update tracked wallet labels
- 2026-01-05: Fixed tracked wallet alerts to query each wallet directly via API (catches all trades regardless of amount)
- 2026-01-05: Initial project setup with full bot implementation

## User Preferences

- Keep Discord interface simple
- Real-time alerts only (no summaries)
- Default thresholds: $10k for whale and fresh wallet alerts, $5k for sports alerts
