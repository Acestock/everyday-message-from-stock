"""
Discord 訊息格式化：將 foreign_scraper 的資料轉成 Discord Embed。
"""
from __future__ import annotations

import discord

from foreign_scraper import fetch_foreign_rank, fetch_ma_data, fetch_trust_rank

# MA 技術分析標籤
def _ma_tag(info: dict | None) -> str:
    """回傳簡短的 MA 狀態字串，例如 '↑MA20 ↑MA60' 或 '↓MA20'。"""
    if not info:
        return ""
    parts = []
    if info.get("ma20") is not None:
        arrow = "↑" if info["above_ma20"] else "↓"
        trend = "+" if info["ma20_up"] else "-"
        parts.append(f"{arrow}MA20({trend})")
    if info.get("ma60") is not None:
        arrow = "↑" if info["above_ma60"] else "↓"
        trend = "+" if info["ma60_up"] else "-"
        parts.append(f"{arrow}MA60({trend})")
    return "  " + " ".join(parts) if parts else ""


def _rank_lines(stocks: list[dict], ma_data: dict) -> str:
    """將排名清單轉成多行文字（用於 Embed field value）。"""
    if not stocks:
        return "（無資料）"
    lines = []
    for i, s in enumerate(stocks, 1):
        code = s["code"]
        net  = s["net_k"]
        sign = "+" if net > 0 else ""
        ma   = _ma_tag(ma_data.get(code))
        price_str = ""
        info = ma_data.get(code)
        if info and info.get("price"):
            price_str = f"  ${info['price']}"
        lines.append(
            f"`{i:>2}.` **{s['name']}** ({code})  "
            f"`{sign}{net:,} 張`{price_str}{ma}"
        )
    return "\n".join(lines)


def _build_embed(
    title: str,
    color: discord.Color,
    data_date: str,
    twse_buy: list[dict],
    twse_sell: list[dict],
    tpex_buy: list[dict],
    tpex_sell: list[dict],
    ma_data: dict,
) -> discord.Embed:
    embed = discord.Embed(
        title=title,
        description=f"資料日期：{data_date}",
        color=color,
    )

    # 上市
    if twse_buy:
        embed.add_field(
            name="🟢 上市買超 Top 10",
            value=_rank_lines(twse_buy, ma_data),
            inline=False,
        )
    if twse_sell:
        embed.add_field(
            name="🔴 上市賣超 Top 10",
            value=_rank_lines(twse_sell, ma_data),
            inline=False,
        )

    # 上櫃
    if tpex_buy:
        embed.add_field(
            name="🟢 上櫃買超 Top 10",
            value=_rank_lines(tpex_buy, ma_data),
            inline=False,
        )
    if tpex_sell:
        embed.add_field(
            name="🔴 上櫃賣超 Top 10",
            value=_rank_lines(tpex_sell, ma_data),
            inline=False,
        )

    if not any([twse_buy, twse_sell, tpex_buy, tpex_sell]):
        embed.description = f"資料日期：{data_date}\n\n（今日無資料，可能為非交易日）"

    embed.set_footer(text="資料來源：TWSE / TPEx  |  MA 技術資料：Yahoo Finance")
    return embed


def build_foreign_embed() -> discord.Embed:
    """抓取外資買賣超，回傳格式化的 Discord Embed。"""
    rank = fetch_foreign_rank()
    all_stocks = (
        rank["twse"]["buy"] + rank["twse"]["sell"]
        + rank["tpex"]["buy"] + rank["tpex"]["sell"]
    )
    ma_data = fetch_ma_data([s for s in all_stocks]) if all_stocks else {}

    return _build_embed(
        title="📊 外資買賣超排行",
        color=discord.Color.blue(),
        data_date=rank.get("date", "—"),
        twse_buy=rank["twse"]["buy"],
        twse_sell=rank["twse"]["sell"],
        tpex_buy=rank["tpex"]["buy"],
        tpex_sell=rank["tpex"]["sell"],
        ma_data=ma_data,
    )


def build_trust_embed() -> discord.Embed:
    """抓取投信買賣超，回傳格式化的 Discord Embed。"""
    rank = fetch_trust_rank()
    all_stocks = (
        rank["twse"]["buy"] + rank["twse"]["sell"]
        + rank["tpex"]["buy"] + rank["tpex"]["sell"]
    )
    ma_data = fetch_ma_data([s for s in all_stocks]) if all_stocks else {}

    return _build_embed(
        title="📊 投信買賣超排行",
        color=discord.Color.gold(),
        data_date=rank.get("date", "—"),
        twse_buy=rank["twse"]["buy"],
        twse_sell=rank["twse"]["sell"],
        tpex_buy=rank["tpex"]["buy"],
        tpex_sell=rank["tpex"]["sell"],
        ma_data=ma_data,
    )
