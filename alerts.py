import discord
from discord import Embed
from datetime import datetime
from typing import Dict, Any, Optional


def create_whale_alert_embed(
    trade: Dict[str, Any],
    value_usd: float,
    market_title: str = "Unknown Market",
    wallet_address: str = "Unknown"
) -> Embed:
    embed = Embed(
        title="Whale Alert",
        description=f"Large transaction detected on Polymarket",
        color=0xFF6B6B,
        timestamp=datetime.utcnow()
    )
    
    embed.add_field(
        name="Transaction Value",
        value=f"${value_usd:,.2f}",
        inline=True
    )
    
    embed.add_field(
        name="Market",
        value=market_title[:100] if market_title else "Unknown",
        inline=True
    )
    
    side = trade.get('side', 'Unknown')
    embed.add_field(
        name="Side",
        value=side.upper() if side else "Unknown",
        inline=True
    )
    
    short_wallet = f"{wallet_address[:6]}...{wallet_address[-4:]}" if len(wallet_address) > 10 else wallet_address
    embed.add_field(
        name="Wallet",
        value=f"`{short_wallet}`",
        inline=True
    )
    
    price = trade.get('price', 0)
    embed.add_field(
        name="Price",
        value=f"{float(price)*100:.1f}%" if price else "N/A",
        inline=True
    )
    
    size = trade.get('size', 0)
    embed.add_field(
        name="Size",
        value=f"{float(size):,.2f}" if size else "N/A",
        inline=True
    )
    
    embed.set_footer(text="Polymarket Whale Monitor")
    
    return embed


def create_fresh_wallet_alert_embed(
    trade: Dict[str, Any],
    value_usd: float,
    market_title: str = "Unknown Market",
    wallet_address: str = "Unknown"
) -> Embed:
    embed = Embed(
        title="Fresh Wallet Alert",
        description=f"New wallet making first large transaction",
        color=0x4ECDC4,
        timestamp=datetime.utcnow()
    )
    
    embed.add_field(
        name="Transaction Value",
        value=f"${value_usd:,.2f}",
        inline=True
    )
    
    embed.add_field(
        name="Market",
        value=market_title[:100] if market_title else "Unknown",
        inline=True
    )
    
    side = trade.get('side', 'Unknown')
    embed.add_field(
        name="Side",
        value=side.upper() if side else "Unknown",
        inline=True
    )
    
    short_wallet = f"{wallet_address[:6]}...{wallet_address[-4:]}" if len(wallet_address) > 10 else wallet_address
    embed.add_field(
        name="New Wallet",
        value=f"`{short_wallet}`",
        inline=True
    )
    
    price = trade.get('price', 0)
    embed.add_field(
        name="Price",
        value=f"{float(price)*100:.1f}%" if price else "N/A",
        inline=True
    )
    
    embed.set_footer(text="Polymarket Fresh Wallet Monitor")
    
    return embed


def create_custom_wallet_alert_embed(
    trade: Dict[str, Any],
    value_usd: float,
    market_title: str = "Unknown Market",
    wallet_address: str = "Unknown",
    wallet_label: Optional[str] = None
) -> Embed:
    title = f"Tracked Wallet Alert"
    if wallet_label:
        title += f" - {wallet_label}"
    
    embed = Embed(
        title=title,
        description=f"Activity detected from a tracked wallet",
        color=0xFFE66D,
        timestamp=datetime.utcnow()
    )
    
    embed.add_field(
        name="Transaction Value",
        value=f"${value_usd:,.2f}",
        inline=True
    )
    
    embed.add_field(
        name="Market",
        value=market_title[:100] if market_title else "Unknown",
        inline=True
    )
    
    side = trade.get('side', 'Unknown')
    embed.add_field(
        name="Side",
        value=side.upper() if side else "Unknown",
        inline=True
    )
    
    short_wallet = f"{wallet_address[:6]}...{wallet_address[-4:]}" if len(wallet_address) > 10 else wallet_address
    embed.add_field(
        name="Wallet",
        value=f"`{short_wallet}`",
        inline=True
    )
    
    price = trade.get('price', 0)
    embed.add_field(
        name="Price",
        value=f"{float(price)*100:.1f}%" if price else "N/A",
        inline=True
    )
    
    size = trade.get('size', 0)
    embed.add_field(
        name="Size",
        value=f"{float(size):,.2f}" if size else "N/A",
        inline=True
    )
    
    embed.set_footer(text="Polymarket Custom Wallet Monitor")
    
    return embed


def create_settings_embed(
    guild_name: str,
    channel_name: Optional[str],
    whale_threshold: float,
    fresh_wallet_threshold: float,
    is_paused: bool,
    tracked_wallets: list
) -> Embed:
    status = "Paused" if is_paused else "Active"
    status_color = 0xFF6B6B if is_paused else 0x4ECDC4
    
    embed = Embed(
        title=f"Polymarket Monitor Settings",
        description=f"Configuration for {guild_name}",
        color=status_color,
        timestamp=datetime.utcnow()
    )
    
    embed.add_field(
        name="Status",
        value=status,
        inline=True
    )
    
    embed.add_field(
        name="Alert Channel",
        value=f"#{channel_name}" if channel_name else "Not configured",
        inline=True
    )
    
    embed.add_field(
        name="Whale Threshold",
        value=f"${whale_threshold:,.0f}",
        inline=True
    )
    
    embed.add_field(
        name="Fresh Wallet Threshold",
        value=f"${fresh_wallet_threshold:,.0f}",
        inline=True
    )
    
    if tracked_wallets:
        wallet_list = []
        for w in tracked_wallets[:10]:
            addr = w.wallet_address
            short = f"{addr[:6]}...{addr[-4:]}"
            label = f" ({w.label})" if w.label else ""
            wallet_list.append(f"`{short}`{label}")
        
        if len(tracked_wallets) > 10:
            wallet_list.append(f"...and {len(tracked_wallets) - 10} more")
        
        embed.add_field(
            name=f"Tracked Wallets ({len(tracked_wallets)})",
            value="\n".join(wallet_list),
            inline=False
        )
    else:
        embed.add_field(
            name="Tracked Wallets",
            value="None configured",
            inline=False
        )
    
    embed.set_footer(text="Use /help for available commands")
    
    return embed
