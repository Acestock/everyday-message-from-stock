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
  position: relative; overflow: hidden;
}
.card-title {
  font-size: 14px; font-weight: 700; color: #0969da;
  padding-bottom: 8px; margin-bottom: 8px;
  border-bottom: 1px solid #eaeef2;
  position: relative; z-index: 1;
}

/* ── 浮水印 ── */
.watermark {
  position: absolute; top: 50%; left: 50%;
  transform: translate(-50%, -50%) rotate(-25deg);
  font-size: 56px; font-weight: 900; letter-spacing: 6px;
  color: rgba(160, 160, 160, 0.13);
  white-space: nowrap; pointer-events: none;
  user-select: none; z-index: 0;
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

/* ── 加權指數 chip 列 (MA / 量比) ── */
.ov-chips { margin-top: 6px; display: flex; gap: 6px; flex-wrap: wrap; }
.ma-chip, .vol-chip {
  font-size: 11px; font-weight: 700; padding: 2px 7px;
  border-radius: 10px; background: #f6f8fa; border: 1px solid #d0d7de;
  color: #57606a;
}
.ma-chip.pos { color: #cf222e; background: #ffebe9; border-color: #ffcecb; }
.ma-chip.neg { color: #1a7f37; background: #dafbe1; border-color: #b7efca; }

/* ── 大盤 macro ribbon ── */
.macro-ribbon {
  margin-top: 12px; padding: 8px 12px;
  display: grid; grid-template-columns: 1fr 1fr; gap: 12px;
  background: #f6f8fa; border-radius: 8px;
  font-size: 12.5px; border: 1px solid #eaeef2;
}
.mr-cell { display: flex; align-items: baseline; gap: 4px; flex-wrap: wrap; }
.mr-lbl  { color: #57606a; font-weight: 600; margin-right: 4px; }
.mr-aux  { color: #8c959f; font-size: 11px; margin-left: 6px; }

/* ── 排行榜 ── */
.rank-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; position: relative; z-index: 1; }
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
.sname.dual-buy  { color: #e07000; }   /* 外資+投信雙買超 */
.sname.dual-sell { color: #0a5c2e; }   /* 外資+投信雙賣超 */
.sname.dual-mixed { color: #0550ae; }  /* 外資/投信一買一賣 */
.scode { color: #57606a; font-size: 10.5px; font-weight: 400; }
.net   { font-weight: 700; width: 56px; text-align: right; flex-shrink: 0; font-size: 12.5px; }
.price { color: #0550ae; font-size: 12.5px; font-weight: 700;
         width: 52px; text-align: right; flex-shrink: 0; }
.chg   { font-size: 11.5px; font-weight: 700; width: 46px; text-align: right; flex-shrink: 0; }
.ma-wrap { font-size: 11px; width: 70px; flex-shrink: 0; font-weight: 700; }
.no-data { color: #8c959f; font-style: italic; font-size: 12px; padding: 4px 0; }

/* ── 連續天數標記 ── */
.streak-cnt {
  font-size: 10px; font-weight: 700; color: #9a6700;
  background: #fff8c5; border: 1px solid #d4a72c;
  border-radius: 3px; padding: 0 3px; margin-left: 2px;
  white-space: nowrap; flex-shrink: 0;
}
.streak-flip {
  font-size: 10px; font-weight: 700;
  border-radius: 3px; padding: 0 3px; margin-left: 2px;
  white-space: nowrap; flex-shrink: 0;
}
.streak-flip.pos { color: #cf222e; background: #ffebe9; border: 1px solid #ffcecb; }
.streak-flip.neg { color: #1a7f37; background: #dafbe1; border: 1px solid #b7efca; }

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

/* ── 圖例列 ── */
.legend {
  background: #ffffff; border: 1px solid #d0d7de; border-radius: 8px;
  padding: 7px 14px; margin-bottom: 8px;
  font-size: 11px; color: #57606a;
  display: flex; flex-wrap: wrap; align-items: center; gap: 3px 8px;
}
.lg-title { font-weight: 700; color: #1f2328; margin-right: 2px; }
.lg-grp   { display: flex; align-items: center; gap: 3px; flex-wrap: wrap; }
.lg-sep   { color: #d0d7de; font-size: 14px; margin: 0 2px; }
.lg-dot   { font-size: 14px; line-height: 1; }
.lg-lbl   { white-space: nowrap; }

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

        # 連續天數 / 反轉標記
        streak   = s.get("streak", 0)
        prev_dir = s.get("prev_dir")
        if prev_dir:
            # 昨賣→今買（正面轉折）紅；昨買→今賣（負面轉折）綠
            flip_cls = "pos" if prev_dir == "昨賣" else "neg"
            badge = f'<span class="streak-flip {flip_cls}">{prev_dir}</span>'
        elif streak:
            badge = f'<span class="streak-cnt">{streak}d</span>'
        else:
            badge = ""

        dual    = s.get("dual", "")
        dual_cls = f" {dual}" if dual else ""

        chg_pct = info.get("change_pct") if info else None
        if chg_pct is not None:
            chg_sign = "+" if chg_pct >= 0 else ""
            chg_cls  = "pos" if chg_pct >= 0 else "neg"
            chg_html = f'<span class="chg {chg_cls}">{chg_sign}{chg_pct:.2f}%</span>'
        else:
            chg_html = '<span class="chg"></span>'

        rows.append(
            f'<div class="srow">'
            f'<span class="rn">{i}.</span>'
            f'<span class="sname{dual_cls}">{_html.escape(s["name"])}'
            f'<span class="scode"> ({s["code"]})</span></span>'
            f'{badge}'
            f'<span class="net {color}">{_fmt_k(s["net_k"])}張</span>'
            f'<span class="price">{price}</span>'
            f'{chg_html}'
            f'<span class="ma-wrap">{ma}</span>'
            f'</div>'
        )
    return "\n".join(rows)


def _rank_block(title: str, rank: dict, ma_data: dict, buy_color: str) -> str:
    return (
        f'<div class="card">'
        f'<div class="watermark">艾斯DC討論區</div>'
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
    idx     = ov.get("index")
    insti   = ov.get("insti")
    fut     = ov.get("futures")
    breadth = ov.get("breadth")

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
        chips: list[str] = []
        if idx.get("above_ma20") is not None:
            cls = "pos" if idx["above_ma20"] else "neg"
            arr = "↑" if idx["above_ma20"] else "↓"
            chips.append(f'<span class="ma-chip {cls}">{arr}MA20</span>')
        if idx.get("above_ma60") is not None:
            cls = "pos" if idx["above_ma60"] else "neg"
            arr = "↑" if idx["above_ma60"] else "↓"
            chips.append(f'<span class="ma-chip {cls}">{arr}MA60</span>')
        if idx.get("vol_ratio") is not None:
            chips.append(f'<span class="vol-chip">量比 {idx["vol_ratio"]:.2f}x</span>')
        if chips:
            idx_h += f'<div class="ov-chips">{"".join(chips)}</div>'
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
        lo, so = fut.get("long_oi", 0), fut.get("short_oi", 0)
        sub = (
            f'多 {lo:,} ／ 空 {so:,}'
            if (lo or so) else
            f'淨多單 {sign}{net:,}'
        )
        fut_h = (
            f'<div class="ov-val {fc}">{sign}{net:,}口</div>'
            f'<div class="ov-sub" style="color:#57606a">{sub}</div>'
        )
    else:
        fut_h = '<div class="ov-val" style="color:#8c959f">—</div>'

    ribbon_cells: list[str] = []
    if breadth and "up" in breadth and "down" in breadth:
        lu = breadth.get("limit_up", 0)
        ld = breadth.get("limit_down", 0)
        aux = (f'<span class="mr-aux">漲停 {lu} 跌停 {ld}</span>'
               if (lu or ld) else "")
        ribbon_cells.append(
            f'<div class="mr-cell">'
            f'<span class="mr-lbl">漲跌家數</span>'
            f'<span class="pos">▲ {breadth["up"]:,}</span>'
            f'<span style="color:#57606a;margin:0 4px">/</span>'
            f'<span class="neg">▼ {breadth["down"]:,}</span>'
            f'{aux}'
            f'</div>'
        )
    if fut and fut.get("delta") is not None:
        d   = fut["delta"]
        cls = "pos" if d >= 0 else "neg"
        sgn = "+" if d >= 0 else ""
        ribbon_cells.append(
            f'<div class="mr-cell">'
            f'<span class="mr-lbl">外資期 Δ vs 昨</span>'
            f'<span class="{cls}">{sgn}{d:,} 口</span>'
            f'</div>'
        )
    ribbon_html = (f'<div class="macro-ribbon">{"".join(ribbon_cells)}</div>'
                   if ribbon_cells else "")

    return (
        f'<div class="card">'
        f'<div class="card-title">📈 大盤總覽</div>'
        f'<div class="ov-grid">'
        f'<div><div class="ov-label">加權指數</div>{idx_h}</div>'
        f'<div><div class="ov-label">三大法人合計</div>{ins_h}</div>'
        f'<div><div class="ov-label">外資台指期 淨多單</div>{fut_h}</div>'
        f'</div>'
        f'{ribbon_html}'
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
        f'<div class="legend">'
        f'<span class="lg-title">顏色說明</span>'
        f'<span class="lg-sep">｜</span>'
        f'<span class="lg-grp">'
        f'<span class="lg-lbl" style="font-weight:600">個股名稱：</span>'
        f'<span class="lg-dot" style="color:#e07000">■</span><span class="lg-lbl">外資+投信雙買超</span>'
        f'<span class="lg-dot" style="color:#0a5c2e">■</span><span class="lg-lbl">外資+投信雙賣超</span>'
        f'<span class="lg-dot" style="color:#0550ae">■</span><span class="lg-lbl">外資/投信分歧</span>'
        f'</span>'
        f'<span class="lg-sep">｜</span>'
        f'<span class="lg-grp">'
        f'<span class="lg-lbl" style="font-weight:600">數字：</span>'
        f'<span class="lg-dot pos">■</span><span class="lg-lbl">漲／買超／均線上</span>'
        f'<span class="lg-dot neg">■</span><span class="lg-lbl">跌／賣超／均線下</span>'
        f'</span>'
        f'<span class="lg-sep">｜</span>'
        f'<span class="lg-grp">'
        f'<span class="lg-lbl" style="font-weight:600">標記：</span>'
        f'<span class="streak-cnt">Nd</span><span class="lg-lbl"> 連續N天上榜</span>'
        f'<span class="streak-flip pos">昨賣</span><span class="lg-lbl"> 由賣轉買</span>'
        f'<span class="streak-flip neg">昨買</span><span class="lg-lbl"> 由買轉賣</span>'
        f'</span>'
        f'</div>'
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
