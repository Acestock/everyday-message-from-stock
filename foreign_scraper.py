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
from datetime import date
from typing import Optional

import pandas as pd
import yfinance as yf

from sector_themes import THEME_MAP

logger = logging.getLogger(__name__)

_lock = threading.Lock()

# 共用原始資料快取（TWSE + TPEx 一次抓完，三個公開函式共用）
_all_raw_cache: Optional[dict] = None
_all_raw_cache_date: Optional[str] = None

# 產業類別 mapping 快取
_industry_map: dict[str, str] = {}
_industry_map_date: Optional[str] = None

TOP_N        = 10
TOP_SECTOR   = 10   # 族群買超/賣超顯示前 N 個產業
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
    """
    roc_date = _today_roc()
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
    data_date = twse_date or tpex_d or today.replace("-", "/")

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


# ── 共用排名邏輯 ──────────────────────────────────────────────────────────────

def _build_rank(all_stocks: list[dict], data_date: str) -> dict:
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
      "buy":  [{sector, foreign_net, trust_net, total_net}, ...],
      "sell": [{sector, foreign_net, trust_net, total_net}, ...],
    }
    """
    raw          = _fetch_all_raw()
    industry_map = _fetch_industry_map()

    sectors: dict[str, dict[str, int]] = {}

    def _agg(stocks: list[dict], key: str) -> None:
        for s in stocks:
            # THEME_MAP 優先，次則 TWSE/TPEx 官方分類，最後 fallback 為「其他」
            sec = THEME_MAP.get(s["code"]) or industry_map.get(s["code"], "其他")
            if sec not in sectors:
                sectors[sec] = {"foreign_net": 0, "trust_net": 0}
            sectors[sec][key] += s["net_k"]

    _agg(raw["twse_foreign"] + raw["tpex_foreign"], "foreign_net")
    _agg(raw["twse_trust"]   + raw["tpex_trust"],   "trust_net")

    rows = [
        {
            "sector":      sec,
            "foreign_net": v["foreign_net"],
            "trust_net":   v["trust_net"],
            "total_net":   v["foreign_net"] + v["trust_net"],
        }
        for sec, v in sectors.items()
    ]

    buy  = sorted([r for r in rows if r["total_net"] > 0],
                  key=lambda x: x["total_net"], reverse=True)[:TOP_SECTOR]
    sell = sorted([r for r in rows if r["total_net"] < 0],
                  key=lambda x: x["total_net"])[:TOP_SECTOR]

    logger.info("[sector] 買超族群 %d 個，賣超族群 %d 個", len(buy), len(sell))
    return {"date": raw["date"], "buy": buy, "sell": sell}


def fetch_foreign_rank() -> dict:
    """外資買賣超排行（上市 + 上櫃 Top 10）。"""
    raw = _fetch_all_raw()
    all_stocks = raw["twse_foreign"] + raw["tpex_foreign"]
    return _build_rank(all_stocks, raw["date"])


def fetch_trust_rank() -> dict:
    """投信買賣超排行（上市 + 上櫃 Top 10）。"""
    raw = _fetch_all_raw()
    all_stocks = raw["twse_trust"] + raw["tpex_trust"]
    return _build_rank(all_stocks, raw["date"])


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
