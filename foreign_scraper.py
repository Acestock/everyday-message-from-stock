"""
外資 / 投信買賣超：每日從 TWSE（上市）與 TPEx（上櫃）抓取前 10 大買超 / 賣超個股。
另提供「法人積極資金族群」：外資＋投信同日雙向買超的股票。

資料來源（官方 JSON API，無需 token）：
  TWSE  https://www.twse.com.tw/rwd/zh/fund/T86
        三大法人買賣超日報（外資 / 投信 / 自營商欄位同一張表）。
  TPEx  https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge_result.php
        上櫃三大法人買賣明細（同一 API 同時含外資與投信欄位）。
        需帶民國年日期參數 d=115/03/23 與 Referer/XHR header。

使用 stdlib urllib（不依賴 requests 套件），結果按交易日分別快取。
"""
import json
import logging
import threading
import urllib.request
from datetime import date, timedelta
from typing import Optional

import pandas as pd
import yfinance as yf

from sector_themes import THEME_MAP

logger = logging.getLogger(__name__)

_lock = threading.Lock()

# 共用原始資料快取（TWSE + TPEx 一次抓完，三個公開函式共用）
_all_raw_cache: Optional[dict] = None
_all_raw_cache_date: Optional[str] = None

# 歷史快照快取（date_key → snapshot dict）
_hist_cache: dict[str, Optional[dict]] = {}

# 產業類別 mapping 快取
_industry_map: dict[str, str] = {}
_industry_map_date: Optional[str] = None

TOP_N           = 10
TOP_SECTOR      = 10   # 族群買超/賣超顯示前 N 個產業
TOP_ACTIVE      = 20   # 外資投信雙買超個股最多顯示筆數
MAX_STREAK_DAYS = 5    # 連續天數最多追溯幾個前交易日
TIMEOUT = 12

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

# TWSE T86 欄位名稱對照
_TWSE_COL = {
    "foreign": "外陸資買賣超股數",
    "trust":   "投信買賣超股數",
}
# T86 欄位 fallback 索引（若 fields 解析失敗）
_TWSE_FALLBACK_IDX = {"foreign": 4, "trust": 7}


# ── 工具函式 ───────────────────────────────────────────────────────────────────

def _get_json(url: str, referer: str = "") -> dict:
    headers = {
        "User-Agent": _UA,
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
    }
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _clean_num(s) -> float:
    """'1,234,567' / '(1,234)' / -1234 → float"""
    s = str(s).strip().replace(",", "")
    if s.startswith("(") and s.endswith(")"):
        return -float(s[1:-1])
    try:
        return float(s)
    except ValueError:
        return 0.0


def _roc_to_ce(roc_date: str) -> str:
    """'115/03/23' → '2026/03/23'"""
    parts = roc_date.split("/")
    if len(parts) == 3:
        try:
            return f"{int(parts[0]) + 1911}/{parts[1]}/{parts[2]}"
        except ValueError:
            pass
    return roc_date


def _today_roc() -> str:
    """今日日期轉民國年字串，例如 '115/03/23'"""
    d = date.today()
    return f"{d.year - 1911}/{d.month:02d}/{d.day:02d}"


# ── TWSE 共用底層 ──────────────────────────────────────────────────────────────

def _fetch_twse_base(kind: str) -> tuple[list[dict], str]:
    """
    從 TWSE T86 抓取指定法人的買賣超。
    kind: "foreign" → 外陸資買賣超  |  "trust" → 投信買賣超
    回傳 ([{code,name,net_k,market}], date_str)。
    """
    col_name    = _TWSE_COL[kind]
    fallback_idx = _TWSE_FALLBACK_IDX[kind]

    url = (
        "https://www.twse.com.tw/rwd/zh/fund/T86"
        "?response=json&date=&selectType=ALLBUT0999"
    )
    try:
        j    = _get_json(url)
        stat = j.get("stat", "")
        if stat != "OK":
            logger.info("[%s] TWSE stat=%s（可能非交易日）", kind, stat)
            return [], ""

        raw_date = j.get("date", "")
        if len(raw_date) == 8:
            # CE 西元格式：YYYYMMDD
            date_str = f"{raw_date[:4]}/{raw_date[4:6]}/{raw_date[6:]}"
        elif len(raw_date) == 7:
            # 民國格式：YYYMMDD（3 碼民國年）
            ce_year  = int(raw_date[:3]) + 1911
            date_str = f"{ce_year}/{raw_date[3:5]}/{raw_date[5:]}"
        else:
            date_str = ""
        logger.info("[%s] TWSE raw_date=%r → date_str=%s", kind, raw_date, date_str)

        fields = j.get("fields", [])
        data   = j.get("data",   [])

        try:
            net_idx = fields.index(col_name)
        except ValueError:
            net_idx = fallback_idx

        result = []
        for row in data:
            try:
                code     = str(row[0]).strip()
                name     = str(row[1]).strip()
                net_share = _clean_num(row[net_idx])
                net_k    = round(net_share / 1000)    # 股 → 張
                if net_k == 0:
                    continue
                result.append({"code": code, "name": name,
                                "net_k": net_k, "market": "上市"})
            except (IndexError, ValueError, TypeError):
                continue

        logger.info("[%s] TWSE 上市：%d 筆，日期 %s", kind, len(result), date_str)
        return result, date_str

    except Exception as e:
        logger.warning("[%s] TWSE 抓取失敗: %s", kind, e)
        return [], ""


# ── TPEx 三大法人（外資 + 投信 共用）─────────────────────────────────────────

