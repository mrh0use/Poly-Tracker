import os
import discord
from discord import app_commands
from discord.ext import commands, tasks
from discord.ui import View, Button
import asyncio
from datetime import datetime
from typing import Optional

from database import init_db, get_session, ServerConfig, TrackedWallet, SeenTransaction, WalletActivity, PriceSnapshot, VolatilityAlert
from polymarket_client import polymarket_client
from alerts import (
    create_whale_alert_embed,
    create_fresh_wallet_alert_embed,
    create_custom_wallet_alert_embed,
    create_top_trader_alert_embed,
    create_settings_embed,
    create_trade_button_view,
    create_positions_overview_embed,
    create_wallet_positions_embed,
    create_volatility_alert_embed
)


class PolymarketBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.guilds = True
        
        super().__init__(command_prefix="!", intents=intents)
        self.synced = False
    
    async def setup_hook(self):
        init_db()
        print("Database initialized")
    
    async def on_ready(self):
        print(f"Logged in as {self.user} (ID: {self.user.id})")
        
        if not self.synced:
            await self.tree.sync()
            self.synced = True
            print("Slash commands synced")
        
        if not monitor_loop.is_running():
            monitor_loop.start()
            print("Monitor loop started")
        
        if not volatility_loop.is_running():
            volatility_loop.start()
            print("Volatility loop started")
        
        if not cleanup_loop.is_running():
            cleanup_loop.start()
            print("Cleanup loop started")
        
        await polymarket_client.fetch_sports_tags()
        print("Sports tags loaded")


bot = PolymarketBot()


@bot.tree.command(name="setup", description="Configure all alert channels at once")
@app_commands.describe(
    whale="Channel for whale alerts ($10k+)",
    fresh_wallet="Channel for fresh wallet alerts",
    tracked_wallet="Channel for tracked wallet alerts",
    volatility="Channel for volatility alerts (20%+ swings)",
    sports="Channel for sports/esports alerts",
    top_trader="Channel for top 25 trader alerts"
)
@app_commands.checks.has_permissions(administrator=True)
async def setup(
    interaction: discord.Interaction,
    whale: Optional[discord.TextChannel] = None,
    fresh_wallet: Optional[discord.TextChannel] = None,
    tracked_wallet: Optional[discord.TextChannel] = None,
    volatility: Optional[discord.TextChannel] = None,
    sports: Optional[discord.TextChannel] = None,
    top_trader: Optional[discord.TextChannel] = None
):
    if not any([whale, fresh_wallet, tracked_wallet, volatility, sports, top_trader]):
        await interaction.response.send_message(
            "Please specify at least one channel to configure.\n"
            "Example: `/setup whale:#whale-alerts fresh_wallet:#fresh-alerts`",
            ephemeral=True
        )
        return
    
    session = get_session()
    try:
        config = session.query(ServerConfig).filter_by(guild_id=interaction.guild_id).first()
        if not config:
            config = ServerConfig(guild_id=interaction.guild_id)
            session.add(config)
        
        configured = []
        if whale:
            config.whale_channel_id = whale.id
            config.alert_channel_id = whale.id
            configured.append(f"Whale: {whale.mention}")
        if fresh_wallet:
            config.fresh_wallet_channel_id = fresh_wallet.id
            configured.append(f"Fresh Wallet: {fresh_wallet.mention}")
        if tracked_wallet:
            config.tracked_wallet_channel_id = tracked_wallet.id
            configured.append(f"Tracked Wallet: {tracked_wallet.mention}")
        if volatility:
            config.volatility_channel_id = volatility.id
            configured.append(f"Volatility: {volatility.mention}")
        if sports:
            config.sports_channel_id = sports.id
            configured.append(f"Sports: {sports.mention}")
        if top_trader:
            config.top_trader_channel_id = top_trader.id
            configured.append(f"Top Trader: {top_trader.mention}")
        
        session.commit()
        
        await interaction.response.send_message(
            f"**Channels configured:**\n" + "\n".join(configured) +
            "\n\nUse `/threshold` to adjust alert thresholds or `/list` to view all settings.",
            ephemeral=True
        )
    finally:
        session.close()


@bot.tree.command(name="whale_channel", description="Set the channel for whale alerts")
@app_commands.describe(channel="The channel to send whale alerts to")
@app_commands.checks.has_permissions(administrator=True)
async def whale_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    session = get_session()
    try:
        config = session.query(ServerConfig).filter_by(guild_id=interaction.guild_id).first()
        if not config:
            config = ServerConfig(guild_id=interaction.guild_id)
            session.add(config)
        config.whale_channel_id = channel.id
        session.commit()
        await interaction.response.send_message(
            f"Whale alerts will now be sent to {channel.mention}",
            ephemeral=True
        )
    finally:
        session.close()


@bot.tree.command(name="fresh_wallet_channel", description="Set the channel for fresh wallet alerts")
@app_commands.describe(channel="The channel to send fresh wallet alerts to")
@app_commands.checks.has_permissions(administrator=True)
async def fresh_wallet_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    session = get_session()
    try:
        config = session.query(ServerConfig).filter_by(guild_id=interaction.guild_id).first()
        if not config:
            config = ServerConfig(guild_id=interaction.guild_id)
            session.add(config)
        config.fresh_wallet_channel_id = channel.id
        session.commit()
        await interaction.response.send_message(
            f"Fresh wallet alerts will now be sent to {channel.mention}",
            ephemeral=True
        )
    finally:
        session.close()


@bot.tree.command(name="tracked_wallet_channel", description="Set the channel for tracked wallet alerts")
@app_commands.describe(channel="The channel to send tracked wallet alerts to")
@app_commands.checks.has_permissions(administrator=True)
async def tracked_wallet_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    session = get_session()
    try:
        config = session.query(ServerConfig).filter_by(guild_id=interaction.guild_id).first()
        if not config:
            config = ServerConfig(guild_id=interaction.guild_id)
            session.add(config)
        config.tracked_wallet_channel_id = channel.id
        session.commit()
        await interaction.response.send_message(
            f"Tracked wallet alerts will now be sent to {channel.mention}",
            ephemeral=True
        )
    finally:
        session.close()


