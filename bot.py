import os
import discord
from discord import app_commands
from discord.ext import commands, tasks
import asyncio
from datetime import datetime
from typing import Optional

from database import init_db, get_session, ServerConfig, TrackedWallet, SeenTransaction, WalletActivity
from polymarket_client import polymarket_client
from alerts import (
    create_whale_alert_embed,
    create_fresh_wallet_alert_embed,
    create_custom_wallet_alert_embed,
    create_settings_embed
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
        
        embed = create_settings_embed(
            guild_name=interaction.guild.name,
            channel_name=channel_name,
            whale_threshold=config.whale_threshold,
            fresh_wallet_threshold=config.fresh_wallet_threshold,
            is_paused=config.is_paused,
            tracked_wallets=tracked
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


@bot.tree.command(name="help", description="Show available commands")
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Polymarket Monitor - Help",
        description="Monitor Polymarket activity in your Discord server",
        color=0x4ECDC4
    )
    
    embed.add_field(
        name="/setup #channel",
        value="Set the channel for alerts",
        inline=False
    )
    embed.add_field(
        name="/threshold <amount>",
        value="Set the minimum USD value for alerts (default: $10,000)",
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
    
    embed.set_footer(text="Administrator permissions required for configuration commands")
    
    await interaction.response.send_message(embed=embed, ephemeral=True)


@tasks.loop(seconds=30)
async def monitor_loop():
    try:
        trades = await polymarket_client.get_recent_trades(limit=50)
        
        if not trades:
            return
        
        session = get_session()
        try:
            configs = session.query(ServerConfig).filter(
                ServerConfig.alert_channel_id.isnot(None),
                ServerConfig.is_paused == False
            ).all()
            
            if not configs:
                return
            
            processed_wallets_this_batch = set()
            
            for trade in trades:
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
                
                wallet_activity = session.query(WalletActivity).filter_by(wallet_address=wallet).first()
                is_fresh = wallet_activity is None and wallet not in processed_wallets_this_batch
                
                if wallet not in processed_wallets_this_batch:
                    if wallet_activity is None:
                        session.add(WalletActivity(wallet_address=wallet, transaction_count=1))
                    else:
                        wallet_activity.transaction_count += 1
                    processed_wallets_this_batch.add(wallet)
                
                for config in configs:
                    channel = bot.get_channel(config.alert_channel_id)
                    if not channel:
                        continue
                    
                    tracked_wallets = session.query(TrackedWallet).filter_by(guild_id=config.guild_id).all()
                    tracked_addresses = {tw.wallet_address: tw for tw in tracked_wallets}
                    
                    if wallet in tracked_addresses:
                        tw = tracked_addresses[wallet]
                        embed = create_custom_wallet_alert_embed(
                            trade=trade,
                            value_usd=value,
                            market_title=market_title,
                            wallet_address=wallet,
                            wallet_label=tw.label
                        )
                        try:
                            await channel.send(embed=embed)
                        except Exception as e:
                            print(f"Error sending custom wallet alert: {e}")
                    
                    elif is_fresh and value >= config.fresh_wallet_threshold:
                        embed = create_fresh_wallet_alert_embed(
                            trade=trade,
                            value_usd=value,
                            market_title=market_title,
                            wallet_address=wallet
                        )
                        try:
                            await channel.send(embed=embed)
                        except Exception as e:
                            print(f"Error sending fresh wallet alert: {e}")
                    
                    elif value >= config.whale_threshold:
                        embed = create_whale_alert_embed(
                            trade=trade,
                            value_usd=value,
                            market_title=market_title,
                            wallet_address=wallet
                        )
                        try:
                            await channel.send(embed=embed)
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


@setup.error
@threshold.error
@track.error
@untrack.error
@pause.error
@resume.error
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