def _get_json_tpex(url: str, referer: str = "") -> dict:
    """TPEx 專用 HTTP GET，額外帶 Accept-Language 與 Accept 標頭以繞過部分防護。"""
    headers = {
        "User-Agent":      _UA,
        "Accept":          "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        "X-Requested-With": "XMLHttpRequest",
    }
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _fetch_tpex_3insti() -> tuple[list[dict], list[dict], str]:
    """
    上櫃三大法人買賣明細（一支 API 同時含外資與投信）。
    回傳 (foreign_stocks, trust_stocks, date_str)。
    """
    roc_date = _today_roc()
    referer  = "https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge.php?l=zh-tw"
    candidate_urls = [
        (
            "https://www.tpex.org.tw/web/stock/3insti/daily_trade/"
            f"3itrade_hedge_result.php?l=zh-tw&t=D&d={roc_date}&se=EW&o=json"
        ),
        (
            "https://www.tpex.org.tw/web/stock/3insti/daily_trade/"
            f"3itrade_hedge_result.php?l=zh-tw&t=D&d={roc_date}&se=AL&o=json"
        ),
        (
            "https://www.tpex.org.tw/web/stock/3insti/daily_trade/"
            "3itrade_hedge_result.php?l=zh-tw&t=D&se=EW&o=json"
        ),
    ]

    j: dict = {}
    for url in candidate_urls:
        try:
            j    = _get_json_tpex(url, referer=referer)
            rows = (j.get("tables") or [{}])[0].get("data", [])
            logger.info("[tpex_3insti] %s → %d 筆", url.split("?")[1][:30], len(rows))
            if rows:
                break
        except Exception as e:
            logger.warning("[tpex_3insti] %s → 失敗: %s", url.split("?")[1][:30], e)

    try:
        table    = (j.get("tables") or [{}])[0]
        raw_date = table.get("date", "") or j.get("date", "")
        date_str = _roc_to_ce(raw_date) if raw_date else ""
        rows     = table.get("data", [])

        FOREIGN_COL = 10
        TRUST_COL   = 13

        foreign_list: list[dict] = []
        trust_list:   list[dict] = []
        for row in rows:
            try:
                code = str(row[0]).strip()
                name = str(row[1]).strip()
                fnet = round(_clean_num(row[FOREIGN_COL]) / 1000)
                tnet = round(_clean_num(row[TRUST_COL])   / 1000)
                if fnet != 0:
                    foreign_list.append({"code": code, "name": name,
                                         "net_k": fnet, "market": "上櫃"})
                if tnet != 0:
                    trust_list.append({"code": code, "name": name,
                                       "net_k": tnet, "market": "上櫃"})
            except (IndexError, ValueError, TypeError):
                continue

        logger.info("[tpex_3insti] 上櫃 foreign=%d trust=%d 日期 %s",
                    len(foreign_list), len(trust_list), date_str)
        return foreign_list, trust_list, date_str

    except Exception as e:
        logger.warning("[tpex_3insti] 解析失敗: %s", e)
        return [], [], ""


# ── 共用原始資料（一次抓完，三個公開函式共用）────────────────────────────────

def _fetch_all_raw() -> dict:
    """
    抓取並快取當日所有法人原始資料。
    回傳：
    {
      "date": str,
      "twse_foreign": [...],  # 上市外資（全部）
      "twse_trust":   [...],  # 上市投信（全部）
      "tpex_foreign": [...],  # 上櫃外資（全部）
      "tpex_trust":   [...],  # 上櫃投信（全部）
    }
    """
    global _all_raw_cache, _all_raw_cache_date

    today = date.today().strftime("%Y-%m-%d")
    with _lock:
        if _all_raw_cache_date == today and _all_raw_cache:
            return _all_raw_cache

    twse_f, twse_date       = _fetch_twse_base("foreign")
    twse_t, _               = _fetch_twse_base("trust")
    tpex_f, tpex_t, tpex_d = _fetch_tpex_3insti()
    # 只在 TPEx 有實際資料時才採用其日期，避免空回應裡的預設舊日期污染顯示
    tpex_date_valid = tpex_d if (tpex_f or tpex_t) else ""
    data_date = twse_date or tpex_date_valid or today.replace("-", "/")

    result = {
        "date":         data_date,
        "twse_foreign": twse_f,
        "twse_trust":   twse_t,
        "tpex_foreign": tpex_f,
        "tpex_trust":   tpex_t,
    }
    with _lock:
        _all_raw_cache      = result
        _all_raw_cache_date = today
    return result


# ── MA 技術資料 ───────────────────────────────────────────────────────────────

def _download_ohlcv(tickers: list[str]) -> tuple[dict, dict]:
    """批次下載近 6 個月收盤 + 成交量；回傳 (closes_dict, volumes_dict)。
    使用 6mo 而非 3mo，確保春節等長假後仍有 ≥60 個交易日供 MA60 計算。"""
    if not tickers:
        return {}, {}
    try:
        df = yf.download(tickers, period="6mo", progress=False, auto_adjust=False)
        if df is None or df.empty:
            return {}, {}
        if isinstance(df.columns, pd.MultiIndex):
            closes  = df["Close"]
            volumes = df["Volume"]
        else:
            closes  = df[["Close"]].rename(columns={"Close": tickers[0]})
            volumes = df[["Volume"]].rename(columns={"Volume": tickers[0]})
        out_c, out_v = {}, {}
        for t in tickers:
            if t in closes.columns:
                s = closes[t].dropna()
                if len(s) >= 20:
                    out_c[t] = s
            if t in volumes.columns:
                v = volumes[t].dropna()
                if not v.empty:
                    out_v[t] = v
        return out_c, out_v
    except Exception as e:
        logger.debug("[ma] _download_ohlcv failed: %s", e)
        return {}, {}


