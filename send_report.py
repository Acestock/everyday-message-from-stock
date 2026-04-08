"""
每日股市資金流向報告：透過 Discord Webhook 發送外資 / 投信買賣超排行。

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

from message_formatter import build_foreign_payload, build_trust_payload

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def post_to_webhook(webhook_url: str, payload: dict[str, Any]) -> None:
    """將 payload 以 POST 送至 Discord Webhook。"""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        status = resp.status
        if status not in (200, 204):
            raise RuntimeError(f"Webhook 回應非預期狀態碼: {status}")
    logger.info("Webhook 發送成功（HTTP %s）", status)


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

    for label, build_fn in tasks:
        logger.info("抓取%s買賣超資料...", label)
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
