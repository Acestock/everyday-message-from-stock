# 台股法人雷達 📡

每個交易日 18:00 自動推播台股法人資金流向報告到 Discord，以 PNG 圖片呈現，手機友善。

---

## 報告內容

| 區塊 | 說明 |
|------|------|
| 📈 大盤總覽 | 加權指數漲跌、三大法人合計買賣超（億元）、外資台指期淨多單 |
| 📊 外資買賣超排行 | 上市＋上櫃各 Top 10 買超 / 賣超個股，含股價、MA20/60 位置 |
| 📊 投信買賣超排行 | 同上，投信視角 |
| 🏭 法人資金族群彙計 | 外資＋投信買賣超依主題族群加總，快速看資金流向哪個板塊 |

每筆排行股票附有**連續天數標記**（`2d` = 連續 2 天上榜）與**方向反轉標記**（`昨賣` = 昨天賣超今天買超）。

---

## 快速設定

### 1. Fork 本 Repo

### 2. 建立 Discord Webhook
Discord 頻道 → 編輯頻道 → 整合 → Webhook → 新增 Webhook → 複製 URL

### 3. 設定 GitHub Secret
Repo → Settings → Secrets and variables → Actions → New repository secret

| Name | Value |
|------|-------|
| `DISCORD_WEBHOOK_URL` | 貼入 Webhook URL |

### 4. 合併至 main 分支
GitHub Actions 的排程觸發（`schedule`）只在**預設分支**生效。

### 5. 手動測試
Actions → Daily Stock Report → Run workflow

---

## 本地執行

```bash
pip install -r requirements.txt
playwright install chromium --with-deps

# 設定環境變數
export DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/YOUR_ID/YOUR_TOKEN

python send_report.py
```

---

## 自動執行時間

每週一至五**台灣時間 18:00**（UTC 10:00），台股收盤後約 2.5 小時，資料已完整更新。

---

## 族群設定

編輯 `sector_themes.py` 可新增或修改主題族群：

```python
THEME_MAP: dict[str, str] = {
    "3037": "ABF載板",   # 欣興電子
    "3017": "AI散熱",    # 奇鋐科技
    # 新增格式："股票代號": "族群名稱",  # 公司名稱
}
```

---

## 資料來源

| 資料 | 來源 |
|------|------|
| 個股買賣超 | TWSE T86、TPEx 三大法人明細 |
| 三大法人總額 | TWSE BFI82U |
| 加權指數 | Yahoo Finance / TWSE MI_INDEX |
| 外資期貨 | TAIFEX Open API |
| 股價 / MA | Yahoo Finance（yfinance） |
