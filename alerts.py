import re
import discord
from discord import Embed
from discord.ui import View, Button
from datetime import datetime
from typing import Dict, Any, Optional


ONSIGHT_BOT_URL = "https://t.me/polysightbot"


def get_market_link(title: str, url: str) -> str:
    if url and url != "https://polymarket.com":
        return f"[{title[:80]}]({url})"
    return title[:80] if title else "Unknown"


def encode_onsight_param(event_slug: str) -> str:
    """Encode event slug for Onsight Telegram bot deep link.
    
    Format: event_{slug_with_underscores}
    - Hyphens replaced with underscores (Telegram requirement)
    - Prefixed with 'event_'
    - Max 64 chars (Telegram limit)
    """
    if not event_slug:
        return ''
    clean_slug = event_slug.split('?')[0].strip('/')
    underscore_slug = clean_slug.replace('-', '_')
    start_payload = f"event_{underscore_slug}"
    if len(start_payload) <= 64:
        return start_payload
    return start_payload[:64]


def create_trade_button_view(onsight_slug: str, market_url: str) -> View:
    view = View()
    encoded_param = encode_onsight_param(onsight_slug)
    if encoded_param:
        onsight_url = f"{ONSIGHT_BOT_URL}?start={encoded_param}"
    else:
        onsight_url = ONSIGHT_BOT_URL
    view.add_item(Button(
        label="Trade via Onsight",
        url=onsight_url,
        style=discord.ButtonStyle.link,
        emoji="📈"
    ))
    view.add_item(Button(
        label="View on Polymarket",
        url=market_url,
        style=discord.ButtonStyle.link,
        emoji="🔗"
    ))
    return view


