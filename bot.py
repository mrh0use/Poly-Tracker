import os
import discord
from discord import app_commands
from discord.ext import commands, tasks
from discord.ui import View, Button
import asyncio
from datetime import datetime, timedelta
from typing import Optional, Dict, Deque
from collections import deque
from aiohttp import web
import time

from sqlalchemy import text
from database import init_db, get_session, ServerConfig, TrackedWallet, SeenTransaction, WalletActivity, PriceSnapshot, VolatilityAlert
from polymarket_client import polymarket_client, PolymarketWebSocket
from alerts import (
    create_whale_alert_embed,
    create_fresh_wallet_alert_embed,
    create_custom_wallet_alert_embed,
    create_top_trader_alert_embed,
    create_bonds_alert_embed,
    create_settings_embed,
    create_trade_button_view,
    create_positions_overview_embed,
    create_wallet_positions_embed,
    create_volatility_alert_embed
)

# Server config cache to reduce database queries
_server_config_cache = []
_server_config_cache_time = 0
_SERVER_CONFIG_CACHE_TTL = 300  # Refresh every 5 minutes

def get_cached_server_configs():
    """Get server configs from cache, refreshing if stale."""
    global _server_config_cache, _server_config_cache_time
    now = time.time()
    if now - _server_config_cache_time > _SERVER_CONFIG_CACHE_TTL:
        session = get_session()
        try:
            _server_config_cache = session.query(ServerConfig).all()
            _server_config_cache_time = now
        finally:
            session.close()
    return _server_config_cache

def invalidate_server_config_cache():
    """Invalidate cache when configs are updated."""
    global _server_config_cache_time
    _server_config_cache_time = 0

# Tracked wallet cache to avoid DB queries on every trade
_tracked_wallet_cache = {}  # {wallet_address: {guild_id: TrackedWallet}}
_tracked_wallet_set = set()  # Quick lookup set of all tracked addresses
_tracked_wallet_cache_time = 0
_TRACKED_WALLET_CACHE_TTL = 300  # Refresh every 5 minutes

def get_cached_tracked_wallets():
    """Get tracked wallets from cache, refreshing if stale. Returns (set of addresses, dict by guild)."""
    global _tracked_wallet_cache, _tracked_wallet_set, _tracked_wallet_cache_time
    now = time.time()
    if now - _tracked_wallet_cache_time > _TRACKED_WALLET_CACHE_TTL:
        session = get_session()
        try:
            all_tracked = session.query(TrackedWallet).all()
            _tracked_wallet_cache = {}
            _tracked_wallet_set = set()
            for tw in all_tracked:
                addr = tw.wallet_address.lower()
                _tracked_wallet_set.add(addr)
                if tw.guild_id not in _tracked_wallet_cache:
                    _tracked_wallet_cache[tw.guild_id] = {}
                _tracked_wallet_cache[tw.guild_id][addr] = tw
            _tracked_wallet_cache_time = now
        finally:
            session.close()
    return _tracked_wallet_set, _tracked_wallet_cache

def invalidate_tracked_wallet_cache():
    """Invalidate cache when tracked wallets are updated."""
    global _tracked_wallet_cache_time
    _tracked_wallet_cache_time = 0


class VolatilityWindowManager:
    """In-memory manager for real-time volatility detection using multiple rolling time windows."""
    
    def __init__(self, windows_minutes: list = None, max_entries_per_market: int = 5000, warmup_minutes: int = 5):
        self.windows_minutes = windows_minutes or [5, 15, 60]
        self.max_entries = max_entries_per_market
        self._price_windows: Dict[str, Deque] = {}
        self._market_metadata: Dict[str, dict] = {}
        self._alert_cooldowns: Dict[str, datetime] = {}
        self._cooldown_minutes = 15
        self._use_absolute_change = True
        self._startup_time = datetime.utcnow()
        self._warmup_minutes = warmup_minutes
    
    def reset_warmup(self):
        """Reset the warm-up timer (call on WebSocket reconnection to prevent false alerts)."""
        self._startup_time = datetime.utcnow()
        print(f"[VOLATILITY] Warm-up timer reset - suppressing alerts for {self._warmup_minutes} minutes", flush=True)
    
    def record_price(self, condition_id: str, price: float, title: str = "", slug: str = ""):
        """Record a price point for a market. Called on every trade."""
        if condition_id not in self._price_windows:
            self._price_windows[condition_id] = deque(maxlen=self.max_entries)
            self._market_metadata[condition_id] = {}
        
        now = datetime.utcnow()
        self._price_windows[condition_id].append((now, price))
        
        if title:
            self._market_metadata[condition_id]['title'] = title
        if slug:
            self._market_metadata[condition_id]['slug'] = slug
    
    def seed_price(self, condition_id: str, price: float, title: str = "", slug: str = ""):
        """Seed initial price for a market (called on startup)."""
        self.record_price(condition_id, price, title, slug)
    
    def _get_oldest_in_window(self, window: Deque, window_start: datetime) -> Optional[tuple]:
        """Get the oldest price entry within the time window (>= window_start)."""
        if not window:
            return None
        
        for timestamp, price in window:
            if timestamp >= window_start:
                return (timestamp, price)
        return None
    
    def _get_price_before_time(self, window: Deque, target_time: datetime) -> Optional[tuple]:
        """Get the newest price entry before target_time (<= target_time)."""
        if not window:
            return None
        
        best = None
        for timestamp, price in window:
            if timestamp <= target_time:
                best = (timestamp, price)
            else:
                break
        return best
    
    def check_volatility(self, condition_id: str, guild_id: int, threshold_pct: float = 5.0) -> Optional[dict]:
        """
        Check if market has a price swing exceeding threshold across ANY timeframe.
        Returns the shortest timeframe that triggers (most urgent).
        Uses ABSOLUTE percentage point change.
        
        For each window, compares current price against:
        1. First: any price from before the window start (traditional swing detection)
        2. Fallback: oldest price within the window (for rapid swings)
        """
        now = datetime.utcnow()
        
        if now < self._startup_time + timedelta(minutes=self._warmup_minutes):
            return None
        
        if condition_id not in self._price_windows:
            return None
        
        window = self._price_windows[condition_id]
        if len(window) < 2:
            return None
        
        current_time, current_price = window[-1]
        
        if current_price <= 0.02 or current_price >= 0.98:
            return None
        
        for window_minutes in sorted(self.windows_minutes):
            window_start = now - timedelta(minutes=window_minutes)
            
            old_entry = self._get_price_before_time(window, window_start)
            if old_entry is None:
                old_entry = self._get_oldest_in_window(window, window_start)
            
            if old_entry is None:
                continue
            
            old_time, old_price = old_entry
            
            if old_price <= 0.02 or old_price >= 0.98:
                continue
            
            price_change_pct = (current_price - old_price) * 100
            
            if abs(price_change_pct) < threshold_pct:
                continue
            
            cooldown_key = f"{condition_id}:{guild_id}:{window_minutes}"
            if cooldown_key in self._alert_cooldowns:
                if now < self._alert_cooldowns[cooldown_key]:
                    continue
            
            self._alert_cooldowns[cooldown_key] = now + timedelta(minutes=self._cooldown_minutes)
            
            metadata = self._market_metadata.get(condition_id, {})
            return {
                'condition_id': condition_id,
                'title': metadata.get('title', 'Unknown Market'),
                'slug': metadata.get('slug', ''),
                'old_price': old_price,
                'new_price': current_price,
                'price_change_pct': price_change_pct,
                'time_window_minutes': window_minutes
            }
        
        return None
    
    def cleanup_old_data(self):
        """Remove price data older than largest window + buffer."""
        max_window = max(self.windows_minutes)
        cutoff = datetime.utcnow() - timedelta(minutes=max_window + 15)
        
        for condition_id, window in list(self._price_windows.items()):
            while len(window) > 2 and window[0][0] < cutoff:
                window.popleft()
        
        now = datetime.utcnow()
        expired = [k for k, v in self._alert_cooldowns.items() if v < now]
        for k in expired:
            del self._alert_cooldowns[k]
    
    def get_stats(self) -> dict:
        """Get stats for debugging."""
        total_entries = sum(len(w) for w in self._price_windows.values())
        active_cooldowns = len([k for k, v in self._alert_cooldowns.items() if v > datetime.utcnow()])
        return {
            'markets_tracked': len(self._price_windows),
            'total_price_entries': total_entries,
            'active_cooldowns': active_cooldowns,
            'timeframes': self.windows_minutes
        }


volatility_manager = VolatilityWindowManager(windows_minutes=[5, 15, 60])


class PolymarketBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.guilds = True
        
        super().__init__(command_prefix="!", intents=intents)
        self.synced = False
        self.websocket_started = False
    
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
            print("Monitor loop started (backup for tracked wallets)")
        
        # Seed initial price snapshots immediately so volatility can work faster
        try:
            await seed_initial_prices()
            print("In-memory volatility tracker seeded")
        except Exception as e:
            print(f"Error seeding initial snapshots: {e}")
        
        if not volatility_loop.is_running():
            volatility_loop.start()
            print("Volatility loop started")
        
        if not cleanup_loop.is_running():
            cleanup_loop.start()
            print("Cleanup loop started")
        
        await polymarket_client.fetch_sports_tags()
        print("Sports tags loaded")
        
        if not self.websocket_started:
            self.websocket_started = True
            asyncio.create_task(start_websocket())
            print("WebSocket task scheduled")
        
        # Log all server configs at startup
        session = get_session()
        try:
            all_configs = session.query(ServerConfig).all()
            print(f"[STARTUP] Found {len(all_configs)} server configs:", flush=True)
            for c in all_configs:
                print(f"[STARTUP] Guild {c.guild_id}: whale=${c.whale_threshold:,.0f}, fresh=${c.fresh_wallet_threshold or 10000:,.0f}, sports=${c.sports_threshold or 5000:,.0f}, paused={c.is_paused}", flush=True)
        finally:
            session.close()
    
    async def on_guild_join(self, guild):
        """Sync slash commands when bot joins a new server."""
        try:
            await self.tree.sync(guild=guild)
            print(f"[SYNC] Synced commands to new guild: {guild.name} ({guild.id})", flush=True)
        except Exception as e:
            print(f"[SYNC ERROR] Failed to sync to {guild.name}: {e}", flush=True)


bot = PolymarketBot()


async def get_or_fetch_channel(channel_id):
    """Get channel from cache or fetch from API if not cached."""
    if not channel_id:
        return None
    channel = bot.get_channel(channel_id)
    if channel:
        return channel
    try:
        channel = await bot.fetch_channel(channel_id)
        print(f"[CHANNEL] Fetched channel {channel_id} from API (was not in cache)", flush=True)
        return channel
    except discord.NotFound:
        print(f"[CHANNEL] Channel {channel_id} not found", flush=True)
        return None
    except discord.Forbidden:
        print(f"[CHANNEL] Bot lacks access to channel {channel_id}", flush=True)
        return None
    except Exception as e:
        print(f"[CHANNEL] Error fetching channel {channel_id}: {e}", flush=True)
        return None