@bot.tree.command(name="threshold", description="Set the whale alert threshold")
@app_commands.describe(amount="Minimum USD value for whale alerts (e.g., 10000)")
@app_commands.checks.has_permissions(administrator=True)
async def threshold(interaction: discord.Interaction, amount: float):
    if amount < 100:
        await interaction.response.send_message(
            "Threshold must be at least $100",
            ephemeral=True
        )
        return
    
    session = get_session()
    try:
        config = session.query(ServerConfig).filter_by(guild_id=interaction.guild_id).first()
        if not config:
            config = ServerConfig(guild_id=interaction.guild_id)
            session.add(config)
        
        config.whale_threshold = amount
        session.commit()
        
        await interaction.response.send_message(
            f"Whale alert threshold set to ${amount:,.0f}",
            ephemeral=True
        )
    finally:
        session.close()


@bot.tree.command(name="track", description="Add a wallet address to track")
@app_commands.describe(
    wallet="The wallet address to track (0x...)",
    label="Optional label for this wallet (e.g., 'Whale 1')"
)
@app_commands.checks.has_permissions(administrator=True)
async def track(interaction: discord.Interaction, wallet: str, label: Optional[str] = None):
    wallet = wallet.strip().lower()
    
    if not wallet.startswith("0x") or len(wallet) != 42:
        await interaction.response.send_message(
            "Invalid wallet address. Must be a valid Ethereum address (0x...)",
            ephemeral=True
        )
        return
    
    session = get_session()
    try:
        config = session.query(ServerConfig).filter_by(guild_id=interaction.guild_id).first()
        if not config:
            config = ServerConfig(guild_id=interaction.guild_id)
            session.add(config)
            session.commit()
        
        existing = session.query(TrackedWallet).filter_by(
            guild_id=interaction.guild_id,
            wallet_address=wallet
        ).first()
        
        if existing:
            await interaction.response.send_message(
                f"Wallet `{wallet[:6]}...{wallet[-4:]}` is already being tracked",
                ephemeral=True
            )
            return
        
        tracked = TrackedWallet(
            guild_id=interaction.guild_id,
            wallet_address=wallet,
            label=label,
            added_by=interaction.user.id
        )
        session.add(tracked)
        session.commit()
        
        label_text = f" with label '{label}'" if label else ""
        await interaction.response.send_message(
            f"Now tracking wallet `{wallet[:6]}...{wallet[-4:]}`{label_text}",
            ephemeral=True
        )
    finally:
        session.close()


class UntrackSelect(discord.ui.Select):
    def __init__(self, wallets: list):
        options = []
        for w in wallets[:25]:
            label = w.label if w.label else f"{w.wallet_address[:6]}...{w.wallet_address[-4:]}"
            description = w.wallet_address[:20] + "..." if len(w.wallet_address) > 20 else w.wallet_address
            options.append(discord.SelectOption(
                label=label[:100],
                value=w.wallet_address,
                description=description
            ))
        super().__init__(placeholder="Select a wallet to untrack...", options=options)
    
    async def callback(self, interaction: discord.Interaction):
        wallet = self.values[0]
        session = get_session()
        try:
            tracked = session.query(TrackedWallet).filter_by(
                guild_id=interaction.guild_id,
                wallet_address=wallet
            ).first()
            
            if tracked:
                label = tracked.label or f"{wallet[:6]}...{wallet[-4:]}"
                session.delete(tracked)
                session.commit()
                await interaction.response.send_message(
                    f"Stopped tracking wallet: {label}",
                    ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    "Wallet not found",
                    ephemeral=True
                )
        finally:
            session.close()


class UntrackView(discord.ui.View):
    def __init__(self, wallets: list):
        super().__init__(timeout=60)
        self.add_item(UntrackSelect(wallets))


@bot.tree.command(name="untrack", description="Remove a wallet from tracking")
@app_commands.checks.has_permissions(administrator=True)
async def untrack(interaction: discord.Interaction):
    session = get_session()
    try:
        tracked = session.query(TrackedWallet).filter_by(
            guild_id=interaction.guild_id
        ).all()
        
        if not tracked:
            await interaction.response.send_message(
                "No wallets are currently being tracked",
                ephemeral=True
            )
            return
        
        view = UntrackView(tracked)
        await interaction.response.send_message(
            "Select a wallet to stop tracking:",
            view=view,
            ephemeral=True
        )
    finally:
        session.close()