def fetch_ma_data(
    codes: "list[str] | list[dict]",
) -> dict[str, dict]:
    """
    批次抓取股價 + MA20 + MA60 及其走勢方向。
    codes 可傳：
      - list[str]：全部先試 .TW，抓不到再試 .TWO
      - list[dict]：每筆含 code/market，依 market 只查對應 suffix
    回傳：
      {code: {price, ma20, ma20_up, above_ma20, ma60, ma60_up, above_ma60}}
    """
    result: dict[str, dict] = {}
    if not codes:
        return result

    if isinstance(codes[0], dict):
        tw_codes  = [s["code"] for s in codes if s.get("market") != "上櫃"]
        two_codes = [s["code"] for s in codes if s.get("market") == "上櫃"]
        unknown   = []
    else:
        tw_codes = two_codes = []
        unknown  = list(codes)   # type: ignore[arg-type]

    tw_tickers  = [c + ".TW"  for c in (tw_codes  + unknown)]
    two_tickers = [c + ".TWO" for c in (two_codes + unknown)]
    tw_data,  tw_vols  = _download_ohlcv(tw_tickers)  if tw_tickers  else ({}, {})
    two_data, two_vols = _download_ohlcv(two_tickers) if two_tickers else ({}, {})

    all_codes = tw_codes + two_codes + unknown
    for code in all_codes:
        tw_t, two_t = code + ".TW", code + ".TWO"
        series = tw_data.get(tw_t)
        if series is None:
            series = two_data.get(two_t)
        if series is None or len(series) < 20:
            continue

        vol_s = tw_vols.get(tw_t)
        if vol_s is None:
            vol_s = two_vols.get(two_t)
        vol_k = round(float(vol_s.iloc[-1]) / 1000) if vol_s is not None else None

        price  = float(series.iloc[-1])
        ma20_s = series.rolling(20).mean().dropna()
        ma20   = float(ma20_s.iloc[-1])
        ma20_prev  = float(ma20_s.iloc[max(-6, -len(ma20_s))])

        if len(series) >= 60:
            ma60_s     = series.rolling(60).mean().dropna()
            ma60       = float(ma60_s.iloc[-1])
            ma60_prev  = float(ma60_s.iloc[max(-6, -len(ma60_s))])
            ma60_up    = ma60 >= ma60_prev
            above_ma60 = price >= ma60
        else:
            ma60 = ma60_up = above_ma60 = None

        result[code] = {
            "price":      round(price, 1),
            "ma20":       round(ma20,  1),
            "ma20_up":    ma20 >= ma20_prev,
            "above_ma20": price >= ma20,
            "ma60":       round(ma60, 1) if ma60 is not None else None,
            "ma60_up":    ma60_up,
            "above_ma60": above_ma60,
            "volume_k":   vol_k,
        }

    logger.debug("[ma] fetch_ma_data: %d / %d 支成功", len(result), len(codes))
    return result


# ── ETF 判斷 ─────────────────────────────────────────────────────────────────

def _is_etf(code: str) -> bool:
    """台股 ETF 代號均以 '0' 開頭；普通股從 1xxx 起，不會以 0 開頭。"""
    return code.startswith("0")


# ── 共用排名邏輯 ──────────────────────────────────────────────────────────────

def _build_rank(all_stocks: list[dict], data_date: str) -> dict:
    def _split(stocks: list[dict]) -> dict:
        # 過濾 ETF
        stocks = [s for s in stocks if not _is_etf(s["code"])]
        buy  = sorted(
            [s for s in stocks if s["net_k"] > 0],
            key=lambda x: x["net_k"], reverse=True,
        )[:TOP_N]
        sell = sorted(
            [s for s in stocks if s["net_k"] < 0],
            key=lambda x: x["net_k"],
        )[:TOP_N]
        return {"buy": buy, "sell": sell}

    twse = [s for s in all_stocks if s["market"] == "上市"]
    tpex = [s for s in all_stocks if s["market"] == "上櫃"]
    return {"date": data_date, "twse": _split(twse), "tpex": _split(tpex)}


# ── 歷史資料（連續天數標記）────────────────────────────────────────────────────

def _prev_weekdays(n: int) -> list[date]:
    """回傳今日之前 n 個工作日（週一〜週五）的日期，最近的在前。"""
    result: list[date] = []
    d = date.today()
    while len(result) < n:
        d -= timedelta(days=1)
        if d.weekday() < 5:   # 0=Mon … 4=Fri
            result.append(d)
    return result


