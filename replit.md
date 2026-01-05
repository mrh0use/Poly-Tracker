# Polymarket Discord Bot

## Overview
A Discord bot that monitors Polymarket activity and sends real-time alerts to configured Discord channels. Supports multiple Discord servers with individual configurations.

## Features
- **Whale Alerts**: Notifications for large transactions ($10k+ by default)
- **Fresh Wallet Alerts**: Detect new wallets making their first trades
- **Custom Wallet Tracking**: Monitor specific wallet addresses (any trade amount)
- **Redeem Alerts**: Notifications when tracked wallets cash out positions
- **Volatility Alerts**: Track markets with 20%+ price swings within 1 hour
- **Sports Filtering**: Automatically excludes sports/esports markets from all alerts
- **Position Viewing**: View current holdings of tracked wallets with drill-down
- **Market Links**: Clickable links to Polymarket + Trade on Polysight buttons
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
| `/setup #channel` | Set trade alerts channel | Admin |
| `/volatility #channel` | Set volatility alerts channel | Admin |
| `/threshold <amount>` | Set USD threshold | Admin |
| `/track <wallet> [label]` | Track a wallet | Admin |
| `/untrack <wallet>` | Stop tracking | Admin |
| `/rename <wallet> <name>` | Rename a tracked wallet | Admin |
| `/positions` | View tracked wallets' positions | Anyone |
| `/list` | Show settings | Anyone |
| `/pause` | Pause alerts | Admin |
| `/resume` | Resume alerts | Admin |
| `/help` | Show commands | Anyone |

## Alert Types

- **Whale Alerts**: Large transactions $10k+ (configurable threshold)
- **Fresh Wallet Alerts**: New wallets making first trades $10k+
- **Tracked Wallet Alerts**: Any activity from tracked wallets (no minimum)
- **Redeem Alerts**: When tracked wallets cash out winning positions
- **Volatility Alerts**: Markets with 20%+ price swings within 1 hour (separate channel)

Note: Sports and esports markets are automatically excluded from all alert types.

## Environment Variables

- `DISCORD_BOT_TOKEN` - Required Discord bot token
- `DATABASE_URL` - PostgreSQL connection string (auto-configured)

## Recent Changes

- 2026-01-05: Added volatility tracker with separate channel (/volatility command) for 20%+ price swings
- 2026-01-05: Added sports market filtering - excludes all sports/esports from alerts
- 2026-01-05: Added redeem tracking, position viewing, market links, and Polysight trade buttons
- 2026-01-05: Added /positions command with wallet buttons for drill-down views
- 2026-01-05: Added /rename command to update tracked wallet labels
- 2026-01-05: Fixed tracked wallet alerts to query each wallet directly via API (catches all trades regardless of amount)
- 2026-01-05: Initial project setup with full bot implementation

## User Preferences

- Keep Discord interface simple
- Real-time alerts only (no summaries)
- Default thresholds: $10k for whale and fresh wallet alerts