@bot.tree.command(name="list", description="Show current settings and tracked wallets")
async def list_settings(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    
    session = get_session()
    try:
        config = session.query(ServerConfig).filter_by(guild_id=interaction.guild_id).first()
        
        if not config:
            await interaction.followup.send(
                "No configuration found. Use `/setup` to get started.",
                ephemeral=True
            )
            return
        
        channel_name = None
        if config.alert_channel_id:
            channel = interaction.guild.get_channel(config.alert_channel_id)
            channel_name = channel.name if channel else None
        
        tracked = session.query(TrackedWallet).filter_by(guild_id=interaction.guild_id).all()
        
        wallet_stats = {}
        for w in tracked[:10]:
            try:
                stats = await polymarket_client.get_wallet_pnl_stats(w.wallet_address)
                wallet_stats[w.wallet_address.lower()] = stats
            except Exception as e:
                print(f"Error fetching stats for {w.wallet_address}: {e}")
        
        volatility_channel_name = None
        if config.volatility_channel_id:
            vol_channel = interaction.guild.get_channel(config.volatility_channel_id)
            volatility_channel_name = vol_channel.name if vol_channel else None
        
        sports_channel_name = None
        if config.sports_channel_id:
            sports_channel = interaction.guild.get_channel(config.sports_channel_id)
            sports_channel_name = sports_channel.name if sports_channel else None
        
        whale_channel_name = None
        if config.whale_channel_id:
            whale_ch = interaction.guild.get_channel(config.whale_channel_id)
            whale_channel_name = whale_ch.name if whale_ch else None
        
        fresh_wallet_channel_name = None
        if config.fresh_wallet_channel_id:
            fresh_ch = interaction.guild.get_channel(config.fresh_wallet_channel_id)
            fresh_wallet_channel_name = fresh_ch.name if fresh_ch else None
        
        tracked_wallet_channel_name = None
        if config.tracked_wallet_channel_id:
            tracked_ch = interaction.guild.get_channel(config.tracked_wallet_channel_id)
            tracked_wallet_channel_name = tracked_ch.name if tracked_ch else None
        
        embed = create_settings_embed(
            guild_name=interaction.guild.name,
            channel_name=channel_name,
            whale_threshold=config.whale_threshold,
            fresh_wallet_threshold=config.fresh_wallet_threshold,
            is_paused=config.is_paused,
            tracked_wallets=tracked,
            volatility_channel_name=volatility_channel_name,
            volatility_threshold=config.volatility_threshold or 20.0,
            sports_channel_name=sports_channel_name,
            sports_threshold=config.sports_threshold or 5000.0,
            wallet_stats=wallet_stats,
            whale_channel_name=whale_channel_name,
            fresh_wallet_channel_name=fresh_wallet_channel_name,
            tracked_wallet_channel_name=tracked_wallet_channel_name
        )
        
        await interaction.followup.send(embed=embed, ephemeral=True)
    finally:
        session.close()


@bot.tree.command(name="pause", description="Pause all alerts for this server")
@app_commands.checks.has_permissions(administrator=True)
async def pause(interaction: discord.Interaction):
    session = get_session()
    try:
        config = session.query(ServerConfig).filter_by(guild_id=interaction.guild_id).first()
        if not config:
            await interaction.response.send_message(
                "No configuration found. Use `/setup` first.",
                ephemeral=True
            )
            return
        
        config.is_paused = True
        session.commit()
        
        await interaction.response.send_message(
            "Alerts have been paused. Use `/resume` to start them again.",
            ephemeral=True
        )
    finally:
        session.close()


@bot.tree.command(name="resume", description="Resume alerts for this server")
@app_commands.checks.has_permissions(administrator=True)
async def resume(interaction: discord.Interaction):
    session = get_session()
    try:
        config = session.query(ServerConfig).filter_by(guild_id=interaction.guild_id).first()
        if not config:
            await interaction.response.send_message(
                "No configuration found. Use `/setup` first.",
                ephemeral=True
            )
            return
        
        config.is_paused = False
        session.commit()
        
        await interaction.response.send_message(
            "Alerts have been resumed.",
            ephemeral=True
        )
    finally:
        session.close()


@bot.tree.command(name="volatility", description="Set the channel for volatility alerts")
@app_commands.describe(channel="The channel to send volatility alerts to")
@app_commands.checks.has_permissions(administrator=True)
async def volatility(interaction: discord.Interaction, channel: discord.TextChannel):
    session = get_session()
    try:
        config = session.query(ServerConfig).filter_by(guild_id=interaction.guild_id).first()
        if not config:
            config = ServerConfig(guild_id=interaction.guild_id)
            session.add(config)
        
        config.volatility_channel_id = channel.id
        session.commit()
        
        await interaction.response.send_message(
            f"Volatility alerts will be sent to {channel.mention}. Markets with 20%+ price swings within 1 hour will trigger alerts.",
            ephemeral=True
        )
    finally:
        session.close()


@bot.tree.command(name="sports", description="Set the channel for sports market alerts")
@app_commands.describe(channel="The channel to send sports alerts to")
@app_commands.checks.has_permissions(administrator=True)
async def sports(interaction: discord.Interaction, channel: discord.TextChannel):
    session = get_session()
    try:
        config = session.query(ServerConfig).filter_by(guild_id=interaction.guild_id).first()
        if not config:
            config = ServerConfig(guild_id=interaction.guild_id)
            session.add(config)
        
        config.sports_channel_id = channel.id
        session.commit()
        
        await interaction.response.send_message(
            f"Sports market alerts will be sent to {channel.mention}. All sports/esports trading activity will be routed here.",
            ephemeral=True
        )
    finally:
        session.close()


@bot.tree.command(name="sports_threshold", description="Set the minimum USD value for sports market alerts")
@app_commands.describe(amount="Minimum USD value for sports alerts (e.g., 5000)")
@app_commands.checks.has_permissions(administrator=True)
async def sports_threshold(interaction: discord.Interaction, amount: float):
    if amount < 100:
        await interaction.response.send_message(
            "Threshold must be at least $100",
            ephemeral=True
        )
        return
    
    session = get_session()
    try:
        config = session.query(ServerConfig).filter_by(guild_id=interaction.guild_id).first()
        if not config:
            config = ServerConfig(guild_id=interaction.guild_id)
            session.add(config)
        
        config.sports_threshold = amount
        session.commit()
        
        await interaction.response.send_message(
            f"Sports alert threshold set to ${amount:,.0f}",
            ephemeral=True
        )
    finally:
        session.close()


@bot.tree.command(name="fresh_wallet_threshold", description="Set the minimum USD value for fresh wallet alerts")
@app_commands.describe(amount="Minimum USD value for fresh wallet alerts (e.g., 10000)")
@app_commands.checks.has_permissions(administrator=True)
async def fresh_wallet_threshold_cmd(interaction: discord.Interaction, amount: float):
    if amount < 100:
        await interaction.response.send_message(
            "Threshold must be at least $100",
            ephemeral=True
        )
        return
    
    session = get_session()
    try:
        config = session.query(ServerConfig).filter_by(guild_id=interaction.guild_id).first()
        if not config:
            config = ServerConfig(guild_id=interaction.guild_id)
            session.add(config)
        
        config.fresh_wallet_threshold = amount
        session.commit()
        
        await interaction.response.send_message(
            f"Fresh wallet alert threshold set to ${amount:,.0f}",
            ephemeral=True
        )
    finally:
        session.close()


@bot.tree.command(name="top_trader_channel", description="Set the channel for top 25 trader alerts")
@app_commands.describe(channel="The channel to send top trader alerts to")
@app_commands.checks.has_permissions(administrator=True)
async def top_trader_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    session = get_session()
    try:
        config = session.query(ServerConfig).filter_by(guild_id=interaction.guild_id).first()
        if not config:
            config = ServerConfig(guild_id=interaction.guild_id)
            session.add(config)
        
        config.top_trader_channel_id = channel.id
        session.commit()
        
        await interaction.response.send_message(
            f"Top 25 trader alerts will be sent to {channel.mention}. All trades from top 25 all-time profit leaders will be shown here.",
            ephemeral=True
        )
    finally:
        session.close()


@bot.tree.command(name="trending", description="Show top trending markets by 24h volume")
async def trending_command(interaction: discord.Interaction):
    await interaction.response.defer()
    
    markets = await polymarket_client.get_trending_markets(limit=10, sports_only=False)
    
    if not markets:
        await interaction.followup.send("No trending markets found.", ephemeral=True)
        return
    
    embed = discord.Embed(
        title="Trending Markets (24h Volume)",
        description="Top non-sports markets by trading volume",
        color=0x4ECDC4
    )
    
    for i, market in enumerate(markets, 1):
        volume_str = f"${market['volume_24h']:,.0f}"
        price_str = f"{market['yes_price']*100:.0f}%"
        url = f"https://polymarket.com/market/{market['slug']}" if market['slug'] else None
        
        name = f"{i}. {market['question'][:60]}{'...' if len(market['question']) > 60 else ''}"
        value = f"Volume: {volume_str} | Yes: {price_str}"
        if url:
            value += f"\n[View Market]({url})"
        
        embed.add_field(name=name, value=value, inline=False)
    
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="sports_trending", description="Show top trending sports markets by 24h volume")
async def sports_trending_command(interaction: discord.Interaction):
    await interaction.response.defer()
    
    markets = await polymarket_client.get_trending_markets(limit=10, sports_only=True)
    
    if not markets:
        await interaction.followup.send("No trending sports markets found.", ephemeral=True)
        return
    
    embed = discord.Embed(
        title="Trending Sports Markets (24h Volume)",
        description="Top sports/esports markets by trading volume",
        color=0xFF6B35
    )
    
    for i, market in enumerate(markets, 1):
        volume_str = f"${market['volume_24h']:,.0f}"
        price_str = f"{market['yes_price']*100:.0f}%"
        url = f"https://polymarket.com/market/{market['slug']}" if market['slug'] else None
        
        name = f"{i}. {market['question'][:60]}{'...' if len(market['question']) > 60 else ''}"
        value = f"Volume: {volume_str} | Yes: {price_str}"
        if url:
            value += f"\n[View Market]({url})"
        
        embed.add_field(name=name, value=value, inline=False)
    
    await interaction.followup.send(embed=embed)