def _fetch_twse_t86_for_date(dt: date) -> tuple[list[dict], list[dict]]:
    """
    抓取指定日期的 TWSE T86（外資 + 投信同一支 API）。
    使用舊端點（非 rwd/zh），確保 date= 參數有效支援歷史查詢。
    舊端點欄位索引：foreign=4, trust=7（與 _TWSE_FALLBACK_IDX 相同）。
    回傳日期與請求日期不符時視為無資料，避免端點忽略 date= 造成假連續。
    """
    url = (
        "https://www.twse.com.tw/fund/T86"
        f"?response=json&date={dt.strftime('%Y%m%d')}&selectType=ALLBUT0999"
    )
    try:
        j = _get_json(url)
        if j.get("stat") != "OK":
            return [], []

        # 驗證回傳日期與請求日期一致，防止端點忽略 date= 而回傳最新資料
        raw_date = j.get("date", "")
        if len(raw_date) == 8:
            returned = date(int(raw_date[:4]), int(raw_date[4:6]), int(raw_date[6:]))
            if returned != dt:
                logger.debug("[hist_twse] 請求 %s 但端點回傳 %s，忽略（date= 無效）", dt, raw_date)
                return [], []

        fields = j.get("fields", [])
        data   = j.get("data",   [])
        try:
            f_idx = fields.index(_TWSE_COL["foreign"])
        except ValueError:
            f_idx = _TWSE_FALLBACK_IDX["foreign"]
        try:
            t_idx = fields.index(_TWSE_COL["trust"])
        except ValueError:
            t_idx = _TWSE_FALLBACK_IDX["trust"]

        foreign_list: list[dict] = []
        trust_list:   list[dict] = []
        for row in data:
            try:
                code = str(row[0]).strip()
                name = str(row[1]).strip()
                fnet = round(_clean_num(row[f_idx]) / 1000)
                tnet = round(_clean_num(row[t_idx]) / 1000)
                if fnet != 0:
                    foreign_list.append({"code": code, "name": name,
                                         "net_k": fnet, "market": "上市"})
                if tnet != 0:
                    trust_list.append({"code": code, "name": name,
                                       "net_k": tnet, "market": "上市"})
            except (IndexError, ValueError, TypeError):
                continue
        logger.debug("[hist_twse] %s  外資=%d 投信=%d", dt, len(foreign_list), len(trust_list))
        return foreign_list, trust_list
    except Exception as e:
        logger.debug("[hist_twse] %s 失敗: %s", dt, e)
        return [], []


def _fetch_tpex_for_date(dt: date) -> tuple[list[dict], list[dict]]:
    """
    抓取指定日期的 TPEx 三大法人（外資 + 投信）。
    回傳日期與請求日期不符時視為無資料，避免假連續。
    """
    roc = f"{dt.year - 1911}/{dt.month:02d}/{dt.day:02d}"
    referer = "https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge.php?l=zh-tw"
    candidate_urls = [
        (
            "https://www.tpex.org.tw/web/stock/3insti/daily_trade/"
            f"3itrade_hedge_result.php?l=zh-tw&t=D&d={roc}&se=EW&o=json"
        ),
        (
            "https://www.tpex.org.tw/web/stock/3insti/daily_trade/"
            f"3itrade_hedge_result.php?l=zh-tw&t=D&d={roc}&se=AL&o=json"
        ),
    ]
    for url in candidate_urls:
        try:
            j     = _get_json_tpex(url, referer=referer)
            table = (j.get("tables") or [{}])[0]
            rows  = table.get("data", [])
            if not rows:
                continue

            # 驗證回傳日期與請求日期一致
            raw_date = table.get("date", "") or j.get("date", "")
            if raw_date and raw_date != roc:
                logger.debug("[hist_tpex] 請求 %s 但端點回傳 %s，忽略（date= 無效）", roc, raw_date)
                return [], []

            foreign_list: list[dict] = []
            trust_list:   list[dict] = []
            for row in rows:
                try:
                    code = str(row[0]).strip()
                    name = str(row[1]).strip()
                    fnet = round(_clean_num(row[10]) / 1000)
                    tnet = round(_clean_num(row[13]) / 1000)
                    if fnet != 0:
                        foreign_list.append({"code": code, "name": name,
                                             "net_k": fnet, "market": "上櫃"})
                    if tnet != 0:
                        trust_list.append({"code": code, "name": name,
                                           "net_k": tnet, "market": "上櫃"})
                except (IndexError, ValueError, TypeError):
                    continue
            logger.debug("[hist_tpex] %s  外資=%d 投信=%d", dt, len(foreign_list), len(trust_list))
            return foreign_list, trust_list
        except Exception as e:
            logger.debug("[hist_tpex] %s 失敗: %s", dt, e)
    return [], []


def _build_snapshot(dt: date) -> Optional[dict]:
    """
    建立指定日期的排行快照（不含 ETF 的 Top N buy/sell code 集合）。
    非交易日回傳 None。
    """
    def _top_sets(stocks: list[dict]) -> tuple[set, set]:
        stocks = [s for s in stocks if not _is_etf(s["code"])]
        buy_codes = {s["code"] for s in
                     sorted([s for s in stocks if s["net_k"] > 0],
                            key=lambda x: x["net_k"], reverse=True)[:TOP_N]}
        sell_codes = {s["code"] for s in
                      sorted([s for s in stocks if s["net_k"] < 0],
                             key=lambda x: x["net_k"])[:TOP_N]}
        return buy_codes, sell_codes

    twse_f, twse_t = _fetch_twse_t86_for_date(dt)
    tpex_f, tpex_t = _fetch_tpex_for_date(dt)

    if not (twse_f or twse_t or tpex_f or tpex_t):
        logger.info("[snapshot] %s 無資料（非交易日）", dt)
        return None

    fb, fs = _top_sets(twse_f + tpex_f)
    tb, ts = _top_sets(twse_t + tpex_t)
    logger.info("[snapshot] %s  外資buy=%d sell=%d  投信buy=%d sell=%d",
                dt, len(fb), len(fs), len(tb), len(ts))
    return {
        "foreign_buy":  fb,
        "foreign_sell": fs,
        "trust_buy":    tb,
        "trust_sell":   ts,
    }


def _get_hist_snapshots() -> list[Optional[dict]]:
    """回傳前 MAX_STREAK_DAYS 個交易日的快照列表（今日→過去排列）。"""
    prev_days = _prev_weekdays(MAX_STREAK_DAYS)
    result: list[Optional[dict]] = []
    for d in prev_days:
        key = d.strftime("%Y-%m-%d")
        with _lock:
            if key in _hist_cache:
                result.append(_hist_cache[key])
                continue
        snap = _build_snapshot(d)
        with _lock:
            _hist_cache[key] = snap
        result.append(snap)
    return result


