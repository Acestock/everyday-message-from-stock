"""
每日股市資金流向報告：透過 Discord Webhook 發送外資 / 投信買賣超 + 法人積極資金族群。

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

from message_formatter import (
    build_foreign_payload,
    build_sector_payload,
    build_trust_payload,
)

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# Webhook 訊息的顯示名稱與頭像（可在 .env 中自訂）
WEBHOOK_USERNAME   = os.getenv("WEBHOOK_USERNAME",   "台股法人雷達 📡")
WEBHOOK_AVATAR_URL = os.getenv("WEBHOOK_AVATAR_URL", "").strip()


def _wrap_identity(payload: dict[str, Any]) -> dict[str, Any]:
    """將自訂名稱與頭像注入 payload。"""
    payload["username"] = WEBHOOK_USERNAME
    if WEBHOOK_AVATAR_URL:
        payload["avatar_url"] = WEBHOOK_AVATAR_URL
    return payload


def post_to_webhook(webhook_url: str, payload: dict[str, Any]) -> None:
    """將 payload 以 POST 送至 Discord Webhook。"""
    data = json.dumps(_wrap_identity(payload)).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "DiscordBot (https://github.com/acestock/everyday-message-from-stock, 1.0)",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            status = resp.status
            if status not in (200, 204):
                raise RuntimeError(f"Webhook 回應非預期狀態碼: {status}")
        logger.info("Webhook 發送成功（HTTP %s）", status)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        logger.error("Discord 回應 HTTP %s: %s", e.code, body)
        raise


def main() -> None:
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook_url:
        logger.error("未設定 DISCORD_WEBHOOK_URL，請在環境變數或 .env 中設定")
        sys.exit(1)

    report_type = os.getenv("REPORT_TYPE", "all").strip().lower()
    errors: list[str] = []

    tasks = []
    if report_type in ("all", "foreign"):
        tasks.append(("外資", build_foreign_payload))
    if report_type in ("all", "trust"):
        tasks.append(("投信", build_trust_payload))
    if report_type in ("all", "active"):
        tasks.append(("法人資金族群彙計", build_sector_payload))

    for label, build_fn in tasks:
        logger.info("抓取%s資料...", label)
        try:
            payload = build_fn()
            post_to_webhook(webhook_url, payload)
        except urllib.error.HTTPError as e:
            msg = f"{label}報告 Webhook 發送失敗（HTTP {e.code}）: {e.reason}"
            logger.error(msg)
            errors.append(msg)
        except Exception as e:
            msg = f"{label}報告失敗: {e}"
            logger.error(msg)
            errors.append(msg)

    if errors:
        logger.error("完成，但有 %d 個錯誤", len(errors))
        sys.exit(1)
    else:
        logger.info("所有報告發送完成")


if __name__ == "__main__":
    main()