class MarketSearchSelect(discord.ui.Select):
    def __init__(self, markets: list):
        self.markets_data = {str(i): m for i, m in enumerate(markets[:25])}
        options = []
        for i, m in enumerate(markets[:25]):
            vol_str = f"${m['volume']:,.0f}" if m['volume'] >= 1000 else f"${m['volume']:.0f}"
            liq_str = f"${m['liquidity']:,.0f}" if m['liquidity'] >= 1000 else f"${m['liquidity']:.0f}"
            
            prices = m.get('outcome_prices', [0.5, 0.5])
            outcomes = m.get('outcomes', ['Yes', 'No'])
            
            price_parts = []
            for j, outcome in enumerate(outcomes[:2]):
                if j < len(prices):
                    try:
                        p = float(prices[j]) * 100
                        price_parts.append(f"{outcome}: {p:.1f}c")
                    except (ValueError, TypeError):
                        price_parts.append(f"{outcome}: ?")
            
            desc = f"Vol: {vol_str} | Liq: {liq_str} | {' | '.join(price_parts)}"
            
            question = m['question'][:100] if len(m['question']) <= 100 else m['question'][:97] + "..."
            
            options.append(discord.SelectOption(
                label=question[:100],
                value=str(i),
                description=desc[:100]
            ))
        
        super().__init__(
            placeholder="Choose a market to view its orderbook...",
            min_values=1,
            max_values=1,
            options=options
        )
    
    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        market = self.markets_data.get(self.values[0])
        if not market:
            await interaction.followup.send("Market not found.", ephemeral=True)
            return
        
        token_ids = market.get('token_ids', [])
        if not token_ids:
            await interaction.followup.send("No orderbook data available for this market.", ephemeral=True)
            return
        
        yes_token = None
        for t in token_ids:
            if t.get('outcome', '').lower() == 'yes':
                yes_token = t.get('token_id')
                break
        
        if not yes_token and token_ids:
            yes_token = token_ids[0].get('token_id')
        
        if not yes_token:
            await interaction.followup.send("Could not find token ID for orderbook.", ephemeral=True)
            return
        
        orderbook = await polymarket_client.get_orderbook(yes_token)
        
        embed = create_orderbook_embed(
            market_title=market['question'],
            orderbook=orderbook,
            outcomes=market.get('outcomes', ['Yes', 'No'])
        )
        
        event_slug = market.get('event_slug', market.get('slug', ''))
        market_url = f"https://polymarket.com/market/{market.get('slug', '')}"
        
        from alerts import create_trade_button_view
        view = create_trade_button_view(event_slug, market_url)
        
        await interaction.followup.send(embed=embed, view=view)


class MarketSearchView(discord.ui.View):
    def __init__(self, markets: list):
        super().__init__(timeout=300)
        self.add_item(MarketSearchSelect(markets))