def _annotate_streaks(rank: dict, kind: str,
                      snapshots: list[Optional[dict]]) -> None:
    """
    為 rank 中每筆股票加上 streak（連續天數）和 prev_dir（昨買/昨賣）。
    kind: "foreign" | "trust"
    """
    buy_key  = f"{kind}_buy"
    sell_key = f"{kind}_sell"

    for market in ("twse", "tpex"):
        for side in ("buy", "sell"):
            same_key = buy_key  if side == "buy" else sell_key
            opp_key  = sell_key if side == "buy" else buy_key
            for stock in rank[market][side]:
                code = stock["code"]
                streak   = 0
                prev_dir = None
                for snap in snapshots:
                    if snap is None:
                        break
                    if code in snap.get(same_key, set()):
                        streak += 1
                    elif code in snap.get(opp_key, set()):
                        if streak == 0:
                            prev_dir = "昨賣" if side == "buy" else "昨買"
                        break
                    else:
                        break
                stock["streak"]   = streak
                stock["prev_dir"] = prev_dir


# ── 公開 API ───────────────────────────────────────────────────────────────────

def _fetch_industry_map() -> dict[str, str]:
    """
    從 TWSE / TPEx Open API 抓取 股票代號 → 產業類別 mapping（每日快取）。
    抓取失敗時回傳空 dict，呼叫端應以「其他」作 fallback。
    """
    global _industry_map, _industry_map_date

    today = date.today().strftime("%Y-%m-%d")
    with _lock:
        if _industry_map_date == today and _industry_map:
            return _industry_map

    mapping: dict[str, str] = {}

    # TWSE 上市 Open API（t187ap03_L）
    # 欄位：公司代號, 公司名稱, 產業類別, ...
    for url in [
        "https://openapi.twse.com.tw/v1/opendata/t187ap03_L",
    ]:
        try:
            j = _get_json(url)
            if isinstance(j, list):
                for item in j:
                    code     = str(item.get("公司代號", "")).strip()
                    industry = str(item.get("產業類別", "")).strip()
                    if code and industry:
                        mapping[code] = industry
            if mapping:
                break
        except Exception as e:
            logger.warning("[industry_map] TWSE open API 失敗: %s", e)

    # TPEx 上櫃 Open API（t187ap04_L）
    for url in [
        "https://openapi.twse.com.tw/v1/opendata/t187ap04_L",
    ]:
        try:
            j = _get_json(url)
            if isinstance(j, list):
                for item in j:
                    code     = str(item.get("公司代號", "")).strip()
                    industry = str(item.get("產業類別", "")).strip()
                    if code and industry:
                        mapping[code] = industry
        except Exception as e:
            logger.warning("[industry_map] TPEx open API 失敗: %s", e)

    with _lock:
        _industry_map      = mapping
        _industry_map_date = today

    logger.info("[industry_map] 共 %d 支股票產業資料", len(mapping))
    return mapping


def fetch_sector_institutional() -> dict:
    """
    法人資金族群彙計：將外資＋投信買賣超依產業類別加總，
    回傳買超 / 賣超前 TOP_SECTOR 個產業。
    回傳格式：
    {
      "date": str,
      "buy":  [{sector, foreign_net, trust_net, total_net, top_stock}, ...],
      "sell": [{sector, foreign_net, trust_net, total_net, top_stock}, ...],
    }
    top_stock: {"code": str, "name": str, "net_k": int}
    """
    raw          = _fetch_all_raw()
    industry_map = _fetch_industry_map()

    # sectors[sec] = {foreign_net, trust_net, stock_nets:{code:{name,total}}}
    sectors: dict[str, dict] = {}

    def _agg(stocks: list[dict], key: str) -> None:
        for s in stocks:
            sec = THEME_MAP.get(s["code"]) or industry_map.get(s["code"], "其他")
            if sec not in sectors:
                sectors[sec] = {"foreign_net": 0, "trust_net": 0, "stock_nets": {}}
            sectors[sec][key] += s["net_k"]
            code = s["code"]
            if code not in sectors[sec]["stock_nets"]:
                sectors[sec]["stock_nets"][code] = {"name": s["name"], "total": 0}
            sectors[sec]["stock_nets"][code]["total"] += s["net_k"]

    _agg(raw["twse_foreign"] + raw["tpex_foreign"], "foreign_net")
    _agg(raw["twse_trust"]   + raw["tpex_trust"],   "trust_net")

    rows = []
    for sec, v in sectors.items():
        total       = v["foreign_net"] + v["trust_net"]
        stock_nets  = v["stock_nets"]
        if stock_nets:
            # 取與族群同方向最大貢獻的個股
            pick = max if total >= 0 else min
            code, info = pick(stock_nets.items(), key=lambda x: x[1]["total"])
            top_stock = {"code": code, "name": info["name"], "net_k": info["total"]}
        else:
            top_stock = None
        rows.append({
            "sector":      sec,
            "foreign_net": v["foreign_net"],
            "trust_net":   v["trust_net"],
            "total_net":   total,
            "top_stock":   top_stock,
        })

    buy  = sorted([r for r in rows if r["total_net"] > 0],
                  key=lambda x: x["total_net"], reverse=True)[:TOP_SECTOR]
    sell = sorted([r for r in rows if r["total_net"] < 0],
                  key=lambda x: x["total_net"])[:TOP_SECTOR]

    logger.info("[sector] 買超族群 %d 個，賣超族群 %d 個", len(buy), len(sell))
    return {"date": raw["date"], "buy": buy, "sell": sell}


