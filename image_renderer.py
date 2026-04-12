"""
每日台股報告 HTML 模板 → PNG 截圖（Playwright + Chromium）。

需先安裝：
  pip install playwright
  playwright install chromium --with-deps
"""
from __future__ import annotations

import asyncio
import html as _html
from datetime import datetime

_WEEKDAYS = ["一", "二", "三", "四", "五", "六", "日"]


# ── 格式工具 ──────────────────────────────────────────────────────────────────

def _fmt_k(n: int | float) -> str:
    sign = "+" if n >= 0 else ""
    if abs(n) >= 1000:
        return f"{sign}{n / 1000:.1f}k"
    return f"{sign}{int(n)}"


def _ma_html(info: dict | None) -> str:
    """
    MA 位置標籤（只顯示在線上/下，不顯示均線走勢方向）。
    台股慣例：紅=在均線上（多頭）  綠=在均線下（空頭）
    格式：↑20 ↑60
    """
    if not info:
        return ""
    parts = []
    if info.get("ma20") is not None:
        cls   = "pos" if info["above_ma20"] else "neg"
        arrow = "↑" if info["above_ma20"] else "↓"
        parts.append(f'<span class="{cls}">{arrow}20</span>')
    if info.get("ma60") is not None:
        cls   = "pos" if info["above_ma60"] else "neg"
        arrow = "↑" if info["above_ma60"] else "↓"
        parts.append(f'<span class="{cls}">{arrow}60</span>')
    return " ".join(parts)


# ── CSS（淺色主題，台股紅漲綠跌）─────────────────────────────────────────────

_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: 'Noto Sans CJK TC', 'Noto Sans TC', 'PingFang TC',
               'Microsoft JhengHei', 'WenQuanYi Micro Hei', sans-serif;
  background: #f6f8fa;
  color: #1f2328;
  padding: 14px;
  width: 1120px;
  font-size: 12.5px;
  line-height: 1.5;
}