@bot.tree.command(name="setup", description="Configure all alert channels at once")
@app_commands.describe(
    whale="Channel for whale alerts ($10k+)",
    fresh_wallet="Channel for fresh wallet alerts",
    tracked_wallet="Channel for tracked wallet alerts",
    volatility="Channel for volatility alerts (20%+ swings)",
    sports="Channel for sports/esports alerts",
    top_trader="Channel for top 25 trader alerts",
    bonds="Channel for bond alerts (>=95% price markets)"
)
@app_commands.checks.has_permissions(administrator=True)
async def setup(
    interaction: discord.Interaction,
    whale: Optional[discord.TextChannel] = None,
    fresh_wallet: Optional[discord.TextChannel] = None,
    tracked_wallet: Optional[discord.TextChannel] = None,
    volatility: Optional[discord.TextChannel] = None,
    sports: Optional[discord.TextChannel] = None,
    top_trader: Optional[discord.TextChannel] = None,
    bonds: Optional[discord.TextChannel] = None
):
    if not any([whale, fresh_wallet, tracked_wallet, volatility, sports, top_trader, bonds]):
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
        if bonds:
            config.bonds_channel_id = bonds.id
            configured.append(f"Bonds: {bonds.mention}")
        
        session.commit()
        invalidate_server_config_cache()
        
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
        invalidate_server_config_cache()
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
        invalidate_server_config_cache()
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
        invalidate_server_config_cache()
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
        invalidate_server_config_cache()
        print(f"[CMD] Threshold updated to ${amount:,.0f} for guild {interaction.guild_id}", flush=True)
        
        await interaction.response.send_message(
            f"Whale alert threshold set to ${amount:,.0f}",
            ephemeral=True
        )
    except Exception as e:
        print(f"[CMD ERROR] threshold command failed: {e}", flush=True)
        if not interaction.response.is_done():
            await interaction.response.send_message(
                f"Error saving threshold: {str(e)}",
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
        invalidate_tracked_wallet_cache()
        
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
                invalidate_tracked_wallet_cache()
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
                stats = await asyncio.wait_for(
                    polymarket_client.get_wallet_pnl_stats(w.wallet_address),
                    timeout=3.0
                )
                wallet_stats[w.wallet_address.lower()] = stats
            except asyncio.TimeoutError:
                print(f"[CMD] PNL stats timeout for {w.wallet_address[:10]}...", flush=True)
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
        
        top_trader_channel_name = None
        if config.top_trader_channel_id:
            top_ch = interaction.guild.get_channel(config.top_trader_channel_id)
            top_trader_channel_name = top_ch.name if top_ch else None
        
        bonds_channel_name = None
        if config.bonds_channel_id:
            bonds_ch = interaction.guild.get_channel(config.bonds_channel_id)
            bonds_channel_name = bonds_ch.name if bonds_ch else None
        
        embed = create_settings_embed(
            guild_name=interaction.guild.name,
            channel_name=channel_name,
            whale_threshold=config.whale_threshold,
            fresh_wallet_threshold=config.fresh_wallet_threshold,
            is_paused=config.is_paused,
            tracked_wallets=tracked,
            volatility_channel_name=volatility_channel_name,
            volatility_threshold=config.volatility_threshold or 5.0,
            sports_channel_name=sports_channel_name,
            sports_threshold=config.sports_threshold or 5000.0,
            wallet_stats=wallet_stats,
            whale_channel_name=whale_channel_name,
            fresh_wallet_channel_name=fresh_wallet_channel_name,
            tracked_wallet_channel_name=tracked_wallet_channel_name,
            top_trader_channel_name=top_trader_channel_name,
            top_trader_threshold=config.top_trader_threshold or 2500.0,
            bonds_channel_name=bonds_channel_name,
            volatility_window_minutes=config.volatility_window_minutes or 15
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
        invalidate_server_config_cache()
        
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
        invalidate_server_config_cache()
        
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
        invalidate_server_config_cache()
        
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
        invalidate_server_config_cache()
        
        await interaction.response.send_message(
            f"Sports market alerts will be sent to {channel.mention}. All sports/esports trading activity will be routed here.",
            ephemeral=True
        )
    finally:
        session.close()


@bot.tree.command(name="bonds", description="Set the channel for bond alerts (>=95% price markets)")
@app_commands.describe(channel="The channel to send bond alerts to")
@app_commands.checks.has_permissions(administrator=True)
async def bonds(interaction: discord.Interaction, channel: discord.TextChannel):
    session = get_session()
    try:
        config = session.query(ServerConfig).filter_by(guild_id=interaction.guild_id).first()
        if not config:
            config = ServerConfig(guild_id=interaction.guild_id)
            session.add(config)
        
        config.bonds_channel_id = channel.id
        session.commit()
        invalidate_server_config_cache()
        
        await interaction.response.send_message(
            f"Bond alerts will be sent to {channel.mention}. Trades on markets with >=95% price ($5k+) will be routed here.",
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
        invalidate_server_config_cache()
        print(f"[CMD] Sports threshold updated to ${amount:,.0f} for guild {interaction.guild_id}", flush=True)
        
        await interaction.response.send_message(
            f"Sports alert threshold set to ${amount:,.0f}",
            ephemeral=True
        )
    except Exception as e:
        print(f"[CMD ERROR] sports_threshold command failed: {e}", flush=True)
        if not interaction.response.is_done():
            await interaction.response.send_message(
                f"Error saving threshold: {str(e)}",
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
        invalidate_server_config_cache()
        print(f"[CMD] Fresh wallet threshold updated to ${amount:,.0f} for guild {interaction.guild_id}", flush=True)
        
        await interaction.response.send_message(
            f"Fresh wallet alert threshold set to ${amount:,.0f}",
            ephemeral=True
        )
    except Exception as e:
        print(f"[CMD ERROR] fresh_wallet_threshold command failed: {e}", flush=True)
        if not interaction.response.is_done():
            await interaction.response.send_message(
                f"Error saving threshold: {str(e)}",
                ephemeral=True
            )
    finally:
        session.close()


@bot.tree.command(name="volatility_threshold", description="Set the minimum percentage swing for volatility alerts")
@app_commands.describe(percentage="Minimum percentage swing for volatility alerts (e.g., 20 for 20%)")
@app_commands.checks.has_permissions(administrator=True)
async def volatility_threshold_cmd(interaction: discord.Interaction, percentage: float):
    if percentage < 5:
        await interaction.response.send_message(
            "Volatility threshold must be at least 5%",
            ephemeral=True
        )
        return
    if percentage > 50:
        await interaction.response.send_message(
            "Volatility threshold cannot exceed 50%",
            ephemeral=True
        )
        return
    
    session = get_session()
    try:
        config = session.query(ServerConfig).filter_by(guild_id=interaction.guild_id).first()
        if not config:
            config = ServerConfig(guild_id=interaction.guild_id)
            session.add(config)
        
        config.volatility_threshold = percentage
        session.commit()
        invalidate_server_config_cache()
        print(f"[CMD] Volatility threshold updated to {percentage:.0f}% for guild {interaction.guild_id}", flush=True)
        
        await interaction.response.send_message(
            f"Volatility alert threshold set to {percentage:.0f}% price swing",
            ephemeral=True
        )
    except Exception as e:
        print(f"[CMD ERROR] volatility_threshold command failed: {e}", flush=True)
        if not interaction.response.is_done():
            await interaction.response.send_message(
                f"Error saving threshold: {str(e)}",
                ephemeral=True
            )
    finally:
        session.close()


@bot.tree.command(name="volatility_window", description="Set the time window for detecting volatility")
@app_commands.describe(minutes="Time window in minutes")
@app_commands.choices(minutes=[
    app_commands.Choice(name="5 minutes", value=5),
    app_commands.Choice(name="10 minutes", value=10),
    app_commands.Choice(name="15 minutes", value=15),
    app_commands.Choice(name="30 minutes", value=30),
    app_commands.Choice(name="60 minutes", value=60),
])
@app_commands.checks.has_permissions(administrator=True)
async def volatility_window_cmd(interaction: discord.Interaction, minutes: app_commands.Choice[int]):
    session = get_session()
    try:
        config = session.query(ServerConfig).filter_by(guild_id=interaction.guild_id).first()
        if not config:
            config = ServerConfig(guild_id=interaction.guild_id)
            session.add(config)
        
        config.volatility_window_minutes = minutes.value
        session.commit()
        invalidate_server_config_cache()
        print(f"[CMD] Volatility window updated to {minutes.value} minutes for guild {interaction.guild_id}", flush=True)
        
        await interaction.response.send_message(
            f"Volatility detection window set to {minutes.value} minutes",
            ephemeral=True
        )
    except Exception as e:
        print(f"[CMD ERROR] volatility_window command failed: {e}", flush=True)
        if not interaction.response.is_done():
            await interaction.response.send_message(
                f"Error saving window: {str(e)}",
                ephemeral=True
            )
    finally:
        session.close()


@bot.tree.command(name="volatility_category", description="Filter volatility alerts by market category")
@app_commands.describe(category="Category to filter by")
@app_commands.choices(category=[
    app_commands.Choice(name="All Markets", value="all"),
    app_commands.Choice(name="Sports/Esports", value="sports"),
    app_commands.Choice(name="Crypto", value="crypto"),
    app_commands.Choice(name="Politics", value="politics"),
    app_commands.Choice(name="Entertainment", value="entertainment"),
])
@app_commands.checks.has_permissions(administrator=True)
async def volatility_category_cmd(interaction: discord.Interaction, category: app_commands.Choice[str]):
    session = get_session()
    try:
        config = session.query(ServerConfig).filter_by(guild_id=interaction.guild_id).first()
        if not config:
            config = ServerConfig(guild_id=interaction.guild_id)
            session.add(config)
        
        config.volatility_category = category.value
        session.commit()
        invalidate_server_config_cache()
        print(f"[CMD] Volatility category updated to {category.value} for guild {interaction.guild_id}", flush=True)
        
        await interaction.response.send_message(
            f"Volatility alerts will only show **{category.name}** markets",
            ephemeral=True
        )
    except Exception as e:
        print(f"[CMD ERROR] volatility_category command failed: {e}", flush=True)
        if not interaction.response.is_done():
            await interaction.response.send_message(
                f"Error saving category filter: {str(e)}",
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
        invalidate_server_config_cache()
        
        await interaction.response.send_message(
            f"Top 25 trader alerts will be sent to {channel.mention}. All trades from top 25 all-time profit leaders will be shown here.",
            ephemeral=True
        )
    finally:
        session.close()


@bot.tree.command(name="top_trader_threshold", description="Set the minimum USD value for top 25 trader alerts")
@app_commands.describe(amount="Minimum trade amount in USD (e.g., 5000 for $5k)")
@app_commands.checks.has_permissions(administrator=True)
async def top_trader_threshold_cmd(interaction: discord.Interaction, amount: float):
    if amount < 0:
        await interaction.response.send_message("Threshold must be a positive number.", ephemeral=True)
        return
    
    session = get_session()
    try:
        config = session.query(ServerConfig).filter_by(guild_id=interaction.guild_id).first()
        if not config:
            config = ServerConfig(guild_id=interaction.guild_id)
            session.add(config)
        
        config.top_trader_threshold = amount
        session.commit()
        invalidate_server_config_cache()
        print(f"[CMD] Top trader threshold updated to ${amount:,.0f} for guild {interaction.guild_id}", flush=True)
        
        await interaction.response.send_message(
            f"Top 25 trader alert threshold set to ${amount:,.0f}",
            ephemeral=True
        )
    except Exception as e:
        print(f"[CMD ERROR] top_trader_threshold command failed: {e}", flush=True)
        if not interaction.response.is_done():
            await interaction.response.send_message(
                f"Error saving threshold: {str(e)}",
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
        title="Onsight Alerts - Commands",
        color=0x4ECDC4
    )
    
    embed.add_field(
        name="Setup & Channels",
        value=(
            "`/setup` - Configure all channels at once\n"
            "`/whale_channel` `/sports` `/volatility`\n"
            "`/fresh_wallet_channel` `/tracked_wallet_channel`\n"
            "`/top_trader_channel` `/bonds`"
        ),
        inline=False
    )
    
    embed.add_field(
        name="Thresholds",
        value=(
            "`/threshold` - Whale alerts ($10k)\n"
            "`/sports_threshold` - Sports ($5k)\n"
            "`/fresh_wallet_threshold` - Fresh wallets ($10k)\n"
            "`/top_trader_threshold` - Top 25 ($2.5k)\n"
            "`/volatility_threshold` - Price swing (5pts)\n"
            "`/volatility_window` - Time window (15min)"
        ),
        inline=False
    )
    
    embed.add_field(
        name="Wallet Tracking",
        value=(
            "`/track <wallet> [label]` - Track a wallet\n"
            "`/untrack` - Stop tracking\n"
            "`/rename <wallet> <name>` - Rename wallet\n"
            "`/positions` - View tracked positions"
        ),
        inline=False
    )
    
    embed.add_field(
        name="Info & Control",
        value=(
            "`/list` - Current settings\n"
            "`/trending` `/sports_trending` - Hot markets\n"
            "`/search <query>` - Find markets\n"
            "`/pause` `/resume` - Toggle alerts"
        ),
        inline=False
    )
    
    embed.set_footer(text="Admin permissions required for config commands")
    
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
            all_configs = get_cached_server_configs()
            configs = [c for c in all_configs if not c.is_paused]
            
            configs = [c for c in configs if c.alert_channel_id or c.tracked_wallet_channel_id]
            
            if not configs:
                return
            
            all_tracked = session.query(TrackedWallet).all()
            if not all_tracked:
                return
            tracked_by_guild = {}
            unique_tracked_addresses = set()
            for tw in all_tracked:
                if tw.guild_id not in tracked_by_guild:
                    tracked_by_guild[tw.guild_id] = {}
                tracked_by_guild[tw.guild_id][tw.wallet_address] = tw
                unique_tracked_addresses.add(tw.wallet_address)
            
            tracked_trades = []
            for wallet_addr in unique_tracked_addresses:
                wallet_trades = await polymarket_client.get_wallet_trades(wallet_addr, limit=10)
                if wallet_trades:
                    tracked_trades.extend(wallet_trades)
            
            all_trades = tracked_trades
            
            processed_wallets_this_batch = set()
            
            new_trades_count = 0
            skipped_seen_count = 0
            alerts_sent = 0
            trades_above_threshold = 0
            
            for trade in all_trades:
                unique_key = polymarket_client.get_unique_trade_id(trade)
                
                if not unique_key or len(unique_key) < 10:
                    continue
                
                seen = session.query(SeenTransaction).filter_by(tx_hash=unique_key[:66]).first()
                if seen:
                    skipped_seen_count += 1
                    continue
                
                session.add(SeenTransaction(tx_hash=unique_key[:66]))
                new_trades_count += 1
                
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
                if side == 'sell':
                    continue
                
                is_fresh = False
                if wallet not in processed_wallets_this_batch:
                    wallet_activity = session.query(WalletActivity).filter_by(wallet_address=wallet).first()
                    if wallet_activity is None:
                        try:
                            has_history = await asyncio.wait_for(
                                polymarket_client.has_prior_activity(wallet),
                                timeout=2.0
                            )
                        except asyncio.TimeoutError:
                            has_history = True  # Assume not fresh if timeout
                            print(f"[MONITOR] Activity check timeout for {wallet[:10]}...", flush=True)
                        if has_history is False:
                            is_fresh = True
                        session.add(WalletActivity(wallet_address=wallet, transaction_count=1))
                    else:
                        wallet_activity.transaction_count += 1
                    processed_wallets_this_batch.add(wallet)
                
                is_sports = polymarket_client.is_sports_market(trade)
                is_bond = price >= 0.95
                
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
                    
                    if wallet in tracked_addresses:
                        tracked_channel_id = config.tracked_wallet_channel_id or config.alert_channel_id
                        print(f"[MONITOR] ALERT TRIGGERED: Tracked wallet ${value:,.0f}, attempting channel {tracked_channel_id}", flush=True)
                        tracked_channel = await get_or_fetch_channel(tracked_channel_id)
                        print(f"[MONITOR] Channel fetch result: {tracked_channel} (type: {type(tracked_channel).__name__ if tracked_channel else 'None'})", flush=True)
                        if tracked_channel:
                            tw = tracked_addresses[wallet]
                            if not is_trade_after_tracking(trade_time, tw.added_at):
                                continue
                            try:
                                wallet_stats = await asyncio.wait_for(
                                    polymarket_client.get_wallet_pnl_stats(wallet),
                                    timeout=3.0
                                )
                            except asyncio.TimeoutError:
                                wallet_stats = {}
                                print(f"[MONITOR] PNL stats timeout for {wallet[:10]}...", flush=True)
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
                                message = await tracked_channel.send(embed=embed, view=button_view)
                                print(f"[MONITOR] ✓ ALERT SENT: Tracked wallet ${value:,.0f} to channel {tracked_channel_id}, msg_id={message.id}", flush=True)
                            except discord.Forbidden as e:
                                print(f"[MONITOR] ✗ FORBIDDEN: Cannot send to channel {tracked_channel_id} - {e}", flush=True)
                            except discord.NotFound as e:
                                print(f"[MONITOR] ✗ NOT FOUND: Channel {tracked_channel_id} doesn't exist - {e}", flush=True)
                            except discord.HTTPException as e:
                                print(f"[MONITOR] ✗ HTTP ERROR: {e.status} {e.code} - {e.text}", flush=True)
                            except Exception as e:
                                print(f"[MONITOR] ✗ UNEXPECTED ERROR: {type(e).__name__}: {e}", flush=True)
                        else:
                            print(f"[MONITOR] ✗ CHANNEL IS NONE - cannot send tracked wallet alert to {tracked_channel_id}", flush=True)
                    
                    if is_sports:
                        if top_trader_info and config.top_trader_channel_id:
                            print(f"[MONITOR] ALERT TRIGGERED: Sports top trader ${value:,.0f}, attempting channel {config.top_trader_channel_id}", flush=True)
                            top_channel = await get_or_fetch_channel(config.top_trader_channel_id)
                            print(f"[MONITOR] Channel fetch result: {top_channel} (type: {type(top_channel).__name__ if top_channel else 'None'})", flush=True)
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
                                    message = await top_channel.send(embed=embed, view=button_view)
                                    print(f"[MONITOR] ✓ ALERT SENT: Sports top trader ${value:,.0f} to channel {config.top_trader_channel_id}, msg_id={message.id}", flush=True)
                                except discord.Forbidden as e:
                                    print(f"[MONITOR] ✗ FORBIDDEN: Cannot send to channel {config.top_trader_channel_id} - {e}", flush=True)
                                except discord.NotFound as e:
                                    print(f"[MONITOR] ✗ NOT FOUND: Channel {config.top_trader_channel_id} doesn't exist - {e}", flush=True)
                                except discord.HTTPException as e:
                                    print(f"[MONITOR] ✗ HTTP ERROR: {e.status} {e.code} - {e.text}", flush=True)
                                except Exception as e:
                                    print(f"[MONITOR] ✗ UNEXPECTED ERROR: {type(e).__name__}: {e}", flush=True)
                            else:
                                print(f"[MONITOR] ✗ CHANNEL IS NONE - cannot send sports top trader alert to {config.top_trader_channel_id}", flush=True)
                        
                        sports_channel = await get_or_fetch_channel(config.sports_channel_id)
                        print(f"[MONITOR] Sports channel fetch result: {sports_channel} (type: {type(sports_channel).__name__ if sports_channel else 'None'})", flush=True)
                        if sports_channel:
                            if wallet in tracked_addresses:
                                pass
                            elif is_fresh and value >= (config.sports_threshold or 5000.0):
                                print(f"[MONITOR] ALERT TRIGGERED: Sports fresh wallet ${value:,.0f}, attempting channel {config.sports_channel_id}", flush=True)
                                try:
                                    wallet_stats = await asyncio.wait_for(
                                        polymarket_client.get_wallet_pnl_stats(wallet),
                                        timeout=3.0
                                    )
                                except asyncio.TimeoutError:
                                    wallet_stats = {}
                                    print(f"[MONITOR] PNL stats timeout for {wallet[:10]}...", flush=True)
                                embed = create_fresh_wallet_alert_embed(
                                    trade=trade,
                                    value_usd=value,
                                    market_title=market_title,
                                    wallet_address=wallet,
                                    market_url=market_url,
                                    pnl=wallet_stats.get('pnl'),
                                    rank=wallet_stats.get('rank'),
                                    is_sports=True
                                )
                                try:
                                    message = await sports_channel.send(embed=embed, view=button_view)
                                    print(f"[MONITOR] ✓ ALERT SENT: Sports fresh wallet ${value:,.0f} to channel {config.sports_channel_id}, msg_id={message.id}", flush=True)
                                except discord.Forbidden as e:
                                    print(f"[MONITOR] ✗ FORBIDDEN: Cannot send to channel {config.sports_channel_id} - {e}", flush=True)
                                except discord.NotFound as e:
                                    print(f"[MONITOR] ✗ NOT FOUND: Channel {config.sports_channel_id} doesn't exist - {e}", flush=True)
                                except discord.HTTPException as e:
                                    print(f"[MONITOR] ✗ HTTP ERROR: {e.status} {e.code} - {e.text}", flush=True)
                                except Exception as e:
                                    print(f"[MONITOR] ✗ UNEXPECTED ERROR: {type(e).__name__}: {e}", flush=True)
                            elif value >= (config.sports_threshold or 5000.0):
                                print(f"[MONITOR] ALERT TRIGGERED: Sports whale ${value:,.0f}, attempting channel {config.sports_channel_id}", flush=True)
                                try:
                                    wallet_stats = await asyncio.wait_for(
                                        polymarket_client.get_wallet_pnl_stats(wallet),
                                        timeout=3.0
                                    )
                                except asyncio.TimeoutError:
                                    wallet_stats = {}
                                    print(f"[MONITOR] PNL stats timeout for {wallet[:10]}...", flush=True)
                                embed = create_whale_alert_embed(
                                    trade=trade,
                                    value_usd=value,
                                    market_title=market_title,
                                    wallet_address=wallet,
                                    market_url=market_url,
                                    pnl=wallet_stats.get('pnl'),
                                    rank=wallet_stats.get('rank'),
                                    is_sports=True
                                )
                                try:
                                    message = await sports_channel.send(embed=embed, view=button_view)
                                    print(f"[MONITOR] ✓ ALERT SENT: Sports whale ${value:,.0f} to channel {config.sports_channel_id}, msg_id={message.id}", flush=True)
                                except discord.Forbidden as e:
                                    print(f"[MONITOR] ✗ FORBIDDEN: Cannot send to channel {config.sports_channel_id} - {e}", flush=True)
                                except discord.NotFound as e:
                                    print(f"[MONITOR] ✗ NOT FOUND: Channel {config.sports_channel_id} doesn't exist - {e}", flush=True)
                                except discord.HTTPException as e:
                                    print(f"[MONITOR] ✗ HTTP ERROR: {e.status} {e.code} - {e.text}", flush=True)
                                except Exception as e:
                                    print(f"[MONITOR] ✗ UNEXPECTED ERROR: {type(e).__name__}: {e}", flush=True)
                        else:
                            print(f"[MONITOR] ✗ SPORTS CHANNEL IS NONE - cannot send alert to {config.sports_channel_id}", flush=True)
                    else:
                        if top_trader_info and config.top_trader_channel_id:
                            print(f"[MONITOR] ALERT TRIGGERED: Top trader ${value:,.0f}, attempting channel {config.top_trader_channel_id}", flush=True)
                            top_channel = await get_or_fetch_channel(config.top_trader_channel_id)
                            print(f"[MONITOR] Channel fetch result: {top_channel} (type: {type(top_channel).__name__ if top_channel else 'None'})", flush=True)
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
                                    message = await top_channel.send(embed=embed, view=button_view)
                                    print(f"[MONITOR] ✓ ALERT SENT: Top trader ${value:,.0f} to channel {config.top_trader_channel_id}, msg_id={message.id}", flush=True)
                                except discord.Forbidden as e:
                                    print(f"[MONITOR] ✗ FORBIDDEN: Cannot send to channel {config.top_trader_channel_id} - {e}", flush=True)
                                except discord.NotFound as e:
                                    print(f"[MONITOR] ✗ NOT FOUND: Channel {config.top_trader_channel_id} doesn't exist - {e}", flush=True)
                                except discord.HTTPException as e:
                                    print(f"[MONITOR] ✗ HTTP ERROR: {e.status} {e.code} - {e.text}", flush=True)
                                except Exception as e:
                                    print(f"[MONITOR] ✗ UNEXPECTED ERROR: {type(e).__name__}: {e}", flush=True)
                            else:
                                print(f"[MONITOR] ✗ CHANNEL IS NONE - cannot send top trader alert to {config.top_trader_channel_id}", flush=True)
                        
                        if is_bond and value >= 5000.0 and config.bonds_channel_id:
                            print(f"[MONITOR] ALERT TRIGGERED: Bonds ${value:,.0f}, attempting channel {config.bonds_channel_id}", flush=True)
                            bonds_channel = await get_or_fetch_channel(config.bonds_channel_id)
                            print(f"[MONITOR] Channel fetch result: {bonds_channel} (type: {type(bonds_channel).__name__ if bonds_channel else 'None'})", flush=True)
                            if bonds_channel:
                                try:
                                    wallet_stats = await asyncio.wait_for(
                                        polymarket_client.get_wallet_pnl_stats(wallet),
                                        timeout=3.0
                                    )
                                except asyncio.TimeoutError:
                                    wallet_stats = {}
                                    print(f"[MONITOR] PNL stats timeout for {wallet[:10]}...", flush=True)
                                embed = create_bonds_alert_embed(
                                    trade=trade,
                                    value_usd=value,
                                    market_title=market_title,
                                    wallet_address=wallet,
                                    market_url=market_url,
                                    pnl=wallet_stats.get('pnl'),
                                    rank=wallet_stats.get('rank')
                                )
                                try:
                                    message = await bonds_channel.send(embed=embed, view=button_view)
                                    alerts_sent += 1
                                    print(f"[MONITOR] ✓ ALERT SENT: Bonds ${value:,.0f} to channel {config.bonds_channel_id}, msg_id={message.id}", flush=True)
                                except discord.Forbidden as e:
                                    print(f"[MONITOR] ✗ FORBIDDEN: Cannot send to channel {config.bonds_channel_id} - {e}", flush=True)
                                except discord.NotFound as e:
                                    print(f"[MONITOR] ✗ NOT FOUND: Channel {config.bonds_channel_id} doesn't exist - {e}", flush=True)
                                except discord.HTTPException as e:
                                    print(f"[MONITOR] ✗ HTTP ERROR: {e.status} {e.code} - {e.text}", flush=True)
                                except Exception as e:
                                    print(f"[MONITOR] ✗ UNEXPECTED ERROR: {type(e).__name__}: {e}", flush=True)
                            else:
                                print(f"[MONITOR] ✗ CHANNEL IS NONE - cannot send bonds alert to {config.bonds_channel_id}", flush=True)
                        
                        elif is_fresh and value >= (config.fresh_wallet_threshold or 10000.0) and not is_bond:
                            fresh_channel_id = config.fresh_wallet_channel_id or config.alert_channel_id
                            print(f"[MONITOR] ALERT TRIGGERED: Fresh wallet ${value:,.0f}, attempting channel {fresh_channel_id}", flush=True)
                            fresh_channel = await get_or_fetch_channel(fresh_channel_id)
                            print(f"[MONITOR] Channel fetch result: {fresh_channel} (type: {type(fresh_channel).__name__ if fresh_channel else 'None'})", flush=True)
                            if fresh_channel:
                                try:
                                    wallet_stats = await asyncio.wait_for(
                                        polymarket_client.get_wallet_pnl_stats(wallet),
                                        timeout=3.0
                                    )
                                except asyncio.TimeoutError:
                                    wallet_stats = {}
                                    print(f"[MONITOR] PNL stats timeout for {wallet[:10]}...", flush=True)
                                embed = create_fresh_wallet_alert_embed(
                                    trade=trade,
                                    value_usd=value,
                                    market_title=market_title,
                                    wallet_address=wallet,
                                    market_url=market_url,
                                    pnl=wallet_stats.get('pnl'),
                                    rank=wallet_stats.get('rank')
                                )
                                try:
                                    message = await fresh_channel.send(embed=embed, view=button_view)
                                    print(f"[MONITOR] ✓ ALERT SENT: Fresh wallet ${value:,.0f} to channel {fresh_channel_id}, msg_id={message.id}", flush=True)
                                except discord.Forbidden as e:
                                    print(f"[MONITOR] ✗ FORBIDDEN: Cannot send to channel {fresh_channel_id} - {e}", flush=True)
                                except discord.NotFound as e:
                                    print(f"[MONITOR] ✗ NOT FOUND: Channel {fresh_channel_id} doesn't exist - {e}", flush=True)
                                except discord.HTTPException as e:
                                    print(f"[MONITOR] ✗ HTTP ERROR: {e.status} {e.code} - {e.text}", flush=True)
                                except Exception as e:
                                    print(f"[MONITOR] ✗ UNEXPECTED ERROR: {type(e).__name__}: {e}", flush=True)
                            else:
                                print(f"[MONITOR] ✗ CHANNEL IS NONE - cannot send fresh wallet alert to {fresh_channel_id}", flush=True)
                        
                        elif value >= (config.whale_threshold or 10000.0) and not is_bond:
                            whale_channel_id = config.whale_channel_id or config.alert_channel_id
                            whale_threshold = config.whale_threshold or 10000.0
                            print(f"[MONITOR] ALERT TRIGGERED: Whale ${value:,.0f} >= threshold ${whale_threshold:,.0f}, attempting channel {whale_channel_id}", flush=True)
                            whale_channel = await get_or_fetch_channel(whale_channel_id)
                            print(f"[MONITOR] Channel fetch result: {whale_channel} (type: {type(whale_channel).__name__ if whale_channel else 'None'})", flush=True)
                            if whale_channel:
                                try:
                                    wallet_stats = await asyncio.wait_for(
                                        polymarket_client.get_wallet_pnl_stats(wallet),
                                        timeout=3.0
                                    )
                                except asyncio.TimeoutError:
                                    wallet_stats = {}
                                    print(f"[MONITOR] PNL stats timeout for {wallet[:10]}...", flush=True)
                                embed = create_whale_alert_embed(
                                    trade=trade,
                                    value_usd=value,
                                    market_title=market_title,
                                    wallet_address=wallet,
                                    market_url=market_url,
                                    pnl=wallet_stats.get('pnl'),
                                    rank=wallet_stats.get('rank')
                                )
                                try:
                                    message = await whale_channel.send(embed=embed, view=button_view)
                                    alerts_sent += 1
                                    print(f"[MONITOR] ✓ ALERT SENT: Whale ${value:,.0f} to channel {whale_channel_id}, msg_id={message.id}", flush=True)
                                except discord.Forbidden as e:
                                    print(f"[MONITOR] ✗ FORBIDDEN: Cannot send to channel {whale_channel_id} - {e}", flush=True)
                                except discord.NotFound as e:
                                    print(f"[MONITOR] ✗ NOT FOUND: Channel {whale_channel_id} doesn't exist - {e}", flush=True)
                                except discord.HTTPException as e:
                                    print(f"[MONITOR] ✗ HTTP ERROR: {e.status} {e.code} - {e.text}", flush=True)
                                except Exception as e:
                                    print(f"[MONITOR] ✗ UNEXPECTED ERROR: {type(e).__name__}: {e}", flush=True)
                            else:
                                print(f"[MONITOR] ✗ CHANNEL IS NONE - cannot send whale alert to {whale_channel_id}", flush=True)
            
            if new_trades_count > 0 or alerts_sent > 0:
                print(f"[Monitor] Tracked wallets: {new_trades_count} new trades, {alerts_sent} alerts sent")
            
            session.commit()
        finally:
            session.close()
            
    except Exception as e:
        print(f"Error in monitor loop: {e}")


@monitor_loop.before_loop
async def before_monitor():
    await bot.wait_until_ready()


async def seed_initial_prices():
    """Seed initial prices into in-memory volatility manager on startup."""
    markets = await polymarket_client.get_active_markets_prices(limit=500, include_sports=True)
    
    for market in markets:
        condition_id = market['condition_id']
        current_price = market['yes_price']
        title = market['title']
        slug = market['slug']
        
        volatility_manager.seed_price(condition_id, current_price, title, slug)
    
    stats = volatility_manager.get_stats()
    print(f"[STARTUP] Seeded {stats['markets_tracked']} markets into in-memory volatility tracker")


@tasks.loop(minutes=5)
async def volatility_loop():
    """Periodic price refresh to keep in-memory volatility tracker current for markets with no recent trades."""
    try:
        markets = await polymarket_client.get_active_markets_prices(limit=500, include_sports=True)
        
        for market in markets:
            condition_id = market['condition_id']
            current_price = market['yes_price']
            title = market['title']
            slug = market['slug']
            
            volatility_manager.record_price(condition_id, current_price, title, slug)
        
        volatility_manager.cleanup_old_data()
        
        stats = volatility_manager.get_stats()
        print(f"[VOLATILITY] Stats: {stats['markets_tracked']} markets, {stats['total_price_entries']} prices, {stats['active_cooldowns']} cooldowns, windows={stats['timeframes']}", flush=True)
    except Exception as e:
        print(f"Error in volatility refresh: {e}")


@volatility_loop.before_loop
async def before_volatility():
    await bot.wait_until_ready()


@tasks.loop(hours=1)
async def cleanup_loop():
    """Cleanup old database records. PriceSnapshots no longer used (in-memory now)."""
    try:
        session = get_session()
        try:
            alert_cutoff = datetime.utcnow() - timedelta(hours=24)
            deleted_alerts = session.query(VolatilityAlert).filter(
                VolatilityAlert.alerted_at < alert_cutoff
            ).delete()
            
            old_cutoff = datetime.utcnow() - timedelta(days=7)
            deleted_seen = session.query(SeenTransaction).filter(
                SeenTransaction.seen_at < old_cutoff
            ).delete()
            
            session.commit()
            if deleted_alerts > 0 or deleted_seen > 0:
                print(f"Cleanup: {deleted_alerts} old volatility alerts, {deleted_seen} old seen transactions")
        finally:
            session.close()
    except Exception as e:
        print(f"Error in cleanup loop: {e}")


@cleanup_loop.before_loop
async def before_cleanup():
    await bot.wait_until_ready()


_ws_stats = {'processed': 0, 'above_5k': 0, 'above_10k': 0, 'alerts_sent': 0, 'last_log': 0}

async def handle_websocket_trade(trade: dict):
    global _ws_stats
    
    # EARLY EXTRACTION: Get value and wallet BEFORE any DB calls
    value = polymarket_client.calculate_trade_value(trade)
    wallet = polymarket_client.get_wallet_from_trade(trade)
    
    if not wallet:
        return
    
    wallet = wallet.lower()
    side = trade.get('side', '').upper()
    
    # Record price for SELLs (they move markets too!) but don't process further
    if side == 'SELL':
        price = float(trade.get('price', 0) or 0)
        condition_id = trade.get('conditionId') or trade.get('condition_id') or trade.get('asset_id', '')
        if condition_id and price > 0:
            market_title = trade.get('title', '') or polymarket_client.get_market_title(trade)
            slug = trade.get('slug', '') or polymarket_client.get_market_slug(trade)
            volatility_manager.record_price(condition_id, price, market_title, slug)
        return
    
    # VOLATILITY TRACKING: Record price for ALL trades (before any filtering)
    price = float(trade.get('price', 0) or 0)
    condition_id = trade.get('conditionId') or trade.get('condition_id') or trade.get('asset_id', '')
    if condition_id and price > 0:
        market_title = trade.get('title', '') or polymarket_client.get_market_title(trade)
        slug = trade.get('slug', '') or polymarket_client.get_market_slug(trade)
        volatility_manager.record_price(condition_id, price, market_title, slug)
        
        # Check for volatility alerts
        if bot.is_ready():
            all_configs = get_cached_server_configs()
            volatility_configs = [c for c in all_configs if not c.is_paused and c.volatility_channel_id]
            
            for config in volatility_configs:
                threshold = config.volatility_threshold or 5.0
                category_filter = getattr(config, 'volatility_category', 'all') or 'all'
                
                if category_filter != 'all':
                    market_category = polymarket_client.detect_market_category(trade)
                    if market_category != category_filter:
                        continue
                
                alert = volatility_manager.check_volatility(condition_id, config.guild_id, threshold)
                
                if alert:
                    try:
                        vol_session = get_session()
                        cooldown_time = datetime.utcnow() - timedelta(minutes=30)
                        recent_db_alert = vol_session.query(VolatilityAlert).filter(
                            VolatilityAlert.condition_id == condition_id,
                            VolatilityAlert.alerted_at >= cooldown_time
                        ).first()
                        
                        if not recent_db_alert:
                            print(f"[VOLATILITY] ALERT: {alert['title'][:30]}... swing {alert['price_change_pct']:+.1f} pts", flush=True)
                            channel = await get_or_fetch_channel(config.volatility_channel_id)
                            if channel:
                                embed, vol_market_url = create_volatility_alert_embed(
                                    market_title=alert['title'],
                                    slug=alert['slug'],
                                    old_price=alert['old_price'],
                                    new_price=alert['new_price'],
                                    price_change=alert['price_change_pct'],
                                    time_window_minutes=alert['time_window_minutes']
                                )
                                vol_event_slug = polymarket_client.get_event_slug_by_condition(condition_id, alert['slug'])
                                button_view = create_trade_button_view(vol_event_slug, vol_market_url)
                                
                                try:
                                    message = await channel.send(embed=embed, view=button_view)
                                    vol_session.add(VolatilityAlert(condition_id=condition_id, price_change=alert['price_change_pct']))
                                    vol_session.commit()
                                    _ws_stats['alerts_sent'] += 1
                                    print(f"[VOLATILITY] ✓ ALERT SENT to channel {config.volatility_channel_id}", flush=True)
                                except Exception as e:
                                    print(f"[VOLATILITY] ✗ ERROR: {e}", flush=True)
                        vol_session.close()
                    except Exception as e:
                        print(f"[VOLATILITY] DB error: {e}", flush=True)
    
    # Check if wallet is tracked (uses cache, no DB query)
    tracked_addresses, tracked_by_guild = get_cached_tracked_wallets()
    is_tracked = wallet in tracked_addresses
    
    # EARLY EXIT: Skip trades below $1000 unless it's a tracked wallet
    # This prevents database overload from tiny trades
    if value < 1000 and not is_tracked:
        return
    
    # Track stats (minimal overhead)
    _ws_stats['processed'] += 1
    if value >= 5000:
        _ws_stats['above_5k'] += 1
    if value >= 10000:
        _ws_stats['above_10k'] += 1
    
    # Log stats every 5000 trades
    if _ws_stats['processed'] % 5000 == 0:
        print(f"[WS Stats] Processed: {_ws_stats['processed']}, $5k+ BUY: {_ws_stats['above_5k']}, $10k+ BUY: {_ws_stats['above_10k']}, Alerts: {_ws_stats['alerts_sent']}")
    
    # Only log significant trades
    if value >= 5000:
        print(f"[WS] Processing ${value:,.0f} trade from {wallet[:10]}...", flush=True)
    elif is_tracked:
        print(f"[WS] Processing tracked wallet trade ${value:,.0f} from {wallet[:10]}...", flush=True)
    
    # Check bot ready state before processing
    if not bot.is_ready():
        return
    
    # Now we can do DB operations for significant trades
    session = None
    for attempt in range(3):
        try:
            session = get_session()
            session.execute(text("SELECT 1"))
            break
        except Exception as e:
            if attempt == 2:
                print(f"[WS] Database connection failed after 3 attempts: {e}", flush=True)
                return
            await asyncio.sleep(0.5)
    
    if not session:
        return
    
    try:
        unique_key = polymarket_client.get_unique_trade_id(trade)
        if not unique_key or len(unique_key) < 10:
            return
        
        seen = session.query(SeenTransaction).filter_by(tx_hash=unique_key[:66]).first()
        if seen:
            return
        
        session.add(SeenTransaction(tx_hash=unique_key[:66]))
        session.commit()
        
        price = float(trade.get('price', 0) or 0)
        
        market_title = polymarket_client.get_market_title(trade)
        market_url = polymarket_client.get_market_url(trade)
        event_slug = polymarket_client.get_event_slug(trade)
        condition_id = trade.get('condition_id') or trade.get('asset_id', '')
        slug = polymarket_client.get_market_slug(trade)
        
        # Volatility tracking is now handled earlier (before $1000 filter)
        
        is_sports = polymarket_client.is_sports_market(trade)
        is_bond = price >= 0.95
        
        all_configs = get_cached_server_configs()
        configs = [c for c in all_configs if not c.is_paused]
        configs = [c for c in configs if c.alert_channel_id or c.sports_channel_id or c.top_trader_channel_id or c.bonds_channel_id or c.tracked_wallet_channel_id or c.whale_channel_id or c.fresh_wallet_channel_id]
        
        if not configs:
            return
        
        wallet_activity = session.query(WalletActivity).filter_by(wallet_address=wallet).first()
        is_fresh = False
        if wallet_activity is None:
            try:
                has_history = await asyncio.wait_for(
                    polymarket_client.has_prior_activity(wallet),
                    timeout=2.0
                )
            except asyncio.TimeoutError:
                has_history = True  # Assume not fresh if timeout
                print(f"[WS] Activity check timeout for {wallet[:10]}...", flush=True)
            if has_history is False:
                is_fresh = True
            session.add(WalletActivity(wallet_address=wallet, transaction_count=1))
            session.commit()
        else:
            wallet_activity.transaction_count += 1
            session.commit()
        
        top_trader_info = polymarket_client.is_top_trader(wallet)
        
        if not top_trader_info and value >= 5000:
            try:
                top_trader_info = await asyncio.wait_for(
                    polymarket_client.lookup_trader_rank(wallet),
                    timeout=3.0
                )
                if top_trader_info:
                    polymarket_client._proxy_to_trader_map[wallet.lower()] = top_trader_info
                    print(f"[WS] DISCOVERED TOP TRADER: {wallet[:10]}... is Rank #{top_trader_info.get('rank')} ({top_trader_info.get('username', 'Unknown')})", flush=True)
            except asyncio.TimeoutError:
                pass
        
        if top_trader_info:
            print(f"[WS] TOP TRADER DETECTED: {wallet[:10]}... ${value:,.0f} - Rank #{top_trader_info.get('rank')} ({top_trader_info.get('username', 'Unknown')})", flush=True)
        
        trade_timestamp = trade.get('timestamp', 0)
        trade_time = datetime.utcfromtimestamp(trade_timestamp) if trade_timestamp else None
        
        def is_trade_after_tracking(trade_dt, added_dt):
            if not trade_dt or not added_dt:
                return True
            trade_naive = trade_dt.replace(tzinfo=None) if hasattr(trade_dt, 'tzinfo') and trade_dt.tzinfo else trade_dt
            added_naive = added_dt.replace(tzinfo=None) if hasattr(added_dt, 'tzinfo') and added_dt.tzinfo else added_dt
            return trade_naive >= added_naive
        
        for config in configs:
            tracked_addresses = tracked_by_guild.get(config.guild_id, {})
            button_view = create_trade_button_view(event_slug, market_url)
            
            if wallet in tracked_addresses:
                tracked_channel_id = config.tracked_wallet_channel_id or config.alert_channel_id
                print(f"[WS] ALERT TRIGGERED: Tracked wallet ${value:,.0f}, attempting channel {tracked_channel_id}", flush=True)
                tracked_channel = await get_or_fetch_channel(tracked_channel_id)
                print(f"[WS] Channel fetch result: {tracked_channel} (type: {type(tracked_channel).__name__ if tracked_channel else 'None'})", flush=True)
                if tracked_channel:
                    tw = tracked_addresses[wallet]
                    if not is_trade_after_tracking(trade_time, tw.added_at):
                        continue
                    try:
                        wallet_stats = await asyncio.wait_for(
                            polymarket_client.get_wallet_pnl_stats(wallet),
                            timeout=3.0
                        )
                    except asyncio.TimeoutError:
                        wallet_stats = {}
                        print(f"[WS] PNL stats timeout for {wallet[:10]}...", flush=True)
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
                        message = await tracked_channel.send(embed=embed, view=button_view)
                        _ws_stats['alerts_sent'] += 1
                        print(f"[WS] ✓ ALERT SENT: Tracked wallet ${value:,.0f} to channel {tracked_channel_id}, msg_id={message.id}", flush=True)
                    except discord.Forbidden as e:
                        print(f"[WS] ✗ FORBIDDEN: Cannot send to channel {tracked_channel_id} - {e}", flush=True)
                    except discord.NotFound as e:
                        print(f"[WS] ✗ NOT FOUND: Channel {tracked_channel_id} doesn't exist - {e}", flush=True)
                    except discord.HTTPException as e:
                        print(f"[WS] ✗ HTTP ERROR: {e.status} {e.code} - {e.text}", flush=True)
                    except Exception as e:
                        print(f"[WS] ✗ UNEXPECTED ERROR: {type(e).__name__}: {e}", flush=True)
                else:
                    print(f"[WS] ✗ CHANNEL IS NONE - cannot send tracked wallet alert to {tracked_channel_id}", flush=True)
            
            if is_sports:
                top_trader_threshold = config.top_trader_threshold or 2500.0
                sent_top_trader_alert = False
                if top_trader_info and config.top_trader_channel_id and value >= top_trader_threshold:
                    print(f"[WS] ALERT TRIGGERED: Sports top trader ${value:,.0f}, attempting channel {config.top_trader_channel_id}", flush=True)
                    top_channel = await get_or_fetch_channel(config.top_trader_channel_id)
                    print(f"[WS] Channel fetch result: {top_channel} (type: {type(top_channel).__name__ if top_channel else 'None'})", flush=True)
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
                            message = await top_channel.send(embed=embed, view=button_view)
                            sent_top_trader_alert = True
                            print(f"[WS] ✓ ALERT SENT: Sports top trader ${value:,.0f} to channel {config.top_trader_channel_id}, msg_id={message.id}", flush=True)
                            print(f"[WS] Top trader takes priority - skipping sports whale routing", flush=True)
                        except discord.Forbidden as e:
                            print(f"[WS] ✗ FORBIDDEN: Cannot send to channel {config.top_trader_channel_id} - {e}", flush=True)
                        except discord.NotFound as e:
                            print(f"[WS] ✗ NOT FOUND: Channel {config.top_trader_channel_id} doesn't exist - {e}", flush=True)
                        except discord.HTTPException as e:
                            print(f"[WS] ✗ HTTP ERROR: {e.status} {e.code} - {e.text}", flush=True)
                        except Exception as e:
                            print(f"[WS] ✗ UNEXPECTED ERROR: {type(e).__name__}: {e}", flush=True)
                    else:
                        print(f"[WS] ✗ CHANNEL IS NONE - cannot send sports top trader alert to {config.top_trader_channel_id}", flush=True)
                
                if sent_top_trader_alert:
                    continue
                
                sports_channel = await get_or_fetch_channel(config.sports_channel_id)
                if sports_channel:
                    if wallet in tracked_addresses:
                        pass
                    elif is_fresh and value >= (config.sports_threshold or 5000.0):
                        print(f"[WS] ALERT TRIGGERED: Sports fresh wallet ${value:,.0f}, attempting channel {config.sports_channel_id}", flush=True)
                        try:
                            wallet_stats = await asyncio.wait_for(
                                polymarket_client.get_wallet_pnl_stats(wallet),
                                timeout=3.0
                            )
                        except asyncio.TimeoutError:
                            wallet_stats = {}
                            print(f"[WS] PNL stats timeout for {wallet[:10]}...", flush=True)
                        embed = create_fresh_wallet_alert_embed(
                            trade=trade,
                            value_usd=value,
                            market_title=market_title,
                            wallet_address=wallet,
                            market_url=market_url,
                            pnl=wallet_stats.get('pnl'),
                            rank=wallet_stats.get('rank'),
                            is_sports=True
                        )
                        try:
                            message = await sports_channel.send(embed=embed, view=button_view)
                            _ws_stats['alerts_sent'] += 1
                            print(f"[WS] ✓ ALERT SENT: Sports fresh wallet ${value:,.0f} to channel {config.sports_channel_id}, msg_id={message.id}", flush=True)
                        except discord.Forbidden as e:
                            print(f"[WS] ✗ FORBIDDEN: Cannot send to channel {config.sports_channel_id} - {e}", flush=True)
                        except discord.NotFound as e:
                            print(f"[WS] ✗ NOT FOUND: Channel {config.sports_channel_id} doesn't exist - {e}", flush=True)
                        except discord.HTTPException as e:
                            print(f"[WS] ✗ HTTP ERROR: {e.status} {e.code} - {e.text}", flush=True)
                        except Exception as e:
                            print(f"[WS] ✗ UNEXPECTED ERROR: {type(e).__name__}: {e}", flush=True)
                    elif value >= (config.sports_threshold or 5000.0):
                        print(f"[WS] ALERT TRIGGERED: Sports whale ${value:,.0f}, attempting channel {config.sports_channel_id}", flush=True)
                        try:
                            wallet_stats = await asyncio.wait_for(
                                polymarket_client.get_wallet_pnl_stats(wallet),
                                timeout=3.0
                            )
                        except asyncio.TimeoutError:
                            wallet_stats = {}
                            print(f"[WS] PNL stats timeout for {wallet[:10]}...", flush=True)
                        embed = create_whale_alert_embed(
                            trade=trade,
                            value_usd=value,
                            market_title=market_title,
                            wallet_address=wallet,
                            market_url=market_url,
                            pnl=wallet_stats.get('pnl'),
                            rank=wallet_stats.get('rank'),
                            is_sports=True
                        )
                        try:
                            message = await sports_channel.send(embed=embed, view=button_view)
                            _ws_stats['alerts_sent'] += 1
                            print(f"[WS] ✓ ALERT SENT: Sports whale ${value:,.0f} to channel {config.sports_channel_id}, msg_id={message.id}", flush=True)
                        except discord.Forbidden as e:
                            print(f"[WS] ✗ FORBIDDEN: Cannot send to channel {config.sports_channel_id} - {e}", flush=True)
                        except discord.NotFound as e:
                            print(f"[WS] ✗ NOT FOUND: Channel {config.sports_channel_id} doesn't exist - {e}", flush=True)
                        except discord.HTTPException as e:
                            print(f"[WS] ✗ HTTP ERROR: {e.status} {e.code} - {e.text}", flush=True)
                        except Exception as e:
                            print(f"[WS] ✗ UNEXPECTED ERROR: {type(e).__name__}: {e}", flush=True)
            else:
                top_trader_threshold = config.top_trader_threshold or 2500.0
                sent_top_trader_alert = False
                if top_trader_info and config.top_trader_channel_id and value >= top_trader_threshold:
                    print(f"[WS] ALERT TRIGGERED: Top trader ${value:,.0f}, attempting channel {config.top_trader_channel_id}", flush=True)
                    top_channel = await get_or_fetch_channel(config.top_trader_channel_id)
                    print(f"[WS] Channel fetch result: {top_channel} (type: {type(top_channel).__name__ if top_channel else 'None'})", flush=True)
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
                            message = await top_channel.send(embed=embed, view=button_view)
                            _ws_stats['alerts_sent'] += 1
                            sent_top_trader_alert = True
                            print(f"[WS] ✓ ALERT SENT: Top trader ${value:,.0f} to channel {config.top_trader_channel_id}, msg_id={message.id}", flush=True)
                            print(f"[WS] Top trader takes priority - skipping whale/fresh routing", flush=True)
                        except discord.Forbidden as e:
                            print(f"[WS] ✗ FORBIDDEN: Cannot send to channel {config.top_trader_channel_id} - {e}", flush=True)
                        except discord.NotFound as e:
                            print(f"[WS] ✗ NOT FOUND: Channel {config.top_trader_channel_id} doesn't exist - {e}", flush=True)
                        except discord.HTTPException as e:
                            print(f"[WS] ✗ HTTP ERROR: {e.status} {e.code} - {e.text}", flush=True)
                        except Exception as e:
                            print(f"[WS] ✗ UNEXPECTED ERROR: {type(e).__name__}: {e}", flush=True)
                    else:
                        print(f"[WS] ✗ CHANNEL IS NONE - cannot send top trader alert to {config.top_trader_channel_id}", flush=True)
                
                if sent_top_trader_alert:
                    continue
                
                if is_bond and value >= 5000.0 and config.bonds_channel_id:
                    print(f"[WS] ALERT TRIGGERED: Bonds ${value:,.0f}, attempting channel {config.bonds_channel_id}", flush=True)
                    bonds_channel = await get_or_fetch_channel(config.bonds_channel_id)
                    print(f"[WS] Channel fetch result: {bonds_channel} (type: {type(bonds_channel).__name__ if bonds_channel else 'None'})", flush=True)
                    if bonds_channel:
                        try:
                            wallet_stats = await asyncio.wait_for(
                                polymarket_client.get_wallet_pnl_stats(wallet),
                                timeout=3.0
                            )
                        except asyncio.TimeoutError:
                            wallet_stats = {}
                            print(f"[WS] PNL stats timeout for {wallet[:10]}...", flush=True)
                        embed = create_bonds_alert_embed(
                            trade=trade,
                            value_usd=value,
                            market_title=market_title,
                            wallet_address=wallet,
                            market_url=market_url,
                            pnl=wallet_stats.get('pnl'),
                            rank=wallet_stats.get('rank')
                        )
                        try:
                            message = await bonds_channel.send(embed=embed, view=button_view)
                            _ws_stats['alerts_sent'] += 1
                            print(f"[WS] ✓ ALERT SENT: Bonds ${value:,.0f} to channel {config.bonds_channel_id}, msg_id={message.id}", flush=True)
                        except discord.Forbidden as e:
                            print(f"[WS] ✗ FORBIDDEN: Cannot send to channel {config.bonds_channel_id} - {e}", flush=True)
                        except discord.NotFound as e:
                            print(f"[WS] ✗ NOT FOUND: Channel {config.bonds_channel_id} doesn't exist - {e}", flush=True)
                        except discord.HTTPException as e:
                            print(f"[WS] ✗ HTTP ERROR: {e.status} {e.code} - {e.text}", flush=True)
                        except Exception as e:
                            print(f"[WS] ✗ UNEXPECTED ERROR: {type(e).__name__}: {e}", flush=True)
                    else:
                        print(f"[WS] ✗ CHANNEL IS NONE - cannot send bonds alert to {config.bonds_channel_id}", flush=True)
                
                if is_fresh and value >= (config.fresh_wallet_threshold or 10000.0) and not is_bond:
                    fresh_channel_id = config.fresh_wallet_channel_id or config.alert_channel_id
                    print(f"[WS] ALERT TRIGGERED: Fresh wallet ${value:,.0f}, attempting channel {fresh_channel_id}", flush=True)
                    fresh_channel = await get_or_fetch_channel(fresh_channel_id)
                    print(f"[WS] Channel fetch result: {fresh_channel} (type: {type(fresh_channel).__name__ if fresh_channel else 'None'})", flush=True)
                    if fresh_channel:
                        try:
                            wallet_stats = await asyncio.wait_for(
                                polymarket_client.get_wallet_pnl_stats(wallet),
                                timeout=3.0
                            )
                        except asyncio.TimeoutError:
                            wallet_stats = {}
                            print(f"[WS] PNL stats timeout for {wallet[:10]}...", flush=True)
                        embed = create_fresh_wallet_alert_embed(
                            trade=trade,
                            value_usd=value,
                            market_title=market_title,
                            wallet_address=wallet,
                            market_url=market_url,
                            pnl=wallet_stats.get('pnl'),
                            rank=wallet_stats.get('rank')
                        )
                        try:
                            message = await fresh_channel.send(embed=embed, view=button_view)
                            _ws_stats['alerts_sent'] += 1
                            print(f"[WS] ✓ ALERT SENT: Fresh wallet ${value:,.0f} to channel {fresh_channel_id}, msg_id={message.id}", flush=True)
                        except discord.Forbidden as e:
                            print(f"[WS] ✗ FORBIDDEN: Cannot send to channel {fresh_channel_id} - {e}", flush=True)
                        except discord.NotFound as e:
                            print(f"[WS] ✗ NOT FOUND: Channel {fresh_channel_id} doesn't exist - {e}", flush=True)
                        except discord.HTTPException as e:
                            print(f"[WS] ✗ HTTP ERROR: {e.status} {e.code} - {e.text}", flush=True)
                        except Exception as e:
                            print(f"[WS] ✗ UNEXPECTED ERROR: {type(e).__name__}: {e}", flush=True)
                    else:
                        print(f"[WS] ✗ CHANNEL IS NONE - cannot send fresh wallet alert to {fresh_channel_id}", flush=True)
                
                if value >= (config.whale_threshold or 10000.0) and not is_bond and not is_fresh:
                    whale_channel_id = config.whale_channel_id or config.alert_channel_id
                    whale_threshold = config.whale_threshold or 10000.0
                    print(f"[WS] ALERT TRIGGERED: Whale ${value:,.0f} >= threshold ${whale_threshold:,.0f}, attempting channel {whale_channel_id}", flush=True)
                    whale_channel = await get_or_fetch_channel(whale_channel_id)
                    print(f"[WS] Channel fetch result: {whale_channel} (type: {type(whale_channel).__name__ if whale_channel else 'None'})", flush=True)
                    if whale_channel:
                        try:
                            wallet_stats = await asyncio.wait_for(
                                polymarket_client.get_wallet_pnl_stats(wallet),
                                timeout=3.0
                            )
                        except asyncio.TimeoutError:
                            wallet_stats = {}
                            print(f"[WS] PNL stats timeout for {wallet[:10]}...", flush=True)
                        embed = create_whale_alert_embed(
                            trade=trade,
                            value_usd=value,
                            market_title=market_title,
                            wallet_address=wallet,
                            market_url=market_url,
                            pnl=wallet_stats.get('pnl'),
                            rank=wallet_stats.get('rank')
                        )
                        try:
                            message = await whale_channel.send(embed=embed, view=button_view)
                            _ws_stats['alerts_sent'] += 1
                            print(f"[WS] ✓ ALERT SENT: Whale ${value:,.0f} to channel {whale_channel_id}, msg_id={message.id}", flush=True)
                        except discord.Forbidden as e:
                            print(f"[WS] ✗ FORBIDDEN: Cannot send to channel {whale_channel_id} - {e}", flush=True)
                        except discord.NotFound as e:
                            print(f"[WS] ✗ NOT FOUND: Channel {whale_channel_id} doesn't exist - {e}", flush=True)
                        except discord.HTTPException as e:
                            print(f"[WS] ✗ HTTP ERROR: {e.status} {e.code} - {e.text}", flush=True)
                        except Exception as e:
                            print(f"[WS] ✗ UNEXPECTED ERROR: {type(e).__name__}: {e}", flush=True)
                    else:
                        print(f"[WS] ✗ CHANNEL IS NONE - cannot send whale alert to {whale_channel_id}", flush=True)
    finally:
        session.close()


def on_websocket_reconnect():
    """Called when WebSocket reconnects - reset volatility warm-up to prevent false alerts."""
    volatility_manager.reset_warmup()

polymarket_ws = PolymarketWebSocket(
    on_trade_callback=handle_websocket_trade,
    on_reconnect_callback=on_websocket_reconnect
)


async def start_websocket():
    await bot.wait_until_ready()
    print("[WebSocket] Starting real-time trade feed...")
    await polymarket_ws.connect()


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


async def health_handler(request):
    """Health check endpoint for Railway"""
    return web.Response(text="OK", status=200)


async def metrics_handler(request):
    """Metrics endpoint"""
    uptime = time.time() - bot_start_time if 'bot_start_time' in globals() else 0
    return web.Response(
        text=f"uptime_seconds {uptime}\nbot_ready {bot.is_ready()}\n",
        status=200
    )


async def run_health_server():
    """
    Runs HTTP server for Railway health checks in the background.
    Uses async pattern so health server runs in same event loop as Discord bot.
    This MUST work or Railway kills the app with SIGTERM.
    """
    app = web.Application()
    app.router.add_get('/', health_handler)
    app.router.add_get('/health', health_handler)
    app.router.add_get('/metrics', metrics_handler)
    
    port = int(os.environ.get('PORT', 8080))
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    
    print(f"[HEALTH] Health server listening on 0.0.0.0:{port}", flush=True)
    
    while True:
        await asyncio.sleep(3600)


def main():
    import signal
    import traceback
    import sys
    
    global bot_start_time
    bot_start_time = time.time()
    
    def signal_handler(signum, frame):
        sig_name = signal.Signals(signum).name
        print(f"[SIGNAL] Received {sig_name} (signal {signum})", flush=True)
        print(f"[SIGNAL] Stack trace at signal:", flush=True)
        traceback.print_stack(frame)
        sys.stdout.flush()
        sys.exit(0)
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    is_production = os.environ.get('REPLIT_DEPLOYMENT') == '1'
    
    if is_production:
        token = os.environ.get('DISCORD_BOT_TOKEN')
        print("Running in PRODUCTION - using DISCORD_BOT_TOKEN", flush=True)
    else:
        token = os.environ.get('DEV_DISCORD_BOT_TOKEN') or os.environ.get('DISCORD_BOT_TOKEN')
        if os.environ.get('DEV_DISCORD_BOT_TOKEN'):
            print("Running in DEVELOPMENT - using DEV_DISCORD_BOT_TOKEN", flush=True)
        else:
            print("Running in DEVELOPMENT - using DISCORD_BOT_TOKEN (no dev token set)", flush=True)
    
    if not token:
        print("ERROR: No Discord bot token found", flush=True)
        print("Set DISCORD_BOT_TOKEN for production or DEV_DISCORD_BOT_TOKEN for development", flush=True)
        return
    
    port = os.environ.get('PORT', '8080')
    print(f"[RAILWAY] PORT environment variable: {port}", flush=True)
    print(f"[RAILWAY] Starting health server on port {port}", flush=True)
    
    print("Starting Polymarket Discord Bot...", flush=True)
    
    async def run_all():
        health_task = asyncio.create_task(run_health_server())
        print("[HEALTH] Health server task created", flush=True)
        
        await asyncio.sleep(2)
        
        async with bot:
            await bot.start(token)
    
    try:
        asyncio.run(run_all())
    except KeyboardInterrupt:
        print("[MAIN] Received keyboard interrupt", flush=True)
    except Exception as e:
        print(f"[FATAL] Bot crashed with exception: {type(e).__name__}: {e}", flush=True)
        traceback.print_exc()
        sys.stdout.flush()
        raise


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        print(f"[FATAL] Unhandled exception in main: {type(e).__name__}: {e}", flush=True)
        traceback.print_exc()
        raise