# ── 大盤總覽 ──────────────────────────────────────────────────────────────────

def _fetch_taiex_index() -> dict | None:
    """加權指數收盤、漲跌、漲跌幅。"""
    # Method 1: yfinance Ticker.history()（單支更穩定，避免 MultiIndex 問題）
    try:
        hist = yf.Ticker("^TWII").history(period="5d")
        if hist is not None and not hist.empty:
            c = hist["Close"].dropna()
            if len(c) >= 2:
                close, prev = float(c.iloc[-1]), float(c.iloc[-2])
                chg = close - prev
                return {"close": round(close, 2), "change": round(chg, 2),
                        "change_pct": round(chg / prev * 100, 2)}
    except Exception as e:
        logger.debug("[taiex] yfinance Ticker 失敗: %s", e)

    # Method 2: yfinance download（兼容新版 MultiIndex columns）
    try:
        df = yf.download("^TWII", period="5d", progress=False, auto_adjust=False)
        if df is not None and not df.empty:
            close_col = (df["Close"]["^TWII"] if isinstance(df.columns, pd.MultiIndex)
                         else df["Close"])
            c = close_col.dropna()
            if len(c) >= 2:
                close, prev = float(c.iloc[-1]), float(c.iloc[-2])
                chg = close - prev
                return {"close": round(close, 2), "change": round(chg, 2),
                        "change_pct": round(chg / prev * 100, 2)}
    except Exception as e:
        logger.debug("[taiex] yfinance download 失敗: %s", e)

    # Method 3: TWSE MI_INDEX API（遍歷所有 data 欄位，不依賴固定 key）
    try:
        j = _get_json(
            "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"
            f"?response=json&date={date.today().strftime('%Y%m%d')}&type=IND"
        )
        if j.get("stat") == "OK":
            for v in j.values():
                if not isinstance(v, list):
                    continue
                for row in v:
                    if not isinstance(row, list) or not row:
                        continue
                    if "加權" not in str(row[0]):
                        continue
                    sign = -1 if any("▼" in str(c) for c in row) else 1
                    try:
                        close = float(str(row[1]).replace(",", ""))
                        chg   = float(str(row[2]).replace(",", "").replace("+", "")) * sign
                        pct   = float(str(row[3]).replace(",", "").replace("+", "")) * sign
                        return {"close": round(close, 2), "change": round(chg, 2),
                                "change_pct": round(pct, 2)}
                    except Exception:
                        continue
    except Exception as e:
        logger.warning("[taiex] MI_INDEX 失敗: %s", e)

    return None


def _fetch_bfi82u() -> dict | None:
    """
    上市三大法人買賣超金額（億元）。
    來源：TWSE BFI82U。金額單位：元，/ 1e8 → 億。

    優先取合計行（不含括號的行），若無則累計子行加總：
      外資及陸資(不含自營商) + 外資及陸資自營商 = 外資及陸資合計
      自營商(自行買賣) + 自營商(避險) = 自營商合計
    """
    try:
        j = _get_json(
            "https://www.twse.com.tw/rwd/zh/fund/BFI82U"
            "?response=json&dayDate=&type=day"
        )
        if j.get("stat") != "OK":
            return None

        fields = j.get("fields", [])
        net_idx = 3
        for i, f in enumerate(fields):
            if "買賣超" in str(f):
                net_idx = i
                break
        logger.info("[bfi82u] fields=%s  net_idx=%d", fields, net_idx)

        result: dict[str, float] = {}
        foreign_raw_sum: float = 0.0   # 子行備用
        dealer_raw_sum:  float = 0.0   # 子行備用

        for row in j.get("data", []):
            name = str(row[0]).strip()
            try:
                raw = float(str(row[net_idx]).replace(",", ""))
            except (ValueError, IndexError):
                continue

            # 把所有自營商相關列完整印出，方便確認欄位對應
            if "自營" in name:
                logger.info("[bfi82u] 自營行: name=%r  全列=%s", name, row)

            if "外資及陸資" in name:
                if "不含" not in name and "自營" not in name:
                    result["foreign"] = round(raw / 1e8, 1)   # 合計行（優先）
                else:
                    foreign_raw_sum += raw                      # 子行備用累計
            elif name == "外資自營商":
                # 外資的自營桌，屬於外資合計的一部分，不是自營商機構
                foreign_raw_sum += raw
            elif name == "投信":
                result["trust"] = round(raw / 1e8, 1)
            elif "自營商" in name and "外資" not in name:
                # 排除 "外資自營商"，只處理真正的自營商機構行
                if "(" not in name and "（" not in name:
                    result["dealer"] = round(raw / 1e8, 1)     # 合計行（優先）
                else:
                    dealer_raw_sum += raw                       # 子行備用累計

        # 若無合計行，改用子行加總
        if "foreign" not in result and foreign_raw_sum != 0.0:
            result["foreign"] = round(foreign_raw_sum / 1e8, 1)
            logger.info("[bfi82u] 外資使用子行加總: %.1f億", result["foreign"])
        if "dealer" not in result and dealer_raw_sum != 0.0:
            result["dealer"] = round(dealer_raw_sum / 1e8, 1)
            logger.info("[bfi82u] 自營使用子行加總: %.1f億", result["dealer"])

        if result:
            result["total"] = round(sum(result.values()), 1)
            logger.info("[bfi82u] 外資=%.1f  投信=%.1f  自營=%.1f  合計=%.1f",
                        result.get("foreign", 0), result.get("trust", 0),
                        result.get("dealer", 0), result["total"])
            return result
    except Exception as e:
        logger.warning("[bfi82u] 失敗: %s", e)
    return None


