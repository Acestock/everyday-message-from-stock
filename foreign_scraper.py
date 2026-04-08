"""
外資 / 投信買賣超：每日從 TWSE（上市）與 TPEx（上櫃）抓取前 10 大買超 / 賣超個股。

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
from datetime import date
from typing import Optional

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

_lock = threading.Lock()

# 分別快取外資 / 投信
_foreign_cache: dict             = {}
_foreign_cache_date: Optional[str] = None
_trust_cache: dict               = {}
_trust_cache_date: Optional[str] = None

TOP_N   = 10
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
        date_str = (
            f"{raw_date[:4]}/{raw_date[4:6]}/{raw_date[6:]}"
            if len(raw_date) == 8 else ""
        )

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

def _fetch_tpex_3insti() -> tuple[list[dict], list[dict], str]:
    """
    上櫃三大法人買賣明細（一支 API 同時含外資與投信）。
    回傳 (foreign_stocks, trust_stocks, date_str)。

    欄位順序（千股 = 張）：
      0  代號
      1  名稱
      2  外資買進
      3  外資賣出
      4  外資買賣超
      5  投信買進
      6  投信賣出
      7  投信買賣超
      8  自營商買賣超(含避險)
      9  三大法人合計買賣超
    """
    roc_date = _today_roc()
    # 嘗試多組 URL；通常帶日期參數的第一個就能成功
    candidate_urls = [
        (
            "https://www.tpex.org.tw/web/stock/3insti/daily_trade/"
            f"3itrade_hedge_result.php?l=zh-tw&t=D&d={roc_date}&se=EW&o=json"
        ),
        (
            "https://www.tpex.org.tw/web/stock/3insti/daily_trade/"
            "3itrade_hedge_result.php?l=zh-tw&t=D&se=EW&o=json"
        ),
        (
            "https://www.tpex.org.tw/web/stock/3insti/daily_trade/"
            f"3itrade_hedge_result.php?l=zh-tw&t=D&d={roc_date}&se=AL&o=json"
        ),
    ]
    referer = "https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge.php?l=zh-tw"

    j: dict = {}
    for url in candidate_urls:
        try:
            j    = _get_json(url, referer=referer)
            rows = (j.get("tables") or [{}])[0].get("data", [])
            if rows:
                break
        except Exception as e:
            logger.warning("[tpex_3insti] %s → 失敗: %s", url.split("?")[1], e)

    try:
        table    = (j.get("tables") or [{}])[0]
        raw_date = table.get("date", "") or j.get("date", "")
        date_str = _roc_to_ce(raw_date) if raw_date else ""
        rows     = table.get("data", [])

        # 欄位索引（columnNum=25）：
        # 0 代號, 1 名稱
        # 2-4  外資(不含自營)買進/賣出/買賣超
        # 5-7  外資自營商買進/賣出/買賣超
        # 8-10 外資及陸資合計買進/賣出/買賣超  ← col 10
        # 11-13 投信買進/賣出/買賣超           ← col 13
        # 14-22 自營商(自行/避險/合計) ...
        # 23   三大法人合計買賣超
        FOREIGN_COL = 10
        TRUST_COL   = 13

        foreign_list: list[dict] = []
        trust_list:   list[dict] = []
        for row in rows:
            try:
                code = str(row[0]).strip()
                name = str(row[1]).strip()
                fnet = round(_clean_num(row[FOREIGN_COL]) / 1000)   # 股 → 張
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


# ── MA 技術資料 ───────────────────────────────────────────────────────────────

def _download_ohlcv(tickers: list[str]) -> tuple[dict, dict]:
    """批次下載近 3 個月收盤 + 成交量；回傳 (closes_dict, volumes_dict)。"""
    if not tickers:
        return {}, {}
    try:
        df = yf.download(tickers, period="3mo", progress=False, auto_adjust=False)
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
      - list[dict]：每筆含 code/market，依 market 只查對應 suffix，
                    避免把上市股誤當上櫃查（yfinance 噪音）
    回傳：
      {code: {price, ma20, ma20_up, above_ma20, ma60, ma60_up, above_ma60}}
    找不到資料的 code 不會出現在結果中。
    """
    result: dict[str, dict] = {}
    if not codes:
        return result

    # 若傳入 dict 清單，依 market 分流；否則退回舊行為
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
            "volume_k":   vol_k,          # 今日成交量（張），供占比計算
        }

    logger.debug("[ma] fetch_ma_data: %d / %d 支成功", len(result), len(codes))
    return result


# ── 共用排名邏輯 ──────────────────────────────────────────────────────────────

def _build_rank(all_stocks: list[dict], data_date: str) -> dict:
    """
    依市場（上市 / 上櫃）分別排名，回傳：
    {
      "date": str,
      "twse": {"buy": [...Top10], "sell": [...Top10]},
      "tpex": {"buy": [...Top10], "sell": [...Top10]},
    }
    """
    def _split(stocks: list[dict]) -> dict:
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


# ── 公開 API ───────────────────────────────────────────────────────────────────

def fetch_foreign_rank() -> dict:
    """
    抓取外資買賣超（TWSE + TPEx），回傳：
    {"date","buy":[{code,name,net_k,market}],"sell":[...]}
    結果按交易日快取。
    """
    global _foreign_cache, _foreign_cache_date

    today = date.today().strftime("%Y-%m-%d")
    with _lock:
        if _foreign_cache_date == today and _foreign_cache:
            return _foreign_cache

    twse_stocks, twse_date           = _fetch_twse_base("foreign")
    tpex_foreign, _, tpex_date       = _fetch_tpex_3insti()
    all_stocks = twse_stocks + tpex_foreign
    data_date  = twse_date or tpex_date or today.replace("-", "/")

    result = _build_rank(all_stocks, data_date)
    with _lock:
        _foreign_cache      = result
        _foreign_cache_date = today
    return result


def fetch_trust_rank() -> dict:
    """
    抓取投信買賣超（TWSE + TPEx），回傳：
    {"date","buy":[{code,name,net_k,market}],"sell":[...]}
    結果按交易日快取。
    """
    global _trust_cache, _trust_cache_date

    today = date.today().strftime("%Y-%m-%d")
    with _lock:
        if _trust_cache_date == today and _trust_cache:
            return _trust_cache

    twse_stocks, twse_date           = _fetch_twse_base("trust")
    _, tpex_trust, tpex_date         = _fetch_tpex_3insti()
    all_stocks = twse_stocks + tpex_trust
    data_date  = twse_date or tpex_date or today.replace("-", "/")

    result = _build_rank(all_stocks, data_date)
    with _lock:
        _trust_cache      = result
        _trust_cache_date = today
    return result
