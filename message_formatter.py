"""
Discord Webhook 訊息格式化：將 foreign_scraper 的資料轉成 Webhook Embed payload。

Discord embed 限制：
  - field value   ≤ 1024 字元
  - embed 總字元  ≤ 6000
  - 每則訊息 embed ≤ 10 個
"""
from __future__ import annotations

from foreign_scraper import (
    fetch_foreign_rank,
    fetch_ma_data,
    fetch_sector_institutional,
    fetch_trust_rank,
)

COLOR_BLUE  = 0x3498DB
COLOR_GOLD  = 0xF1C40F
COLOR_GREEN = 0x2ECC71

_FOOTER = {"text": "資料來源：TWSE / TPEx  |  MA：Yahoo Finance"}

# 每個 field value 最大字元（Discord 限制 1024，保留緩衝）
_FIELD_CHAR_LIMIT = 900


# ── 張數 k 格式（≥1000 才換算，節省行寬）────────────────────────────────────

def _fmt_k(n: int) -> str:
    """5234 → '+5.2k'  -800 → '-800'  0 → '+0'"""
    sign = "+" if n >= 0 else ""
    if abs(n) >= 1000:
        return f"{sign}{n / 1000:.1f}k"
    return f"{sign}{n}"


# ── MA 標籤（手機友善：短格式）──────────────────────────────────────────────

def _ma_tag(info: dict | None) -> str:
    """↑20▲↑60▲  （上/下 MA × MA 走勢升/降）"""
    if not info:
        return ""
    parts = []
    if info.get("ma20") is not None:
        a = "↑" if info["above_ma20"] else "↓"
        t = "▲" if info["ma20_up"]    else "▼"
        parts.append(f"{a}20{t}")
    if info.get("ma60") is not None:
        a = "↑" if info["above_ma60"] else "↓"
        t = "▲" if info["ma60_up"]    else "▼"
        parts.append(f"{a}60{t}")
    return " " + "".join(parts) if parts else ""


# ── 排行榜格式（每行 ≈ 40 字，10 行 ≈ 400 字，遠低於 1024）───────────────

def _rank_lines(stocks: list[dict], ma_data: dict) -> str:
    if not stocks:
        return "（無資料）"
    lines = []
    for i, s in enumerate(stocks, 1):
        code  = s["code"]
        info  = ma_data.get(code)
        price = f" ${info['price']}" if info and info.get("price") else ""
        ma    = _ma_tag(info)
        # 股名(代號) 粗體，讓眼睛快速定位
        lines.append(f"{i:>2}. **{s['name']}({code})** {_fmt_k(s['net_k'])}張{price}{ma}")
    return "\n".join(lines)


# ── 通用 payload 建構 ─────────────────────────────────────────────────────────

def _build_payload(
    title: str,
    color: int,
    data_date: str,
    twse_buy:  list[dict],
    twse_sell: list[dict],
    tpex_buy:  list[dict],
    tpex_sell: list[dict],
    ma_data:   dict,
) -> dict:
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

    return {"embeds": [{"title": title, "description": description,
                         "color": color, "fields": fields, "footer": _FOOTER}]}


# ── 公開函式 ──────────────────────────────────────────────────────────────────

def build_foreign_payload() -> dict:
    rank = fetch_foreign_rank()
    all_stocks = (rank["twse"]["buy"] + rank["twse"]["sell"]
                  + rank["tpex"]["buy"] + rank["tpex"]["sell"])
    ma_data = fetch_ma_data(all_stocks) if all_stocks else {}
    return _build_payload(
        "📊 外資買賣超排行", COLOR_BLUE, rank.get("date", "—"),
        rank["twse"]["buy"], rank["twse"]["sell"],
        rank["tpex"]["buy"], rank["tpex"]["sell"], ma_data,
    )


def build_trust_payload() -> dict:
    rank = fetch_trust_rank()
    all_stocks = (rank["twse"]["buy"] + rank["twse"]["sell"]
                  + rank["tpex"]["buy"] + rank["tpex"]["sell"])
    ma_data = fetch_ma_data(all_stocks) if all_stocks else {}
    return _build_payload(
        "📊 投信買賣超排行", COLOR_GOLD, rank.get("date", "—"),
        rank["twse"]["buy"], rank["twse"]["sell"],
        rank["tpex"]["buy"], rank["tpex"]["sell"], ma_data,
    )


def build_sector_payload() -> dict:
    """
    法人資金族群彙計：外資＋投信買賣超依產業類別加總。
    顯示買超 / 賣超前 TOP_SECTOR 個產業，每行 ≈ 45 字，遠低於 1024 字元限制。
    """
    data = fetch_sector_institutional()

    def _sector_lines(rows: list[dict]) -> str:
        if not rows:
            return "（無資料）"
        lines = []
        for i, r in enumerate(rows, 1):
            top    = r.get("top_stock")
            leader = f"  ▷**{top['name']}** {_fmt_k(top['net_k'])}張" if top else ""
            # 兩行格式：第一行族群名+總量，第二行外/信分拆+領頭股
            lines.append(
                f"{i:>2}. **{r['sector']}** {_fmt_k(r['total_net'])}張\n"
                f"    外{_fmt_k(r['foreign_net'])}/信{_fmt_k(r['trust_net'])}{leader}"
            )
        return "\n".join(lines)

    fields = []
    if data["buy"]:
        fields.append({
            "name":   "📈 法人合計買超族群",
            "value":  _sector_lines(data["buy"]),
            "inline": False,
        })
    if data["sell"]:
        fields.append({
            "name":   "📉 法人合計賣超族群",
            "value":  _sector_lines(data["sell"]),
            "inline": False,
        })

    embed = {
        "title":       "🏭 法人資金族群彙計",
        "description": f"資料日期：{data.get('date', '—')}\n外資＋投信買賣超按產業加總",
        "color":       COLOR_GREEN,
        "fields":      fields or [{"name": "—", "value": "（今日無資料）", "inline": False}],
        "footer":      _FOOTER,
    }
    return {"embeds": [embed]}