def _fetch_tpex_institutional() -> dict | None:
    """
    上櫃三大法人買賣超金額（億元）。
    嘗試多個 TPEX 端點，任一成功即回傳。
    """
    def _to_yi(s) -> float | None:
        try:
            return round(int(str(s).replace(",", "").replace("+", "").strip()) / 1e5, 1)
        except (ValueError, TypeError):
            return None

    def _parse(latest: dict) -> dict:
        result: dict[str, float] = {}
        for key, val in latest.items():
            v = _to_yi(val)
            if v is None:
                continue
            kl = key.lower()
            if "外資及陸資" in key and "淨" in key and "不含" not in key and "自營" not in key:
                result["foreign"] = v
            elif "投信" in key and "淨" in key:
                result["trust"] = v
            elif "自營商" in key and "淨" in key and "(" not in key and "（" not in key:
                result["dealer"] = v
            elif "foreign" in kl and "net" in kl and "trust" not in kl and "dealer" not in kl:
                result.setdefault("foreign", v)
            elif "trust" in kl and "net" in kl and "foreign" not in kl:
                result.setdefault("trust", v)
            elif "dealer" in kl and "net" in kl:
                result.setdefault("dealer", v)
        return result

    candidate_urls = [
        "https://www.tpex.org.tw/openapi/v1/tpex_3insti_summary_bu",
        "https://www.tpex.org.tw/openapi/v1/ch/tpex_3insti_summary_bu",
        "https://openapi.tpex.org.tw/v1/tpex_3insti_summary_bu",
    ]
    for url in candidate_urls:
        try:
            j = _get_json(url)
            if not isinstance(j, list) or not j:
                logger.info("[tpex_insti] %s → 空回應", url.split("/")[-1])
                continue
            latest = j[-1]
            logger.info("[tpex_insti] 欄位：%s", list(latest.keys()))
            result = _parse(latest)
            if result:
                logger.info("[tpex_insti] 外資=%.1f  投信=%.1f  自營=%.1f",
                            result.get("foreign", 0), result.get("trust", 0),
                            result.get("dealer", 0))
                return result
            logger.info("[tpex_insti] 欄位解析無結果，繼續嘗試下一端點")
        except Exception as e:
            logger.info("[tpex_insti] %s 失敗: %s", url.split("/")[-1], e)

    logger.warning("[tpex_insti] 所有端點均失敗，上櫃三大法人資料無法取得")
    return None


