"""
Discord Webhook 訊息格式化：將 foreign_scraper 的資料轉成 Webhook Embed payload。
"""
from __future__ import annotations

from foreign_scraper import (
    fetch_foreign_rank,
    fetch_institutional_active,
    fetch_ma_data,
    fetch_trust_rank,
)

# Discord embed 顏色（十進位）
COLOR_BLUE  = 0x3498DB   # 外資
COLOR_GOLD  = 0xF1C40F   # 投信
COLOR_GREEN = 0x2ECC71   # 法人積極資金族群


def _ma_tag(info: dict | None) -> str:
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
    if not stocks:
        return "（無資料）"
    lines = []
    for i, s in enumerate(stocks, 1):
        code = s["code"]
        net  = s["net_k"]
        sign = "+" if net > 0 else ""
        info = ma_data.get(code)
        price_str = f"  ${info['price']}" if info and info.get("price") else ""
        ma = _ma_tag(info)
        lines.append(
            f"`{i:>2}.` **{s['name']}** ({code})  "
            f"`{sign}{net:,} 張`{price_str}{ma}"
        )
    return "\n".join(lines)


def _build_payload(
    title: str,
    color: int,
    data_date: str,
    twse_buy: list[dict],
    twse_sell: list[dict],
    tpex_buy: list[dict],
    tpex_sell: list[dict],
    ma_data: dict,
) -> dict:
    """回傳 Discord Webhook 的 JSON payload（含單一 embed）。"""
    fields = []
    if twse_buy:
        fields.append({"name": "🟢 上市買超 Top 10",
                        "value": _rank_lines(twse_buy,  ma_data), "inline": False})
    if twse_sell:
        fields.append({"name": "🔴 上市賣超 Top 10",
                        "value": _rank_lines(twse_sell, ma_data), "inline": False})
    if tpex_buy:
        fields.append({"name": "🟢 上櫃買超 Top 10",
                        "value": _rank_lines(tpex_buy,  ma_data), "inline": False})
    if tpex_sell:
        fields.append({"name": "🔴 上櫃賣超 Top 10",
                        "value": _rank_lines(tpex_sell, ma_data), "inline": False})

    description = f"資料日期：{data_date}"
    if not fields:
        description += "\n\n（今日無資料，可能為非交易日）"

    embed = {
        "title": title,
        "description": description,
        "color": color,
        "fields": fields,
        "footer": {"text": "資料來源：TWSE / TPEx  |  MA 技術資料：Yahoo Finance"},
    }
    return {"embeds": [embed]}


def build_foreign_payload() -> dict:
    """抓取外資買賣超，回傳 Discord Webhook payload。"""
    rank = fetch_foreign_rank()
    all_stocks = (
        rank["twse"]["buy"] + rank["twse"]["sell"]
        + rank["tpex"]["buy"] + rank["tpex"]["sell"]
    )
    ma_data = fetch_ma_data(all_stocks) if all_stocks else {}
    return _build_payload(
        title="📊 外資買賣超排行",
        color=COLOR_BLUE,
        data_date=rank.get("date", "—"),
        twse_buy=rank["twse"]["buy"],
        twse_sell=rank["twse"]["sell"],
        tpex_buy=rank["tpex"]["buy"],
        tpex_sell=rank["tpex"]["sell"],
        ma_data=ma_data,
    )


def build_trust_payload() -> dict:
    """抓取投信買賣超，回傳 Discord Webhook payload。"""
    rank = fetch_trust_rank()
    all_stocks = (
        rank["twse"]["buy"] + rank["twse"]["sell"]
        + rank["tpex"]["buy"] + rank["tpex"]["sell"]
    )
    ma_data = fetch_ma_data(all_stocks) if all_stocks else {}
    return _build_payload(
        title="📊 投信買賣超排行",
        color=COLOR_GOLD,
        data_date=rank.get("date", "—"),
        twse_buy=rank["twse"]["buy"],
        twse_sell=rank["twse"]["sell"],
        tpex_buy=rank["tpex"]["buy"],
        tpex_sell=rank["tpex"]["sell"],
        ma_data=ma_data,
    )


def build_institutional_active_payload() -> dict:
    """
    法人積極資金族群：外資＋投信同日雙買超。
    回傳 Discord Webhook payload。
    """
    data = fetch_institutional_active()
    stocks = data["stocks"]
    ma_data = fetch_ma_data(stocks) if stocks else {}

    if stocks:
        lines = []
        for i, s in enumerate(stocks, 1):
            code   = s["code"]
            info   = ma_data.get(code)
            price_str = f"  ${info['price']}" if info and info.get("price") else ""
            ma     = _ma_tag(info)
            lines.append(
                f"`{i:>2}.` **{s['name']}** ({code}) [{s['market']}]  "
                f"`合計 +{s['total_net_k']:,} 張`  "
                f"外資 +{s['foreign_net_k']:,} / 投信 +{s['trust_net_k']:,}"
                f"{price_str}{ma}"
            )
        field_value = "\n".join(lines)
    else:
        field_value = "（今日無外資投信同步買超個股）"

    embed = {
        "title": "🏦 法人積極資金族群",
        "description": (
            f"資料日期：{data.get('date', '—')}\n"
            f"外資＋投信同日雙買超，按合計買超張數排序（Top {len(stocks)}）"
        ),
        "color": COLOR_GREEN,
        "fields": [
            {
                "name": "📈 外資＋投信同步加碼",
                "value": field_value,
                "inline": False,
            }
        ],
        "footer": {"text": "資料來源：TWSE / TPEx  |  MA 技術資料：Yahoo Finance"},
    }
    return {"embeds": [embed]}
