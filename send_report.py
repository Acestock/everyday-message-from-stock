"""
每日股市資金流向報告：抓取資料 → 渲染成 PNG → 透過 Discord Webhook 上傳圖片。

用法：
  export DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
  python send_report.py

或本地測試：
  cp .env.example .env   # 填入 DISCORD_WEBHOOK_URL
  python send_report.py
"""
from __future__ import annotations

import json
import logging
import os
import sys
import urllib.error
import urllib.request
from typing import Any

from dotenv import load_dotenv

from foreign_scraper import (
    fetch_foreign_rank,
    fetch_ma_data,
    fetch_market_overview,
    fetch_sector_institutional,
    fetch_trust_rank,
)
from image_renderer import render_report_png

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

WEBHOOK_USERNAME   = os.getenv("WEBHOOK_USERNAME",   "台股法人雷達 📡")
WEBHOOK_AVATAR_URL = os.getenv("WEBHOOK_AVATAR_URL", "").strip()


def post_image_to_webhook(webhook_url: str, png_bytes: bytes) -> None:
    """將 PNG 圖片以 multipart/form-data 上傳至 Discord Webhook。"""
    boundary = "----DiscordBoundary" + os.urandom(8).hex()

    payload: dict[str, Any] = {"username": WEBHOOK_USERNAME}
    if WEBHOOK_AVATAR_URL:
        payload["avatar_url"] = WEBHOOK_AVATAR_URL

    request_body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="payload_json"\r\n'
        f"Content-Type: application/json\r\n\r\n"
        f"{json.dumps(payload)}\r\n"
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="files[0]"; filename="daily_report.png"\r\n'
        f"Content-Type: image/png\r\n\r\n"
    ).encode("utf-8") + png_bytes + f"\r\n--{boundary}--\r\n".encode("utf-8")

    req = urllib.request.Request(
        webhook_url,
        data=request_body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": "DiscordBot (https://github.com/acestock/everyday-message-from-stock, 1.0)",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            status = resp.status
            if status not in (200, 204):
                raise RuntimeError(f"Webhook 回應非預期狀態碼: {status}")
        logger.info("圖片發送成功（HTTP %s，%.1f KB）", status, len(png_bytes) / 1024)
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        logger.error("Discord 回應 HTTP %s: %s", e.code, error_body)
        raise


def _mark_dual_institutional(foreign_rank: dict, trust_rank: dict) -> None:
    """
    標記同時出現於外資與投信清單的個股，依買賣方向設 dual 類型：
      dual-buy   外資買超 + 投信買超 → 橘色
      dual-sell  外資賣超 + 投信賣超 → 深綠色
      dual-mixed 一個買超一個賣超   → 深藍色
    """
    f_buy: set[str] = set()
    f_sell: set[str] = set()
    t_buy: set[str] = set()
    t_sell: set[str] = set()
    for market in ("twse", "tpex"):
        for s in foreign_rank.get(market, {}).get("buy",  []): f_buy.add(s["code"])
        for s in foreign_rank.get(market, {}).get("sell", []): f_sell.add(s["code"])
        for s in trust_rank.get(market,   {}).get("buy",  []): t_buy.add(s["code"])
        for s in trust_rank.get(market,   {}).get("sell", []): t_sell.add(s["code"])

    dual_map: dict[str, str] = {}
    for code in f_buy  & t_buy:  dual_map[code] = "dual-buy"
    for code in f_sell & t_sell: dual_map[code] = "dual-sell"
    for code in f_buy  & t_sell: dual_map[code] = "dual-mixed"
    for code in f_sell & t_buy:  dual_map[code] = "dual-mixed"

    if not dual_map:
        return
    for side in ("buy", "sell"):
        for market in ("twse", "tpex"):
            for s in foreign_rank.get(market, {}).get(side, []):
                if s["code"] in dual_map:
                    s["dual"] = dual_map[s["code"]]
            for s in trust_rank.get(market, {}).get(side, []):
                if s["code"] in dual_map:
                    s["dual"] = dual_map[s["code"]]


def main() -> None:
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook_url:
        logger.error("未設定 DISCORD_WEBHOOK_URL，請在環境變數或 .env 中設定")
        sys.exit(1)

    # ── 抓取各項資料（各自 try/except，任一失敗不中斷其他）────────────────────
    overview     = None
    foreign_rank = {"twse": {"buy": [], "sell": []}, "tpex": {"buy": [], "sell": []}, "date": "—"}
    trust_rank   = {"twse": {"buy": [], "sell": []}, "tpex": {"buy": [], "sell": []}, "date": "—"}
    sector_data  = {"buy": [], "sell": [], "date": "—"}
    errors: list[str] = []

    logger.info("抓取大盤總覽...")
    try:
        overview = fetch_market_overview()
    except Exception as e:
        errors.append(f"大盤總覽: {e}")
        logger.error("大盤總覽失敗: %s", e)

    logger.info("抓取外資排行...")
    try:
        foreign_rank = fetch_foreign_rank()
    except Exception as e:
        errors.append(f"外資排行: {e}")
        logger.error("外資排行失敗: %s", e)

    logger.info("抓取投信排行...")
    try:
        trust_rank = fetch_trust_rank()
    except Exception as e:
        errors.append(f"投信排行: {e}")
        logger.error("投信排行失敗: %s", e)

    logger.info("抓取族群彙計...")
    try:
        sector_data = fetch_sector_institutional()
    except Exception as e:
        errors.append(f"族群彙計: {e}")
        logger.error("族群彙計失敗: %s", e)

    _mark_dual_institutional(foreign_rank, trust_rank)

    # ── 批次抓 MA 資料 ────────────────────────────────────────────────────────
    all_stocks = (
        foreign_rank["twse"]["buy"]  + foreign_rank["twse"]["sell"] +
        foreign_rank["tpex"]["buy"]  + foreign_rank["tpex"]["sell"] +
        trust_rank["twse"]["buy"]    + trust_rank["twse"]["sell"] +
        trust_rank["tpex"]["buy"]    + trust_rank["tpex"]["sell"]
    )
    ma_data: dict = {}
    if all_stocks:
        logger.info("抓取 MA 資料（%d 支股票）...", len(all_stocks))
        try:
            ma_data = fetch_ma_data(all_stocks)
        except Exception as e:
            logger.warning("MA 資料失敗（不影響主報告）: %s", e)

    date_str = (
        foreign_rank.get("date")
        or trust_rank.get("date")
        or sector_data.get("date", "—")
    )

    # ── 渲染 PNG ──────────────────────────────────────────────────────────────
    data = {
        "date":     date_str,
        "overview": overview,
        "foreign":  foreign_rank,
        "trust":    trust_rank,
        "sector":   sector_data,
        "ma_data":  ma_data,
    }

    logger.info("渲染 PNG 報告...")
    try:
        png_bytes = render_report_png(data)
    except Exception as e:
        logger.error("PNG 渲染失敗: %s", e)
        sys.exit(1)

    # ── 發送至 Discord ────────────────────────────────────────────────────────
    logger.info("發送至 Discord Webhook...")
    try:
        post_image_to_webhook(webhook_url, png_bytes)
    except Exception as e:
        logger.error("Webhook 發送失敗: %s", e)
        sys.exit(1)

    if errors:
        logger.warning("完成（部分資料缺失：%s）", "；".join(errors))
    else:
        logger.info("所有報告發送完成")


if __name__ == "__main__":
    main()