def _fetch_futures_position() -> dict | None:
    """
    外資台指期貨未平倉淨多單口數。
    Strategy 1：TAIFEX Open API（openapi.taifex.com.tw）。
    Strategy 2：TAIFEX 官網 HTML 表格備援。

    TAIFEX API 實際欄位名：
      商品名稱 = "TX"（非 "臺股期貨"）
      身份別   = "外資及陸資"
    """
    import re as _re
    import urllib.parse

    def _int(item: dict, *keys: str) -> int:
        for k in keys:
            v = item.get(k)
            if v is not None:
                try:
                    return int(str(v).replace(",", "").strip() or "0")
                except (ValueError, TypeError):
                    pass
        return 0

    # ── Strategy 1: TAIFEX Open API ──────────────────────────────────────────
    for url in [
        "https://openapi.taifex.com.tw/v1/DailyForeignInstitutionalInvestors",
        "https://openapi.taifex.com.tw/v1/TaifexDailyForeignInstitutionalInvestors",
    ]:
        try:
            j = _get_json(url)
            # 兼容 dict wrapper {"data": [...]}
            if isinstance(j, dict):
                for wrap_key in ("data", "Data", "result", "records"):
                    if isinstance(j.get(wrap_key), list):
                        j = j[wrap_key]
                        break
            if not isinstance(j, list) or not j:
                logger.info("[futures] API %s → 空或非 list", url.split("/")[-1])
                continue

            logger.info("[futures] API %d 筆，欄位：%s", len(j), list(j[0].keys()))

            lo = so = no = 0
            found = False
            for item in j:
                contract = str(
                    item.get("ContractName") or item.get("商品名稱") or
                    item.get("契約名稱") or item.get("Contract") or ""
                ).strip()
                institution = str(
                    item.get("InstitutionName") or item.get("身份別") or
                    item.get("投資人別") or item.get("Institution") or ""
                ).strip()

                if not any(k in contract for k in ("TX", "臺股", "台股")):
                    continue
                if "外資" not in institution:
                    continue

                lo += _int(item, "LongOpenInterest",  "多方未平倉口數")
                so += _int(item, "ShortOpenInterest", "空方未平倉口數")
                no += _int(item, "NetOpenInterest",   "多空淨額未平倉口數")
                found = True

            if found:
                if lo or so:
                    return {"long_oi": lo, "short_oi": so, "net": lo - so}
                if no:
                    return {"long_oi": 0, "short_oi": 0, "net": no}

        except Exception as e:
            logger.info("[futures] API 失敗: %s", e)

    # ── Strategy 2: TAIFEX 官網 HTML 備援 ───────────────────────────────────
    try:
        today = date.today()
        d_str = f"{today.year}/{today.month:02d}/{today.day:02d}"
        html_url = (
            "https://www.taifex.com.tw/cht/3/futContractsDt"
            f"?queryStartDate={d_str}&queryEndDate={d_str}&contractId=TX"
        )
        req = urllib.request.Request(html_url, headers={
            "User-Agent":      _UA,
            "Accept":          "text/html,application/xhtml+xml",
            "Accept-Language": "zh-TW,zh;q=0.9",
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()

        for enc in ("utf-8", "big5", "cp950"):
            try:
                content = raw.decode(enc); break
            except UnicodeDecodeError:
                pass
        else:
            content = raw.decode("utf-8", errors="replace")

        logger.info("[futures_html] 頁面長度 %d 字元", len(content))

        # 找 外資及陸資 區段並提取數字
        # 表格欄順序：多方交易口數×2、空方交易口數×2、淨額交易×2、
        #             多方未平倉×2、空方未平倉×2、淨額未平倉×2（共12個）
        lo = so = 0
        found = False
        for tr_html in _re.findall(r'<tr[^>]*>(.*?)</tr>', content,
                                   _re.DOTALL | _re.IGNORECASE):
            if "外資及陸資" not in tr_html:
                continue
            nums = [int(m.replace(",", ""))
                    for m in _re.findall(r'<td[^>]*>\s*([\d,]+)\s*</td>', tr_html)]
            logger.info("[futures_html] 外資行 nums(%d)=%s", len(nums), nums[:12])
            if len(nums) >= 10:
                lo += nums[6]   # 多方未平倉口數
                so += nums[8]   # 空方未平倉口數
                found = True
            elif len(nums) >= 4:
                lo += nums[-4]
                so += nums[-2]
                found = True

        if found:
            logger.info("[futures_html] 解析成功 多=%d 空=%d", lo, so)
            return {"long_oi": lo, "short_oi": so, "net": lo - so}
        logger.info("[futures_html] 未找到外資及陸資行")

    except Exception as e:
        logger.info("[futures_html] 失敗: %s", e)

    logger.warning("[futures] 所有管道均失敗，外資台指期資料無法取得")
    return None


def fetch_market_overview() -> dict:
    """
    大盤總覽：加權指數 + 三大法人合計（上市＋上櫃，億元）+ 外資期貨淨多單。
    任何子項目失敗不影響其他項目，回傳 None 表示該項目無資料。
    """
    insti_twse = _fetch_bfi82u()
    insti_tpex = _fetch_tpex_institutional()

    # 合併上市 + 上櫃，任一有資料即顯示（另一方缺資料視為 0）
    insti: dict | None = None
    if insti_twse or insti_tpex:
        insti = {}
        for key in ("foreign", "trust", "dealer"):
            t = (insti_twse or {}).get(key)
            p = (insti_tpex or {}).get(key)
            if t is not None or p is not None:
                insti[key] = round((t or 0.0) + (p or 0.0), 1)
        if insti:
            insti["total"] = round(sum(insti.values()), 1)
        else:
            insti = None

    return {
        "index":   _fetch_taiex_index(),
        "insti":   insti,
        "futures": _fetch_futures_position(),
    }


def fetch_foreign_rank() -> dict:
    """外資買賣超排行（上市 + 上櫃 Top 10），含連續天數標記。"""
    raw = _fetch_all_raw()
    all_stocks = raw["twse_foreign"] + raw["tpex_foreign"]
    rank = _build_rank(all_stocks, raw["date"])
    try:
        snaps = _get_hist_snapshots()
        _annotate_streaks(rank, "foreign", snaps)
    except Exception as e:
        logger.warning("[streak] 外資連續天數計算失敗: %s", e)
    return rank


def fetch_trust_rank() -> dict:
    """投信買賣超排行（上市 + 上櫃 Top 10），含連續天數標記。"""
    raw = _fetch_all_raw()
    all_stocks = raw["twse_trust"] + raw["tpex_trust"]
    rank = _build_rank(all_stocks, raw["date"])
    try:
        snaps = _get_hist_snapshots()
        _annotate_streaks(rank, "trust", snaps)
    except Exception as e:
        logger.warning("[streak] 投信連續天數計算失敗: %s", e)
    return rank


def fetch_institutional_active() -> dict:
    """
    法人積極資金族群：外資＋投信同日雙向買超的股票。
    回傳：
    {
      "date": str,
      "stocks": [
        {
          "code", "name", "market",
          "foreign_net_k", "trust_net_k", "total_net_k"
        }, ...
      ]  # 按合計買超張數排序，最多 TOP_ACTIVE 筆
    }
    """
    raw = _fetch_all_raw()

    # 建立外資買超 map {code → stock_dict}
    foreign_buy: dict[str, dict] = {
        s["code"]: s
        for s in (raw["twse_foreign"] + raw["tpex_foreign"])
        if s["net_k"] > 0
    }
    # 建立投信買超 map {code → stock_dict}
    trust_buy: dict[str, dict] = {
        s["code"]: s
        for s in (raw["twse_trust"] + raw["tpex_trust"])
        if s["net_k"] > 0
    }

    # 取交集：同日外資＋投信同時買超
    both_codes = set(foreign_buy) & set(trust_buy)

    active = []
    for code in both_codes:
        f = foreign_buy[code]
        t = trust_buy[code]
        active.append({
            "code":          code,
            "name":          f["name"],
            "market":        f["market"],
            "foreign_net_k": f["net_k"],
            "trust_net_k":   t["net_k"],
            "total_net_k":   f["net_k"] + t["net_k"],
        })

    active.sort(key=lambda x: x["total_net_k"], reverse=True)
    logger.info("[institutional_active] 外資投信雙買超：%d 支", len(active))

    return {
        "date":   raw["date"],
        "stocks": active[:TOP_ACTIVE],
    }