def create_orderbook_embed(market_title: str, orderbook: dict, outcomes: list) -> discord.Embed:
    mid = orderbook.get('mid', 0.5)
    spread = orderbook.get('spread', 0)
    bids = orderbook.get('bids', [])
    asks = orderbook.get('asks', [])
    total_bid_size = orderbook.get('total_bid_size', 0)
    total_ask_size = orderbook.get('total_ask_size', 0)
    
    outcome_name = outcomes[0] if outcomes else "Yes"
    
    embed = discord.Embed(
        title=f"{market_title[:80]}",
        description=f"**{outcome_name.upper()}** | Mid: {mid*100:.1f}c | Spread: {spread*100:.1f}c",
        color=0x4ECDC4,
        timestamp=datetime.utcnow()
    )
    
    asks_text = ""
    for ask in reversed(asks[:5]):
        price_cents = ask['price'] * 100
        size = ask['size']
        total = ask.get('total', size) * ask['price']
        asks_text += f"🔴 `{price_cents:5.1f}c` | {size:>8,.0f} | ${total:>8,.0f}\n"
    
    if asks_text:
        embed.add_field(
            name="🔴 Asks (Sell Orders)",
            value=f"Price  |    Size  |    Total\n{asks_text}",
            inline=False
        )
    
    if total_bid_size + total_ask_size > 0:
        bid_pct = total_bid_size / (total_bid_size + total_ask_size)
        ask_pct = 1 - bid_pct
        
        def format_size(size):
            if size >= 1_000_000:
                return f"{size/1_000_000:.1f}M"
            elif size >= 1000:
                return f"{size/1000:.0f}K"
            return f"{size:.0f}"
        
        bid_str = format_size(total_bid_size)
        ask_str = format_size(total_ask_size)
        
        embed.add_field(
            name="Depth",
            value=f"🟢 Bids: **{bid_str}** ({bid_pct*100:.0f}%) | 🔴 Asks: **{ask_str}** ({ask_pct*100:.0f}%)",
            inline=False
        )
    
    bids_text = ""
    for bid in bids[:5]:
        price_cents = bid['price'] * 100
        size = bid['size']
        total = bid.get('total', size) * bid['price']
        bids_text += f"🟢 `{price_cents:5.1f}c` | {size:>8,.0f} | ${total:>8,.0f}\n"
    
    if bids_text:
        embed.add_field(
            name="🟢 Bids (Buy Orders)",
            value=f"Price  |    Size  |    Total\n{bids_text}",
            inline=False
        )
    
    if not bids and not asks:
        embed.add_field(
            name="Orderbook",
            value="No orders currently available",
            inline=False
        )
    
    return embed


@bot.tree.command(name="search", description="Search markets by keyword and view orderbooks")
@app_commands.describe(query="Keywords to search for (e.g., 'knicks', 'trump', 'bitcoin')")
async def search_command(interaction: discord.Interaction, query: str):
    await interaction.response.defer()
    
    markets = await polymarket_client.search_markets(query, limit=30)
    
    if not markets:
        await interaction.followup.send(f"No markets found matching '{query}'", ephemeral=True)
        return
    
    embed = discord.Embed(
        title=f'Multiple Markets Found: "{query}"',
        description=f"Found {len(markets)} active markets matching your search.\nUse the dropdown menu below to select which market to view:",
        color=0x4ECDC4
    )
    
    for i, m in enumerate(markets[:5], 1):
        vol_str = f"${m['volume']:,.0f}" if m['volume'] >= 1000 else f"${m['volume']:.0f}"
        liq_str = f"${m['liquidity']:,.0f}" if m['liquidity'] >= 1000 else f"${m['liquidity']:.0f}"
        
        prices = m.get('outcome_prices', [0.5, 0.5])
        outcomes = m.get('outcomes', ['Yes', 'No'])
        
        price_parts = []
        for j, outcome in enumerate(outcomes[:2]):
            if j < len(prices):
                try:
                    p = float(prices[j]) * 100
                    price_parts.append(f"{outcome}: {p:.1f}c")
                except (ValueError, TypeError):
                    pass
        
        question = m['question'][:60] + "..." if len(m['question']) > 60 else m['question']
        
        embed.add_field(
            name=f"{i}. {question}",
            value=f"📊 Vol: {vol_str} | Liq: {liq_str}\n💰 {' | '.join(price_parts)}",
            inline=False
        )
    
    if len(markets) > 5:
        embed.add_field(
            name="+ More options",
            value=f"And {len(markets) - 5} more markets available in the dropdown...",
            inline=False
        )
    
    view = MarketSearchView(markets)
    await interaction.followup.send(embed=embed, view=view)


@bot.tree.command(name="help", description="Show available commands")
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Polymarket Monitor - Help",
        description="Monitor Polymarket activity in your Discord server",
        color=0x4ECDC4
    )
    
    embed.add_field(
        name="/setup",
        value="Configure all alert channels at once (whale, fresh_wallet, tracked_wallet, volatility, sports, top_trader)",
        inline=False
    )
    embed.add_field(
        name="/volatility #channel",
        value="Set the channel for volatility alerts (20%+ swings)",
        inline=False
    )
    embed.add_field(
        name="/sports #channel",
        value="Set the channel for sports/esports market alerts",
        inline=False
    )
    embed.add_field(
        name="/whale_channel #channel",
        value="Set the channel for whale alerts",
        inline=False
    )
    embed.add_field(
        name="/fresh_wallet_channel #channel",
        value="Set the channel for fresh wallet alerts",
        inline=False
    )
    embed.add_field(
        name="/tracked_wallet_channel #channel",
        value="Set the channel for tracked wallet alerts",
        inline=False
    )
    embed.add_field(
        name="/top_trader_channel #channel",
        value="Set the channel for top 25 trader alerts",
        inline=False
    )
    embed.add_field(
        name="/threshold <amount>",
        value="Set the minimum USD value for alerts (default: $10,000)",
        inline=False
    )
    embed.add_field(
        name="/sports_threshold <amount>",
        value="Set the minimum USD value for sports alerts (default: $5,000)",
        inline=False
    )
    embed.add_field(
        name="/fresh_wallet_threshold <amount>",
        value="Set the minimum USD value for fresh wallet alerts (default: $10,000)",
        inline=False
    )
    embed.add_field(
        name="/track <wallet> [label]",
        value="Add a wallet to track (any activity will be alerted)",
        inline=False
    )
    embed.add_field(
        name="/untrack <wallet>",
        value="Remove a wallet from tracking",
        inline=False
    )
    embed.add_field(
        name="/list",
        value="Show current settings and tracked wallets",
        inline=False
    )
    embed.add_field(
        name="/pause",
        value="Pause all alerts",
        inline=False
    )
    embed.add_field(
        name="/resume",
        value="Resume alerts",
        inline=False
    )
    
    embed.add_field(
        name="/positions",
        value="View positions of all tracked wallets",
        inline=False
    )
    embed.add_field(
        name="/rename <wallet> <new_name>",
        value="Rename a tracked wallet",
        inline=False
    )
    embed.add_field(
        name="/trending",
        value="Show top 10 trending markets by 24h volume",
        inline=False
    )
    embed.add_field(
        name="/sports_trending",
        value="Show top 10 trending sports markets by 24h volume",
        inline=False
    )
    embed.add_field(
        name="/search <keywords>",
        value="Search markets and view orderbooks (e.g., /search knicks)",
        inline=False
    )
    
    embed.set_footer(text="Administrator permissions required for configuration commands")
    
    await interaction.response.send_message(embed=embed, ephemeral=True)


