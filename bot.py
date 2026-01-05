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


@bot.tree.command(name="setup", description="Set the channel for Polymarket alerts")
@app_commands.describe(channel="The channel to send alerts to")
@app_commands.checks.has_permissions(administrator=True)
async def setup(interaction: discord.Interaction, channel: discord.TextChannel):
    session = get_session()
    try:
        config = session.query(ServerConfig).filter_by(guild_id=interaction.guild_id).first()
        if not config:
            config = ServerConfig(guild_id=interaction.guild_id)
            session.add(config)
        
        config.alert_channel_id = channel.id
        session.commit()
        
        await interaction.response.send_message(
            f"Alerts will now be sent to {channel.mention}. Use `/threshold` to adjust alert thresholds.",
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
        config.fresh_wallet_threshold = amount
        session.commit()
        
        await interaction.response.send_message(
            f"Alert threshold set to ${amount:,.0f}",
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


@bot.tree.command(name="untrack", description="Remove a wallet from tracking")
@app_commands.describe(wallet="The wallet address to stop tracking")
@app_commands.checks.has_permissions(administrator=True)
async def untrack(interaction: discord.Interaction, wallet: str):
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
        
        session.delete(tracked)
        session.commit()
        
        await interaction.response.send_message(
            f"Stopped tracking wallet `{wallet[:6]}...{wallet[-4:]}`",
            ephemeral=True
        )
    finally:
        session.close()


@bot.tree.command(name="list", description="Show current settings and tracked wallets")
async def list_settings(interaction: discord.Interaction):
    session = get_session()
    try:
        config = session.query(ServerConfig).filter_by(guild_id=interaction.guild_id).first()
        
        if not config:
            await interaction.response.send_message(
                "No configuration found. Use `/setup` to get started.",
                ephemeral=True
            )
            return
        
        channel_name = None
        if config.alert_channel_id:
            channel = interaction.guild.get_channel(config.alert_channel_id)
            channel_name = channel.name if channel else None
        
        tracked = session.query(TrackedWallet).filter_by(guild_id=interaction.guild_id).all()
        
        volatility_channel_name = None
        if config.volatility_channel_id:
            vol_channel = interaction.guild.get_channel(config.volatility_channel_id)
            volatility_channel_name = vol_channel.name if vol_channel else None
        
        sports_channel_name = None
        if config.sports_channel_id:
            sports_channel = interaction.guild.get_channel(config.sports_channel_id)
            sports_channel_name = sports_channel.name if sports_channel else None
        
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
            sports_threshold=config.sports_threshold or 5000.0
        )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
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


@bot.tree.command(name="help", description="Show available commands")
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Polymarket Monitor - Help",
        description="Monitor Polymarket activity in your Discord server",
        color=0x4ECDC4
    )
    
    embed.add_field(
        name="/setup #channel",
        value="Set the channel for trade alerts",
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
        embed = create_wallet_positions_embed(
            wallet_address=self.wallet_address,
            wallet_label=self.wallet_label,
            positions=positions
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
        for wallet in tracked:
            wallet_positions = await polymarket_client.get_wallet_positions(wallet.wallet_address)
            positions_data[wallet.wallet_address] = wallet_positions
        
        embed = create_positions_overview_embed(tracked, positions_data)
        
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


@tasks.loop(seconds=30)
async def monitor_loop():
    try:
        await polymarket_client.refresh_market_cache()
        
        session = get_session()
        try:
            configs = session.query(ServerConfig).filter(
                ServerConfig.is_paused == False
            ).all()
            
            configs = [c for c in configs if c.alert_channel_id or c.sports_channel_id]
            
            if not configs:
                return
            
            all_tracked = session.query(TrackedWallet).all()
            tracked_by_guild = {}
            unique_tracked_addresses = set()
            for tw in all_tracked:
                if tw.guild_id not in tracked_by_guild:
                    tracked_by_guild[tw.guild_id] = {}
                tracked_by_guild[tw.guild_id][tw.wallet_address] = tw
                unique_tracked_addresses.add(tw.wallet_address)
            
            global_trades = await polymarket_client.get_recent_trades(limit=50)
            
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
                
                price = float(trade.get('price', 0) or 0)
                side = trade.get('side', '').lower()
                if side == 'sell' and price > 0.99:
                    continue
                
                wallet_activity = session.query(WalletActivity).filter_by(wallet_address=wallet).first()
                is_fresh = wallet_activity is None and wallet not in processed_wallets_this_batch
                
                if wallet not in processed_wallets_this_batch:
                    if wallet_activity is None:
                        session.add(WalletActivity(wallet_address=wallet, transaction_count=1))
                    else:
                        wallet_activity.transaction_count += 1
                    processed_wallets_this_batch.add(wallet)
                
                is_sports = polymarket_client.is_sports_market(trade)
                
                for config in configs:
                    tracked_addresses = tracked_by_guild.get(config.guild_id, {})
                    button_view = create_trade_button_view(market_url)
                    
                    trade_timestamp = trade.get('timestamp', 0)
                    trade_time = datetime.utcfromtimestamp(trade_timestamp) if trade_timestamp else None
                    
                    if is_sports:
                        sports_channel = bot.get_channel(config.sports_channel_id) if config.sports_channel_id else None
                        if sports_channel:
                            if wallet in tracked_addresses:
                                tw = tracked_addresses[wallet]
                                if tw.added_at and trade_time and trade_time < tw.added_at:
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
                                    win_rate=wallet_stats.get('win_rate')
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
                        channel = bot.get_channel(config.alert_channel_id) if config.alert_channel_id else None
                        if not channel:
                            continue
                        
                        if wallet in tracked_addresses:
                            tw = tracked_addresses[wallet]
                            if tw.added_at and trade_time and trade_time < tw.added_at:
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
                                win_rate=wallet_stats.get('win_rate')
                            )
                            try:
                                await channel.send(embed=embed, view=button_view)
                            except Exception as e:
                                print(f"Error sending custom wallet alert: {e}")
                        
                        elif is_fresh and value >= config.fresh_wallet_threshold:
                            embed = create_fresh_wallet_alert_embed(
                                trade=trade,
                                value_usd=value,
                                market_title=market_title,
                                wallet_address=wallet,
                                market_url=market_url
                            )
                            try:
                                await channel.send(embed=embed, view=button_view)
                            except Exception as e:
                                print(f"Error sending fresh wallet alert: {e}")
                        
                        elif value >= config.whale_threshold:
                            embed = create_whale_alert_embed(
                                trade=trade,
                                value_usd=value,
                                market_title=market_title,
                                wallet_address=wallet,
                                market_url=market_url
                            )
                            try:
                                await channel.send(embed=embed, view=button_view)
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
                    button_view = create_trade_button_view(market_url)
                    
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