/* ── Header ── */
.hdr {
  display: flex; justify-content: space-between; align-items: center;
  background: linear-gradient(90deg, #ffffff, #f0f6ff);
  border: 1px solid #d0d7de; border-radius: 8px;
  padding: 12px 18px; margin-bottom: 10px;
  box-shadow: 0 1px 3px rgba(0,0,0,.06);
}
.hdr-title { font-size: 18px; font-weight: 700; color: #0969da; }
.hdr-date  { color: #57606a; font-size: 12.5px; font-weight: 500; }

/* ── Card ── */
.card {
  background: #ffffff; border: 1px solid #d0d7de;
  border-radius: 8px; padding: 12px 14px; margin-bottom: 10px;
  box-shadow: 0 1px 3px rgba(0,0,0,.04);
}
.card-title {
  font-size: 14px; font-weight: 700; color: #0969da;
  padding-bottom: 8px; margin-bottom: 8px;
  border-bottom: 1px solid #eaeef2;
}

/* ── 大盤總覽 ── */
.ov-grid  { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 16px; }
.ov-label { font-size: 11px; color: #57606a; font-weight: 600;
            text-transform: uppercase; letter-spacing: .4px; margin-bottom: 4px; }
.ov-val   { font-size: 22px; font-weight: 700; }
.ov-sub   { font-size: 12px; margin-top: 3px; font-weight: 600; }
.insti-row { display: flex; gap: 16px; margin-top: 6px; flex-wrap: wrap; }
.ii-label  { font-size: 10.5px; color: #57606a; }
.ii-val    { font-size: 13.5px; font-weight: 700; }

/* ── 排行榜 ── */
.rank-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.badge {
  display: inline-block; font-size: 11px; color: #57606a;
  background: #f6f8fa; border: 1px solid #d0d7de; border-radius: 4px;
  padding: 1px 7px; margin: 6px 0 4px; font-weight: 600;
}
.srow {
  display: flex; align-items: center;
  padding: 3px 0; border-bottom: 1px solid #f6f8fa; gap: 3px;
}
.srow:last-child { border-bottom: none; }
.rn    { color: #8c959f; width: 22px; flex-shrink: 0; font-size: 11.5px; }
.sname { font-weight: 700; color: #1f2328; flex: 1; min-width: 0;
         white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.scode { color: #57606a; font-size: 10.5px; font-weight: 400; }
.net   { font-weight: 700; width: 56px; text-align: right; flex-shrink: 0; font-size: 12.5px; }
.price { color: #0550ae; font-size: 13.5px; font-weight: 700;
         width: 58px; text-align: right; flex-shrink: 0; }
.ma-wrap { font-size: 11px; width: 70px; flex-shrink: 0; font-weight: 700; }
.no-data { color: #8c959f; font-style: italic; font-size: 12px; padding: 4px 0; }

/* ── 族群彙計 ── */
.sc-grid   { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.sec-title { font-size: 11px; font-weight: 700; color: #57606a;
             letter-spacing: .5px; margin-bottom: 5px; }
.scrow { padding: 3.5px 0; border-bottom: 1px solid #f6f8fa; }
.scrow:last-child { border-bottom: none; }
.sc-main { display: flex; align-items: baseline; gap: 4px; }
.sc-name { font-weight: 700; color: #1f2328; flex: 1; }
.sc-sub  { font-size: 11px; color: #57606a; margin-top: 1px; }
.sc-ldr  { color: #1f2328; font-weight: 700; }

/* ── 顏色（台股慣例：紅漲綠跌）── */
.pos { color: #cf222e; }   /* 紅＝漲 / 買超 / 在均線上 */
.neg { color: #1a7f37; }   /* 綠＝跌 / 賣超 / 在均線下 */
.blu { color: #0969da; }
.gld { color: #9a6700; }

/* ── Footer ── */
.footer { text-align: center; color: #8c959f; font-size: 11px; margin-top: 6px; }
"""


# ── HTML 區塊建構 ─────────────────────────────────────────────────────────────

def _stock_rows(stocks: list[dict], ma_data: dict, color: str) -> str:
    if not stocks:
        return '<div class="no-data">（無資料）</div>'
    rows = []
    for i, s in enumerate(stocks, 1):
        info  = ma_data.get(s["code"])
        price = f'${info["price"]}' if info and info.get("price") else ""
        ma    = _ma_html(info)
        rows.append(
            f'<div class="srow">'
            f'<span class="rn">{i}.</span>'
            f'<span class="sname">{_html.escape(s["name"])}'
            f'<span class="scode"> ({s["code"]})</span></span>'
            f'<span class="net {color}">{_fmt_k(s["net_k"])}張</span>'
            f'<span class="price">{price}</span>'
            f'<span class="ma-wrap">{ma}</span>'
            f'</div>'
        )
    return "\n".join(rows)


def _rank_block(title: str, rank: dict, ma_data: dict, buy_color: str) -> str:
    return (
        f'<div class="card">'
        f'<div class="card-title">{title}</div>'
        f'<div class="rank-grid">'
        f'<div>'
        f'<span class="badge">🟢 上市買超 Top 10</span>'
        f'{_stock_rows(rank["twse"]["buy"], ma_data, buy_color)}'
        f'<span class="badge" style="margin-top:10px">🟢 上櫃買超 Top 10</span>'
        f'{_stock_rows(rank["tpex"]["buy"], ma_data, buy_color)}'
        f'</div>'
        f'<div>'
        f'<span class="badge">🔴 上市賣超 Top 10</span>'
        f'{_stock_rows(rank["twse"]["sell"], ma_data, "neg")}'
        f'<span class="badge" style="margin-top:10px">🔴 上櫃賣超 Top 10</span>'
        f'{_stock_rows(rank["tpex"]["sell"], ma_data, "neg")}'
        f'</div>'
        f'</div>'
        f'</div>'
    )


def _overview_block(ov: dict | None) -> str:
    ov = ov or {}
    idx   = ov.get("index")
    insti = ov.get("insti")
    fut   = ov.get("futures")

    if idx:
        chg_c = "pos" if idx["change"] >= 0 else "neg"
        sign  = "+" if idx["change"] >= 0 else ""
        arr   = "▲" if idx["change"] >= 0 else "▼"
        idx_h = (
            f'<div class="ov-val {chg_c}">{idx["close"]:,.2f}</div>'
            f'<div class="ov-sub {chg_c}">'
            f'{arr} {sign}{idx["change"]:,.2f} ({sign}{idx["change_pct"]:.2f}%)'
            f'</div>'
        )
    else:
        idx_h = '<div class="ov-val" style="color:#8c959f">—</div>'

    if insti:
        total = insti.get("total", 0)
        tc    = "pos" if total >= 0 else "neg"
        sign  = "+" if total >= 0 else ""
        parts = []
        for k, lbl in [("foreign", "外資"), ("trust", "投信"), ("dealer", "自營")]:
            if k in insti:
                s  = "+" if insti[k] >= 0 else ""
                vc = "pos" if insti[k] >= 0 else "neg"
                parts.append(
                    f'<div><div class="ii-label">{lbl}</div>'
                    f'<div class="ii-val {vc}">{s}{insti[k]:.1f}億</div></div>'
                )
        ins_h = (
            f'<div class="ov-val {tc}">{sign}{total:.1f}億</div>'
            f'<div class="insti-row">{"".join(parts)}</div>'
        )
    else:
        ins_h = '<div class="ov-val" style="color:#8c959f">—</div>'

    if fut:
        net  = fut["net"]
        fc   = "blu" if net >= 0 else "neg"
        sign = "+" if net >= 0 else ""
        fut_h = (
            f'<div class="ov-val {fc}">{sign}{net:,}口</div>'
            f'<div class="ov-sub" style="color:#57606a">'
            f'多 {fut["long_oi"]:,} ／ 空 {fut["short_oi"]:,}'
            f'</div>'
        )
    else:
        fut_h = '<div class="ov-val" style="color:#8c959f">—</div>'

    return (
        f'<div class="card">'
        f'<div class="card-title">📈 大盤總覽</div>'
        f'<div class="ov-grid">'
        f'<div><div class="ov-label">加權指數</div>{idx_h}</div>'
        f'<div><div class="ov-label">三大法人合計</div>{ins_h}</div>'
        f'<div><div class="ov-label">外資台指期 淨多單</div>{fut_h}</div>'
        f'</div>'
        f'</div>'
    )


def _sector_block(sec: dict | None) -> str:
    sec = sec or {}

    def rows(data: list[dict], color: str) -> str:
        if not data:
            return '<div class="no-data">（無資料）</div>'
        out = []
        for i, r in enumerate(data, 1):
            top    = r.get("top_stock")
            leader = ""
            if top:
                leader = (
                    f' ▷ <span class="sc-ldr">{_html.escape(top["name"])}</span>'
                    f' <span class="{color}">{_fmt_k(top["net_k"])}張</span>'
                )
            out.append(
                f'<div class="scrow">'
                f'<div class="sc-main">'
                f'<span class="rn">{i}.</span>'
                f'<span class="sc-name">{_html.escape(r["sector"])}</span>'
                f'<span class="{color}" style="font-weight:700">{_fmt_k(r["total_net"])}張</span>'
                f'</div>'
                f'<div class="sc-sub">'
                f'外{_fmt_k(r["foreign_net"])} / 信{_fmt_k(r["trust_net"])}{leader}'
                f'</div>'
                f'</div>'
            )
        return "\n".join(out)

    return (
        f'<div class="card">'
        f'<div class="card-title">🏭 法人資金族群彙計</div>'
        f'<div class="sc-grid">'
        f'<div>'
        f'<div class="sec-title">📈 法人合計買超族群</div>'
        f'{rows(sec.get("buy", []), "pos")}'
        f'</div>'
        f'<div>'
        f'<div class="sec-title">📉 法人合計賣超族群</div>'
        f'{rows(sec.get("sell", []), "neg")}'
        f'</div>'
        f'</div>'
        f'</div>'
    )


def build_report_html(data: dict) -> str:
    """將報告資料 dict 轉成完整 HTML 字串。"""
    date_str = data.get("date", "—")
    try:
        parts = date_str.replace("-", "/").split("/")
        if len(parts) == 3:
            dt = datetime(int(parts[0]), int(parts[1]), int(parts[2]))
            date_display = f"{date_str}（週{_WEEKDAYS[dt.weekday()]}）"
        else:
            date_display = date_str
    except Exception:
        date_display = date_str

    ma   = data.get("ma_data", {})
    body = "\n".join([
        _overview_block(data.get("overview")),
        _rank_block("📊 外資買賣超排行", data["foreign"], ma, "pos"),
        _rank_block("📊 投信買賣超排行", data["trust"],   ma, "gld"),
        _sector_block(data.get("sector")),
    ])

    return (
        f'<!DOCTYPE html><html lang="zh-TW">'
        f'<head><meta charset="utf-8"><style>{_CSS}</style></head>'
        f'<body>'
        f'<div class="hdr">'
        f'<div class="hdr-title">台股法人雷達 📡</div>'
        f'<div class="hdr-date">{date_display}</div>'
        f'</div>'
        f'{body}'
        f'<div class="footer">資料來源：TWSE / TPEx  ｜  MA：Yahoo Finance</div>'
        f'</body></html>'
    )


# ── 渲染 ──────────────────────────────────────────────────────────────────────

def render_report_png(data: dict) -> bytes:
    """同步入口：將報告資料渲染成 PNG bytes（需先安裝 playwright + chromium）。"""
    return asyncio.run(_async_render(build_report_html(data)))


async def _async_render(html_content: str) -> bytes:
    from playwright.async_api import async_playwright  # lazy import
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page    = await browser.new_page(
            viewport={"width": 1120, "height": 800},
            device_scale_factor=2,   # 2× 解析度，截圖更清晰
        )
        await page.set_content(html_content, wait_until="networkidle", timeout=30_000)
        png = await page.screenshot(full_page=True)
        await browser.close()
    return png
