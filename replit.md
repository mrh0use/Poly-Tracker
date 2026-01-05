# Polymarket Discord Bot

## Overview
A Discord bot that monitors Polymarket activity and sends real-time alerts to configured Discord channels. Supports multiple Discord servers with individual configurations.

## Features
- **Whale Alerts**: Notifications for large transactions ($10k+ by default)
- **Fresh Wallet Alerts**: Detect new wallets making their first trades
- **Custom Wallet Tracking**: Monitor specific wallet addresses
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

- **server_configs**: Per-server settings (channel, thresholds, pause state)
- **tracked_wallets**: Custom wallet addresses to monitor per server
- **seen_transactions**: Prevent duplicate alerts
- **wallet_activity**: Track wallet history for fresh wallet detection

## Discord Commands

| Command | Description | Permissions |
|---------|-------------|-------------|
| `/setup #channel` | Set alerts channel | Admin |
| `/threshold <amount>` | Set USD threshold | Admin |
| `/track <wallet> [label]` | Track a wallet | Admin |
| `/untrack <wallet>` | Stop tracking | Admin |
| `/list` | Show settings | Anyone |
| `/pause` | Pause alerts | Admin |
| `/resume` | Resume alerts | Admin |
| `/help` | Show commands | Anyone |

## Environment Variables

- `DISCORD_BOT_TOKEN` - Required Discord bot token
- `DATABASE_URL` - PostgreSQL connection string (auto-configured)

## Recent Changes

- 2026-01-05: Initial project setup with full bot implementation

## User Preferences

- Keep Discord interface simple
- Real-time alerts only (no summaries)
- Default thresholds: $10k for whale and fresh wallet alerts
