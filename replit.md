# Polymarket Discord Bot

## Overview
This project is a Discord bot designed to monitor Polymarket activity and deliver real-time, configurable alerts to Discord channels. Its primary purpose is to provide users with immediate insights into significant market movements, including large transactions, new trader activity, market volatility, and top trader actions. The bot supports multiple Discord servers, each with independent configuration capabilities, aiming to enhance user engagement and provide timely, actionable information for Polymarket participants.

## User Preferences
- Keep Discord interface simple
- Real-time alerts only (no summaries)
- Default thresholds: $10k for whale and fresh wallet alerts, $5k for sports alerts

## System Architecture
The bot is built around a modular architecture comprising a main Discord bot handling slash commands and a monitoring loop, a dedicated Polymarket API client, a database layer for persistence, and an alerting module for formatting Discord embeds.

**Technical Implementations & Feature Specifications:**
- **Real-time Monitoring:** Utilizes Polymarket's RTDS WebSocket for instant trade alerts, with a polling mechanism as a backup.
- **Alert Types:**
    - **Whale Alerts:** Configurable threshold for large transactions (default $10k+).
    - **Fresh Wallet Alerts:** Identifies new wallets making their first trades (default $10k+).
    - **Custom Wallet Tracking:** Monitors activity for specific user-defined wallet addresses.
    - **Volatility Alerts:** Multi-timeframe detection with simultaneous 5/15/60-minute windows. Alerts on shortest timeframe that triggers (most urgent). Urgency-based formatting: 🚨 RAPID (5min), ⚡ Fast (15min), 📊 Swing (60min). Per-timeframe cooldowns (15min) prevent spam. 5-minute warm-up period after restart. Category filtering available (All, Sports, Crypto, Politics, Entertainment). SELL prices tracked for full market movement capture.
    - **Top Trader Alerts:** Tracks trades from Polymarket's top 25 all-time profit leaders (triggers at $2.5k+, uses on-demand leaderboard lookups with 24-hour negative result caching).
    - **Sports/Esports Alerts:** Dedicated channel for sports-related market activity (default $5k+).
    - **Bonds Alerts:** For high-certainty markets (>=95% price, $5k+).
- **Filtering:** Excludes sell transactions above 99% (position closures) and focuses on BUY transactions for most alerts.
- **Data Enrichment:** Alerts include trader's lifetime PnL, rank, and cash balance for tracked wallets.
- **Interactive Elements:** Alerts feature clickable links to Polymarket and "Trade via Onsight" buttons.
- **Configuration:**
    - **Per-Server Settings:** Each Discord server maintains its own configuration for alert channels, thresholds, and tracking.
    - **Slash Commands:** Intuitive Discord slash commands (`/setup`, `/whale_channel`, `/track`, `/positions`, `/list`, `/pause`, `/resume`, `/trending`, `/search`, `/help`) for easy management.
- **Performance Optimizations:** Implements caching for server configurations and tracked wallets, API call timeouts, and efficient WebSocket handling to reduce CPU and database load. Volatility tracking is managed in-memory to minimize database writes.
- **Resilience:** Features WebSocket activity timeouts, proactive reconnections, and a backup WebSocket for enhanced reliability.

**UI/UX Decisions:**
- **Simple Discord Interface:** Focus on clear, concise alerts and easy-to-use slash commands for configuration.
- **Informative Embeds:** Discord alerts are formatted as rich embeds, including essential trade details, market links, and trader statistics.

## External Dependencies
- **Polymarket API:** Primary data source for market data, trades, and wallet information.
- **Discord API:** For bot interaction, sending messages, and receiving commands.
- **PostgreSQL:** Used for database persistence (`DATABASE_URL`), storing server configurations, tracked wallets, and historical data like `seen_transactions`, `wallet_activity`, and `price_snapshots`.
- **Polygon Blockchain:** Queried for real-time wallet cash balances.
- **Goldsky PnL Subgraph:** Utilized for fetching comprehensive wallet PnL statistics via GraphQL.
- **Railway:** Platform providing hosting and health check services.