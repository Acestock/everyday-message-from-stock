# everyday-message-from-stock — 開發指引

台股每日法人資金流向報告，渲染成 PNG 圖片透過 Discord Webhook 自動推播。

---

## 檔案架構

```
send_report.py          主程式：協調各模組 → 渲染 PNG → POST Webhook
foreign_scraper.py      所有資料抓取、快取、排行、streak 計算
image_renderer.py       HTML 模板 → Playwright → PNG bytes
sector_themes.py        主題族群 mapping（優先於 TWSE 官方產業分類）
message_formatter.py    舊版 Discord embed 格式化（已棄用，保留供參考）
requirements.txt        pandas / yfinance / playwright / python-dotenv
.github/workflows/      GitHub Actions 排程
```

---

## 資料流

```
send_report.main()
  ├── fetch_market_overview()
  │     ├── _fetch_bfi82u()            上市三大法人買賣超億元（TWSE BFI82U）
  │     ├── _fetch_tpex_institutional() 上櫃三大法人億元（TPEX openapi，CI 常失敗）
  │     ├── _fetch_taiex_index()        加權指數（yfinance / TWSE MI_INDEX）
  │     └── _fetch_futures_position()   外資台指期淨多單（TAIFEX API + HTML）
  │
  ├── fetch_foreign_rank()
  │     ├── _fetch_all_raw()            ← 每日 in-memory 快取，三個公開函式共用
  │     │     ├── _fetch_twse_t86_both()  一次請求同時解析外資+投信（避免 CDN 不一致）
  │     │     └── _fetch_tpex_3insti()    上櫃個股外資+投信（多候選 URL）
  │     ├── _get_hist_snapshots(data_date)  前 5 個交易日快照（從資料日期往前算）
  │     └── _annotate_streaks()         寫入 streak / prev_dir 到每筆排行
  │
  ├── fetch_trust_rank()              （同 foreign_rank 流程，共用 _all_raw_cache）
  │
  ├── fetch_sector_institutional()
  │     ├── _fetch_all_raw()           （命中快取）
  │     └── _fetch_industry_map()      股票代號→官方產業 mapping（TWSE/TPEx OpenAPI）
  │
  ├── fetch_ma_data(all_stocks)        yfinance 批次抓 MA20 / MA60 + 成交量
  │
  └── render_report_png(data)          HTML → Playwright → PNG
        └── post_image_to_webhook()    multipart/form-data → Discord
```

---

## 關鍵設計決策與地雷

### TWSE T86 CDN 不一致
同一 URL 分兩次請求，可能被 CDN 路由到快取版本不同的節點，導致外資與投信
回傳不同日期的資料。**解法**：`_fetch_twse_t86_both()` 一次 HTTP 請求、同一
response 解析兩欄，保證日期一致。

### Streak 計算：以資料日期為基準點
`_get_hist_snapshots()` 必須傳入 `data_date`（資料日期），從該日**前一個**交易日
開始取快照。若以 `date.today()` 為基準，週末執行時第一筆快照 = 週五 = 資料當天，
造成全部顯示假的 `1d`。

### 歷史快照日期驗證
`_fetch_twse_t86_for_date()` 與 `_fetch_tpex_for_date()` 均需驗證回傳日期是否
與請求日期一致。若端點忽略 `date=` 參數而回傳最新資料，視為無資料（`[], []`），
寧可不顯示 streak 也不顯示錯誤數字。

### TPEx 封鎖問題
`_fetch_tpex_institutional()`（三大法人億元彙總）與 `_fetch_tpex_for_date()`
（歷史個股資料）在 GitHub Actions 環境可能被 Cloudflare 擋。
`_fetch_tpex_3insti()`（當日個股資料）目前靠三個候選 URL + `Accept-Language` 標頭
繞過，成功率較高。

### TWSE T86 日期格式
`rwd/zh/fund/T86` 的 `"date"` 欄位可能是：
- 8 碼 CE 格式：`"20260424"` → 直接切割
- 7 碼民國格式：`"1150424"` → `int("115") + 1911 = 2026`

兩種都需處理，否則日期顯示異常（原本只處理 8 碼，導致顯示 TPEx 預設舊日期）。

---

## Cloudflare / User-Agent

`urllib` 預設 `User-Agent: Python-urllib/3.x` 被 Cloudflare 擋（error 1010）。
- TWSE/通用：`_UA` = Chrome UA
- TPEx 專用：`_get_json_tpex()` 額外帶 `Accept-Language: zh-TW`
- Discord Webhook POST：`DiscordBot (https://github.com/..., 1.0)`

---

## Discord Webhook 硬性限制

| 項目 | 上限 |
|------|------|
| embed field value | **1024 字元** |
| embed 總字元 | **6000 字元** |
| 單則訊息 embed 數 | **10** |
| embed title | 256 字元 |

現在改為上傳 PNG 圖片（multipart/form-data），不受 embed 字元限制。

---

## 環境變數

| 變數 | 必填 | 說明 |
|------|------|------|
| `DISCORD_WEBHOOK_URL` | ✅ | Discord Webhook 完整 URL |
| `WEBHOOK_USERNAME` | ❌ | 發送者名稱（預設：台股法人雷達 📡）|
| `WEBHOOK_AVATAR_URL` | ❌ | 頭像圖片 URL |

---

## GitHub Actions

- 排程：每週一至五 **UTC 10:00**（= 台灣 18:00）
- `workflow_dispatch` 只在**預設分支 (main)** 顯示手動觸發按鈕
- Secrets：Settings → Secrets and variables → Actions → `DISCORD_WEBHOOK_URL`
- 超時設定：20 分鐘（含 Playwright 安裝 + 5 天歷史快照抓取）

---

## sector_themes.py 維護

`THEME_MAP` 優先於 TWSE 官方產業分類，格式：
```python
"股票代號": "族群名稱",  # 公司名稱
```

每支股票只屬於一個族群（取最具代表性的主題），寧缺毋濫。

---

## 已知限制

| 問題 | 狀態 |
|------|------|
| TPEx 三大法人億元彙總在 CI 失敗 | ⚠️ 僅顯示上市數字 |
| 外資台指期貨在 CI 失敗 | ⚠️ 顯示「—」 |
| 產業 mapping 在 CI 偶爾抓到 0 筆 | ⚠️ 全部歸「其他」族群 |
| streak 歷史快照每次重新抓取（~25 秒） | ⚠️ 無跨執行快取 |