def create_whale_alert_embed(
    trade: Dict[str, Any],
    value_usd: float,
    market_title: str = "Unknown Market",
    wallet_address: str = "Unknown",
    market_url: str = "https://polymarket.com"
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
    
    market_display = get_market_link(market_title, market_url)
    embed.add_field(
        name="Market",
        value=market_display,
        inline=True
    )
    
    side = trade.get('side', '').upper()
    outcome = trade.get('outcome', '')
    if side and outcome:
        action = f"{side} {outcome}"
    elif side:
        action = side
    else:
        action = "Unknown"
    embed.add_field(
        name="Action",
        value=action,
        inline=True
    )
    
    embed.add_field(
        name="Wallet",
        value=f"`{wallet_address}`",
        inline=False
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
    wallet_address: str = "Unknown",
    market_url: str = "https://polymarket.com"
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
    
    market_display = get_market_link(market_title, market_url)
    embed.add_field(
        name="Market",
        value=market_display,
        inline=True
    )
    
    side = trade.get('side', '').upper()
    outcome = trade.get('outcome', '')
    if side and outcome:
        action = f"{side} {outcome}"
    elif side:
        action = side
    else:
        action = "Unknown"
    embed.add_field(
        name="Action",
        value=action,
        inline=True
    )
    
    embed.add_field(
        name="New Wallet",
        value=f"`{wallet_address}`",
        inline=False
    )
    
    price = trade.get('price', 0)
    embed.add_field(
        name="Price",
        value=f"{float(price)*100:.1f}%" if price else "N/A",
        inline=True
    )
    
    embed.set_footer(text="Polymarket Fresh Wallet Monitor")
    
    return embed


def create_top_trader_alert_embed(
    trade: Dict[str, Any],
    value_usd: float,
    market_title: str = "Unknown Market",
    wallet_address: str = "Unknown",
    market_url: str = "https://polymarket.com",
    trader_info: Optional[Dict[str, Any]] = None
) -> Embed:
    rank = trader_info.get('rank') if trader_info else None
    username = trader_info.get('username') if trader_info else None
    pnl = trader_info.get('pnl') if trader_info else None
    
    title = f"Top Trader Alert"
    if rank:
        title = f"#{rank} Top Trader Alert"
    if username:
        title += f" - {username}"
    
    stats_line = ""
    if pnl is not None:
        pnl_sign = "+" if pnl >= 0 else ""
        stats_line = f"All-Time PnL: {pnl_sign}${pnl:,.0f}\n\n"
    
    embed = Embed(
        title=title,
        description=f"{stats_line}Trade from a top 25 all-time profit leader",
        color=0xFFD700,
        timestamp=datetime.utcnow()
    )
    
    embed.add_field(
        name="Transaction Value",
        value=f"${value_usd:,.2f}",
        inline=True
    )
    
    market_display = get_market_link(market_title, market_url)
    embed.add_field(
        name="Market",
        value=market_display,
        inline=True
    )
    
    side = trade.get('side', '').upper()
    outcome = trade.get('outcome', '')
    if side and outcome:
        action = f"{side} {outcome}"
    elif side:
        action = side
    else:
        action = "Unknown"
    embed.add_field(
        name="Action",
        value=action,
        inline=True
    )
    
    embed.add_field(
        name="Wallet",
        value=f"`{wallet_address}`",
        inline=False
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
    
    embed.set_footer(text="Polymarket Top 25 Trader Monitor")
    
    return embed


def create_custom_wallet_alert_embed(
    trade: Dict[str, Any],
    value_usd: float,
    market_title: str = "Unknown Market",
    wallet_address: str = "Unknown",
    wallet_label: Optional[str] = None,
    market_url: str = "https://polymarket.com",
    pnl: Optional[float] = None,
    volume: Optional[float] = None,
    rank: Optional[str] = None
) -> Embed:
    title = f"Tracked Wallet Alert"
    if wallet_label:
        title += f" - {wallet_label}"
    
    stats_line = ""
    if pnl is not None:
        pnl_sign = "+" if pnl >= 0 else ""
        stats_line = f"📊 {pnl_sign}${pnl:,.0f} PnL"
        if rank:
            stats_line += f" | Rank #{rank}"
        stats_line += "\n\n"
    
    embed = Embed(
        title=title,
        description=f"{stats_line}Activity detected from a tracked wallet",
        color=0xFFE66D,
        timestamp=datetime.utcnow()
    )
    
    embed.add_field(
        name="Transaction Value",
        value=f"${value_usd:,.2f}",
        inline=True
    )
    
    market_display = get_market_link(market_title, market_url)
    embed.add_field(
        name="Market",
        value=market_display,
        inline=True
    )
    
    side = trade.get('side', '').upper()
    outcome = trade.get('outcome', '')
    if side and outcome:
        action = f"{side} {outcome}"
    elif side:
        action = side
    else:
        action = "Unknown"
    embed.add_field(
        name="Action",
        value=action,
        inline=True
    )
    
    embed.add_field(
        name="Wallet",
        value=f"`{wallet_address}`",
        inline=False
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


def create_redeem_alert_embed(
    activity: Dict[str, Any],
    value_usd: float,
    market_title: str = "Unknown Market",
    wallet_address: str = "Unknown",
    wallet_label: Optional[str] = None,
    market_url: str = "https://polymarket.com"
) -> Embed:
    title = f"Redeem Alert"
    if wallet_label:
        title += f" - {wallet_label}"
    
    embed = Embed(
        title=title,
        description=f"Tracked wallet cashed out a winning position",
        color=0x9B59B6,
        timestamp=datetime.utcnow()
    )
    
    embed.add_field(
        name="Redeemed Value",
        value=f"${value_usd:,.2f}",
        inline=True
    )
    
    market_display = get_market_link(market_title, market_url)
    embed.add_field(
        name="Market",
        value=market_display,
        inline=True
    )
    
    outcome = activity.get('outcome', 'Unknown')
    embed.add_field(
        name="Outcome",
        value=outcome.upper() if outcome else "Unknown",
        inline=True
    )
    
    short_wallet = f"{wallet_address[:6]}...{wallet_address[-4:]}" if len(wallet_address) > 10 else wallet_address
    embed.add_field(
        name="Wallet",
        value=f"`{short_wallet}`",
        inline=True
    )
    
    embed.set_footer(text="Polymarket Redeem Monitor")
    
    return embed


def create_positions_overview_embed(
    tracked_wallets: list,
    positions_data: Dict[str, list]
) -> Embed:
    embed = Embed(
        title="Tracked Wallet Positions",
        description="Overview of positions for all tracked wallets",
        color=0x3498DB,
        timestamp=datetime.utcnow()
    )
    
    for wallet in tracked_wallets:
        addr = wallet.wallet_address
        label = wallet.label or f"{addr[:6]}...{addr[-4:]}"
        positions = positions_data.get(addr, [])
        
        if positions:
            def get_pos_value(p):
                return float(p.get('currentValue', 0) or p.get('cashValue', 0) or 0)
            top_positions = sorted(positions, key=get_pos_value, reverse=True)[:3]
            pos_text = []
            for pos in top_positions:
                title = pos.get('title', 'Unknown')[:40]
                value = get_pos_value(pos)
                outcome = pos.get('outcome', '')
                pos_text.append(f"• {title} ({outcome}): ${value:,.0f}")
            
            if len(positions) > 3:
                pos_text.append(f"*...and {len(positions) - 3} more*")
            
            embed.add_field(
                name=label,
                value="\n".join(pos_text) if pos_text else "No positions",
                inline=False
            )
        else:
            embed.add_field(
                name=label,
                value="No positions found",
                inline=False
            )
    
    embed.set_footer(text="Click a button below to see full details")
    
    return embed


def create_wallet_positions_embed(
    wallet_address: str,
    wallet_label: Optional[str],
    positions: list
) -> Embed:
    label = wallet_label or f"{wallet_address[:6]}...{wallet_address[-4:]}"
    
    embed = Embed(
        title=f"Positions - {label}",
        description=f"Full position breakdown for `{wallet_address[:10]}...`",
        color=0x3498DB,
        timestamp=datetime.utcnow()
    )
    
    if not positions:
        embed.add_field(name="No Positions", value="This wallet has no open positions", inline=False)
        return embed
    
    def get_value(p):
        return float(p.get('currentValue', 0) or p.get('cashValue', 0) or 0)
    
    sorted_positions = sorted(positions, key=get_value, reverse=True)
    
    total_value = sum(get_value(p) for p in sorted_positions)
    embed.add_field(name="Total Value", value=f"${total_value:,.2f}", inline=True)
    embed.add_field(name="Position Count", value=str(len(sorted_positions)), inline=True)
    embed.add_field(name="\u200b", value="\u200b", inline=True)
    
    for pos in sorted_positions[:10]:
        title = pos.get('title', 'Unknown')[:50]
        value = get_value(pos)
        size = float(pos.get('size', 0) or 0)
        outcome = pos.get('outcome', 'Unknown')
        avg_price = float(pos.get('avgPrice', 0) or 0) * 100
        current_price = float(pos.get('curPrice', 0) or pos.get('currentPrice', 0) or 0) * 100
        
        field_value = f"**{outcome}** | Size: {size:,.0f} | Value: ${value:,.2f}\nEntry: {avg_price:.1f}% → Current: {current_price:.1f}%"
        
        embed.add_field(
            name=title,
            value=field_value,
            inline=False
        )
    
    if len(sorted_positions) > 10:
        embed.set_footer(text=f"Showing top 10 of {len(sorted_positions)} positions")
    
    return embed


def create_volatility_alert_embed(
    market_title: str,
    slug: str,
    old_price: float,
    new_price: float,
    price_change: float,
    time_window_minutes: int = 60
) -> Embed:
    direction = "up" if price_change > 0 else "down"
    arrow = "+" if price_change > 0 else ""
    color = 0x27AE60 if price_change > 0 else 0xE74C3C
    
    market_url = f"https://polymarket.com/market/{slug}" if slug else "https://polymarket.com"
    market_display = get_market_link(market_title, market_url)
    
    embed = Embed(
        title=f"Volatility Alert",
        description=f"Market moved {arrow}{price_change:.1f}% in {time_window_minutes} minutes",
        color=color,
        timestamp=datetime.utcnow()
    )
    
    embed.add_field(
        name="Market",
        value=market_display,
        inline=False
    )
    
    embed.add_field(
        name="Previous Price",
        value=f"{old_price*100:.1f}%",
        inline=True
    )
    
    embed.add_field(
        name="Current Price",
        value=f"{new_price*100:.1f}%",
        inline=True
    )
    
    embed.add_field(
        name="Change",
        value=f"{arrow}{price_change:.1f}%",
        inline=True
    )
    
    embed.set_footer(text="Polymarket Volatility Monitor")
    
    return embed, market_url


def create_settings_embed(
    guild_name: str,
    channel_name: Optional[str],
    whale_threshold: float,
    fresh_wallet_threshold: float,
    is_paused: bool,
    tracked_wallets: list,
    volatility_channel_name: Optional[str] = None,
    volatility_threshold: float = 20.0,
    sports_channel_name: Optional[str] = None,
    sports_threshold: float = 5000.0,
    wallet_stats: Optional[Dict[str, Any]] = None,
    whale_channel_name: Optional[str] = None,
    fresh_wallet_channel_name: Optional[str] = None,
    tracked_wallet_channel_name: Optional[str] = None
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
        name="Fallback Channel",
        value=f"#{channel_name}" if channel_name else "Not configured",
        inline=True
    )
    
    embed.add_field(
        name="\u200b",
        value="\u200b",
        inline=True
    )
    
    whale_ch = whale_channel_name or channel_name
    embed.add_field(
        name="Whale Alerts",
        value=f"#{whale_ch} (${whale_threshold:,.0f}+)" if whale_ch else "Not configured",
        inline=True
    )
    
    fresh_ch = fresh_wallet_channel_name or channel_name
    embed.add_field(
        name="Fresh Wallet Alerts",
        value=f"#{fresh_ch} (${fresh_wallet_threshold:,.0f}+)" if fresh_ch else "Not configured",
        inline=True
    )
    
    tracked_ch = tracked_wallet_channel_name or channel_name
    embed.add_field(
        name="Tracked Wallet Alerts",
        value=f"#{tracked_ch}" if tracked_ch else "Not configured",
        inline=True
    )
    
    embed.add_field(
        name="Volatility Alerts",
        value=f"#{volatility_channel_name} ({volatility_threshold:.0f}%+)" if volatility_channel_name else "Not configured",
        inline=True
    )
    
    embed.add_field(
        name="Sports Alerts",
        value=f"#{sports_channel_name} (${sports_threshold:,.0f}+)" if sports_channel_name else "Not configured",
        inline=True
    )
    
    embed.add_field(
        name="\u200b",
        value="\u200b",
        inline=True
    )
    
    if tracked_wallets:
        wallet_list = []
        for w in tracked_wallets[:10]:
            addr = w.wallet_address
            short = f"{addr[:6]}...{addr[-4:]}"
            label = f" ({w.label})" if w.label else ""
            
            stats_str = ""
            if wallet_stats:
                stats = wallet_stats.get(addr.lower())
                if stats:
                    pnl = stats.get('pnl', 0)
                    pnl_sign = "+" if pnl >= 0 else ""
                    rank = stats.get('rank')
                    stats_str = f" | {pnl_sign}${pnl:,.0f}"
                    if rank:
                        stats_str += f" | #{rank}"
            
            wallet_list.append(f"`{short}`{label}{stats_str}")
        
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