class WalletPositionButton(Button):
    def __init__(self, wallet_address: str, wallet_label: str, row: int = 0):
        label = wallet_label[:20] if wallet_label else f"{wallet_address[:6]}...{wallet_address[-4:]}"
        super().__init__(label=label, style=discord.ButtonStyle.primary, row=row)
        self.wallet_address = wallet_address
        self.wallet_label = wallet_label
    
    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        positions = await polymarket_client.get_wallet_positions(self.wallet_address)
        usdc_balance = await polymarket_client.get_wallet_usdc_balance(self.wallet_address)
        embed = create_wallet_positions_embed(
            wallet_address=self.wallet_address,
            wallet_label=self.wallet_label,
            positions=positions,
            usdc_balance=usdc_balance
        )
        
        await interaction.followup.send(embed=embed, ephemeral=True)


@bot.tree.command(name="positions", description="View positions of all tracked wallets")
async def positions(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    
    session = get_session()
    try:
        tracked = session.query(TrackedWallet).filter_by(guild_id=interaction.guild_id).all()
        
        if not tracked:
            await interaction.followup.send(
                "No wallets are being tracked. Use `/track` to add wallets.",
                ephemeral=True
            )
            return
        
        positions_data = {}
        balance_data = {}
        for wallet in tracked:
            wallet_positions = await polymarket_client.get_wallet_positions(wallet.wallet_address)
            positions_data[wallet.wallet_address] = wallet_positions
            usdc_balance = await polymarket_client.get_wallet_usdc_balance(wallet.wallet_address)
            balance_data[wallet.wallet_address] = usdc_balance
        
        embed = create_positions_overview_embed(tracked, positions_data, balance_data)
        
        view = View(timeout=300)
        for i, wallet in enumerate(tracked[:5]):
            label = wallet.label or f"{wallet.wallet_address[:6]}...{wallet.wallet_address[-4:]}"
            view.add_item(WalletPositionButton(wallet.wallet_address, label, row=i // 3))
        
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
    finally:
        session.close()


@bot.tree.command(name="rename", description="Rename a tracked wallet")
@app_commands.describe(
    wallet="The wallet address to rename (0x...)",
    name="New label for this wallet"
)
@app_commands.checks.has_permissions(administrator=True)
async def rename(interaction: discord.Interaction, wallet: str, name: str):
    wallet = wallet.strip().lower()
    
    session = get_session()
    try:
        tracked = session.query(TrackedWallet).filter_by(
            guild_id=interaction.guild_id,
            wallet_address=wallet
        ).first()
        
        if not tracked:
            await interaction.response.send_message(
                f"Wallet `{wallet[:6]}...{wallet[-4:]}` is not being tracked",
                ephemeral=True
            )
            return
        
        old_label = tracked.label or "None"
        tracked.label = name
        session.commit()
        
        await interaction.response.send_message(
            f"Renamed wallet `{wallet[:6]}...{wallet[-4:]}` from '{old_label}' to '{name}'",
            ephemeral=True
        )
    finally:
        session.close()


@tasks.loop(seconds=15)
async def monitor_loop():
    try:
        await polymarket_client.refresh_market_cache()
        await polymarket_client.get_top_traders(limit=25)
        
        session = get_session()
        try:
            configs = session.query(ServerConfig).filter(
                ServerConfig.is_paused == False
            ).all()
            
            configs = [c for c in configs if c.alert_channel_id or c.sports_channel_id or c.top_trader_channel_id]
            
            if not configs:
                return
            
            min_threshold = min([c.whale_threshold for c in configs] + [c.sports_threshold or 5000.0 for c in configs])
            
            all_tracked = session.query(TrackedWallet).all()
            tracked_by_guild = {}
            unique_tracked_addresses = set()
            for tw in all_tracked:
                if tw.guild_id not in tracked_by_guild:
                    tracked_by_guild[tw.guild_id] = {}
                tracked_by_guild[tw.guild_id][tw.wallet_address] = tw
                unique_tracked_addresses.add(tw.wallet_address)
            
            global_trades = await polymarket_client.get_recent_trades(limit=200)
            
            if global_trades:
                trade_values = [polymarket_client.calculate_trade_value(t) for t in global_trades]
                max_val = max(trade_values) if trade_values else 0
                above_threshold = [v for v in trade_values if v >= min_threshold]
                if above_threshold:
                    print(f"[Monitor] Found {len(above_threshold)} trades >= ${min_threshold:,.0f} (max: ${max_val:,.0f}) out of {len(global_trades)} total")
            
            tracked_trades = []
            for wallet_addr in unique_tracked_addresses:
                wallet_trades = await polymarket_client.get_wallet_trades(wallet_addr, limit=10)
                if wallet_trades:
                    tracked_trades.extend(wallet_trades)
            
            all_trades = global_trades or []
            seen_keys = set()
            for trade in tracked_trades:
                key = polymarket_client.get_unique_trade_id(trade)
                if key not in seen_keys:
                    all_trades.append(trade)
                    seen_keys.add(key)
            
            processed_wallets_this_batch = set()
            
            for trade in all_trades:
                unique_key = polymarket_client.get_unique_trade_id(trade)
                
                if not unique_key or len(unique_key) < 10:
                    continue
                
                seen = session.query(SeenTransaction).filter_by(tx_hash=unique_key[:66]).first()
                if seen:
                    continue
                
                session.add(SeenTransaction(tx_hash=unique_key[:66]))
                
                value = polymarket_client.calculate_trade_value(trade)
                wallet = polymarket_client.get_wallet_from_trade(trade)
                
                if not wallet:
                    continue
                
                wallet = wallet.lower()
                market_title = polymarket_client.get_market_title(trade)
                market_url = polymarket_client.get_market_url(trade)
                event_slug = polymarket_client.get_event_slug(trade)
                
                price = float(trade.get('price', 0) or 0)
                side = trade.get('side', '').lower()
                if side == 'sell' and price > 0.99:
                    continue
                
                is_fresh = False
                if wallet not in processed_wallets_this_batch:
                    wallet_activity = session.query(WalletActivity).filter_by(wallet_address=wallet).first()
                    if wallet_activity is None:
                        has_history = await polymarket_client.has_prior_activity(wallet)
                        if has_history is False:
                            is_fresh = True
                        session.add(WalletActivity(wallet_address=wallet, transaction_count=1))
                    else:
                        wallet_activity.transaction_count += 1
                    processed_wallets_this_batch.add(wallet)
                
                is_sports = polymarket_client.is_sports_market(trade)
                
                for config in configs:
                    tracked_addresses = tracked_by_guild.get(config.guild_id, {})
                    button_view = create_trade_button_view(event_slug, market_url)
                    
                    trade_timestamp = trade.get('timestamp', 0)
                    trade_time = datetime.utcfromtimestamp(trade_timestamp) if trade_timestamp else None
                    
                    def is_trade_after_tracking(trade_dt, added_dt):
                        if not trade_dt or not added_dt:
                            return True
                        trade_naive = trade_dt.replace(tzinfo=None) if hasattr(trade_dt, 'tzinfo') and trade_dt.tzinfo else trade_dt
                        added_naive = added_dt.replace(tzinfo=None) if hasattr(added_dt, 'tzinfo') and added_dt.tzinfo else added_dt
                        return trade_naive >= added_naive
                    
                    top_trader_info = polymarket_client.is_top_trader(wallet)
                    
                    if is_sports:
                        if top_trader_info and config.top_trader_channel_id:
                            top_channel = bot.get_channel(config.top_trader_channel_id)
                            if top_channel:
                                embed = create_top_trader_alert_embed(
                                    trade=trade,
                                    value_usd=value,
                                    market_title=market_title,
                                    wallet_address=wallet,
                                    market_url=market_url,
                                    trader_info=top_trader_info
                                )
                                try:
                                    await top_channel.send(embed=embed, view=button_view)
                                except Exception as e:
                                    print(f"Error sending sports top trader alert: {e}")
                        
                        sports_channel = bot.get_channel(config.sports_channel_id) if config.sports_channel_id else None
                        if sports_channel:
                            if wallet in tracked_addresses:
                                tw = tracked_addresses[wallet]
                                if not is_trade_after_tracking(trade_time, tw.added_at):
                                    continue
                                wallet_stats = await polymarket_client.get_wallet_pnl_stats(wallet)
                                embed = create_custom_wallet_alert_embed(
                                    trade=trade,
                                    value_usd=value,
                                    market_title=market_title,
                                    wallet_address=wallet,
                                    wallet_label=tw.label,
                                    market_url=market_url,
                                    pnl=wallet_stats.get('pnl'),
                                    volume=wallet_stats.get('volume'),
                                    rank=wallet_stats.get('rank')
                                )
                                try:
                                    await sports_channel.send(embed=embed, view=button_view)
                                except Exception as e:
                                    print(f"Error sending sports tracked wallet alert: {e}")
                            elif is_fresh and value >= (config.sports_threshold or 5000.0):
                                embed = create_fresh_wallet_alert_embed(
                                    trade=trade,
                                    value_usd=value,
                                    market_title=market_title,
                                    wallet_address=wallet,
                                    market_url=market_url
                                )
                                try:
                                    await sports_channel.send(embed=embed, view=button_view)
                                except Exception as e:
                                    print(f"Error sending sports fresh wallet alert: {e}")
                            elif value >= (config.sports_threshold or 5000.0):
                                embed = create_whale_alert_embed(
                                    trade=trade,
                                    value_usd=value,
                                    market_title=market_title,
                                    wallet_address=wallet,
                                    market_url=market_url
                                )
                                try:
                                    await sports_channel.send(embed=embed, view=button_view)
                                except Exception as e:
                                    print(f"Error sending sports whale alert: {e}")
                    else:
                        if wallet in tracked_addresses:
                            tracked_channel_id = config.tracked_wallet_channel_id or config.alert_channel_id
                            tracked_channel = bot.get_channel(tracked_channel_id) if tracked_channel_id else None
                            if tracked_channel:
                                tw = tracked_addresses[wallet]
                                if not is_trade_after_tracking(trade_time, tw.added_at):
                                    continue
                                wallet_stats = await polymarket_client.get_wallet_pnl_stats(wallet)
                                embed = create_custom_wallet_alert_embed(
                                    trade=trade,
                                    value_usd=value,
                                    market_title=market_title,
                                    wallet_address=wallet,
                                    wallet_label=tw.label,
                                    market_url=market_url,
                                    pnl=wallet_stats.get('pnl'),
                                    volume=wallet_stats.get('volume'),
                                    rank=wallet_stats.get('rank')
                                )
                                try:
                                    await tracked_channel.send(embed=embed, view=button_view)
                                except Exception as e:
                                    print(f"Error sending tracked wallet alert: {e}")
                        
                        if top_trader_info and config.top_trader_channel_id:
                            top_channel = bot.get_channel(config.top_trader_channel_id)
                            if top_channel:
                                embed = create_top_trader_alert_embed(
                                    trade=trade,
                                    value_usd=value,
                                    market_title=market_title,
                                    wallet_address=wallet,
                                    market_url=market_url,
                                    trader_info=top_trader_info
                                )
                                try:
                                    await top_channel.send(embed=embed, view=button_view)
                                except Exception as e:
                                    print(f"Error sending top trader alert: {e}")
                        
                        if is_fresh and value >= (config.fresh_wallet_threshold or 10000.0):
                            fresh_channel_id = config.fresh_wallet_channel_id or config.alert_channel_id
                            fresh_channel = bot.get_channel(fresh_channel_id) if fresh_channel_id else None
                            if fresh_channel:
                                embed = create_fresh_wallet_alert_embed(
                                    trade=trade,
                                    value_usd=value,
                                    market_title=market_title,
                                    wallet_address=wallet,
                                    market_url=market_url
                                )
                                try:
                                    await fresh_channel.send(embed=embed, view=button_view)
                                except Exception as e:
                                    print(f"Error sending fresh wallet alert: {e}")
                        
                        elif value >= config.whale_threshold:
                            whale_channel_id = config.whale_channel_id or config.alert_channel_id
                            whale_channel = bot.get_channel(whale_channel_id) if whale_channel_id else None
                            if whale_channel:
                                embed = create_whale_alert_embed(
                                    trade=trade,
                                    value_usd=value,
                                    market_title=market_title,
                                    wallet_address=wallet,
                                    market_url=market_url
                                )
                                try:
                                    await whale_channel.send(embed=embed, view=button_view)
                                except Exception as e:
                                    print(f"Error sending whale alert: {e}")
            
            session.commit()
        finally:
            session.close()
            
    except Exception as e:
        print(f"Error in monitor loop: {e}")


@monitor_loop.before_loop
async def before_monitor():
    await bot.wait_until_ready()


@tasks.loop(minutes=5)
async def volatility_loop():
    try:
        from datetime import timedelta
        session = get_session()
        try:
            configs = session.query(ServerConfig).filter(
                ServerConfig.volatility_channel_id.isnot(None),
                ServerConfig.is_paused == False
            ).all()
            
            if not configs:
                return
            
            markets = await polymarket_client.get_active_markets_prices(limit=200)
            now = datetime.utcnow()
            
            for market in markets:
                condition_id = market['condition_id']
                current_price = market['yes_price']
                title = market['title']
                slug = market['slug']
                volume = market['volume']
                
                session.add(PriceSnapshot(
                    condition_id=condition_id,
                    title=title,
                    slug=slug,
                    yes_price=current_price,
                    volume=volume
                ))
            
            session.commit()
            
            one_hour_ago = now - timedelta(minutes=60)
            cooldown_time = now - timedelta(minutes=120)
            
            for market in markets:
                condition_id = market['condition_id']
                current_price = market['yes_price']
                
                old_snapshot = session.query(PriceSnapshot).filter(
                    PriceSnapshot.condition_id == condition_id,
                    PriceSnapshot.captured_at <= one_hour_ago
                ).order_by(PriceSnapshot.captured_at.desc()).first()
                
                if not old_snapshot:
                    continue
                
                old_price = old_snapshot.yes_price
                if old_price <= 0.01 or old_price >= 0.99:
                    continue
                
                price_change_pct = ((current_price - old_price) / old_price) * 100
                
                if abs(price_change_pct) < 20.0:
                    continue
                
                recent_alert = session.query(VolatilityAlert).filter(
                    VolatilityAlert.condition_id == condition_id,
                    VolatilityAlert.alerted_at >= cooldown_time
                ).first()
                
                if recent_alert:
                    continue
                
                session.add(VolatilityAlert(
                    condition_id=condition_id,
                    price_change=price_change_pct
                ))
                
                for config in configs:
                    channel = bot.get_channel(config.volatility_channel_id)
                    if not channel:
                        continue
                    
                    embed, market_url = create_volatility_alert_embed(
                        market_title=market['title'],
                        slug=market['slug'],
                        old_price=old_price,
                        new_price=current_price,
                        price_change=price_change_pct,
                        time_window_minutes=60
                    )
                    event_slug = polymarket_client.get_event_slug_by_condition(condition_id, market['slug'])
                    button_view = create_trade_button_view(event_slug, market_url)
                    
                    try:
                        await channel.send(embed=embed, view=button_view)
                    except Exception as e:
                        print(f"Error sending volatility alert: {e}")
            
            session.commit()
        finally:
            session.close()
    except Exception as e:
        print(f"Error in volatility loop: {e}")


@volatility_loop.before_loop
async def before_volatility():
    await bot.wait_until_ready()


@tasks.loop(hours=1)
async def cleanup_loop():
    try:
        from datetime import timedelta
        session = get_session()
        try:
            cutoff = datetime.utcnow() - timedelta(hours=3)
            deleted = session.query(PriceSnapshot).filter(
                PriceSnapshot.captured_at < cutoff
            ).delete()
            
            alert_cutoff = datetime.utcnow() - timedelta(hours=24)
            session.query(VolatilityAlert).filter(
                VolatilityAlert.alerted_at < alert_cutoff
            ).delete()
            
            session.commit()
            if deleted > 0:
                print(f"Cleaned up {deleted} old price snapshots")
        finally:
            session.close()
    except Exception as e:
        print(f"Error in cleanup loop: {e}")


@cleanup_loop.before_loop
async def before_cleanup():
    await bot.wait_until_ready()


@setup.error
@threshold.error
@track.error
@untrack.error
@pause.error
@resume.error
@rename.error
@volatility.error
async def command_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message(
            "You need administrator permissions to use this command.",
            ephemeral=True
        )
    else:
        print(f"Command error: {error}")
        await interaction.response.send_message(
            "An error occurred. Please try again.",
            ephemeral=True
        )


def main():
    token = os.environ.get('DISCORD_BOT_TOKEN')
    if not token:
        print("ERROR: DISCORD_BOT_TOKEN not found in environment variables")
        print("Please set your Discord bot token in the Secrets tab")
        return
    
    print("Starting Polymarket Discord Bot...")
    bot.run(token)


if __name__ == "__main__":
    main()
