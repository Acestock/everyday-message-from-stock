# everyday-message-from-stock — 開發指引

台股每日法人資金流向報告，透過 Discord Webhook 發送。

---

## 架構

```
foreign_scraper.py     原始資料抓取與快取（TWSE / TPEx）
message_formatter.py   將資料轉成 Discord Webhook embed payload
send_report.py         主程式：呼叫 formatter → POST 到 Webhook
.github/workflows/     GitHub Actions 排程（週一至五 16:30 台灣時間）
```

---

## Discord Webhook 硬性限制（違反 → HTTP 400）

| 項目 | 上限 |
|------|------|
| field value 字元數 | **1024** |
| embed 總字元數 | **6000** |
| 單則訊息的 embed 數 | **10** |
| embed title 字元數 | 256 |

**新增任何 field 時，必須估算字元數：**
- 排行榜 10 筆 × ~45 字/行 ≈ 450 字 ✓
- 法人積極族群每 10 筆 × ~60 字/行 ≈ 600 字 ✓，超過 10 筆就拆成多個 fields

---

## HTTP 403 Cloudflare 封鎖

`urllib` 預設 `User-Agent: Python-urllib/3.x` 會被 Cloudflare 擋（error code 1010）。

`send_report.py` 的 `post_to_webhook()` 必須帶：
```python
"User-Agent": "DiscordBot (https://github.com/acestock/everyday-message-from-stock, 1.0)"
```

---

## 資料流

```
_fetch_all_raw()           ← 每日快取一次，三份報告共用
  ├── _fetch_twse_base("foreign")
  ├── _fetch_twse_base("trust")
  └── _fetch_tpex_3insti()

fetch_foreign_rank()       ← 上市+上櫃外資 Top10 買/賣超
fetch_trust_rank()         ← 上市+上櫃投信 Top10 買/賣超
fetch_institutional_active() ← 外資＋投信同日雙買超（取交集）
```

---

## 手機友善格式規則

- 不在行內混用 backtick code + bold，單純文字即可
- 每行控制在 **50 字以內**（Discord 手機版換行點）
- MA 標籤使用短格式：`↑20▲↑60▼`（上/下均線 × 均線走勢）

---

## 環境變數

| 變數 | 必填 | 說明 |
|------|------|------|
| `DISCORD_WEBHOOK_URL` | ✅ | Discord Webhook 完整 URL |
| `WEBHOOK_USERNAME` | ❌ | 發送者名稱（預設：台股法人雷達 📡）|
| `WEBHOOK_AVATAR_URL` | ❌ | 頭像圖片 URL（留空用 Webhook 預設）|
| `REPORT_TYPE` | ❌ | `all` / `foreign` / `trust` / `active`（預設 all）|

---

## GitHub Actions

- 排程：每週一至五 UTC 08:30（= 台灣 16:30）
- 手動觸發：Actions → Daily Stock Report → Run workflow
  - `workflow_dispatch` 只在**預設分支**顯示觸發按鈕
- Secrets 設定路徑：Settings → Secrets and variables → Actions → Repository secrets
