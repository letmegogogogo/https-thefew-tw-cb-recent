from __future__ import annotations

import json
import os
import ssl
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = ROOT / "outputs" / "exchange-rate-data.js"
CBC_API_URL = "https://cpx.cbc.gov.tw/api/OpenData/FTDOpenData_Day"
CBC_SOURCE_URL = "https://data.gov.tw/dataset/7232"
DXY_API_URL = "https://query2.finance.yahoo.com/v8/finance/chart/DX-Y.NYB?range=18mo&interval=1d"
DXY_FALLBACK_API_URL = "https://query1.finance.yahoo.com/v8/finance/chart/DX-Y.NYB?range=18mo&interval=1d"
DXY_SOURCE_URL = "https://finance.yahoo.com/quote/DX-Y.NYB/history/"
TREASURY10Y_API_URL = "https://query2.finance.yahoo.com/v8/finance/chart/%5ETNX?range=18mo&interval=1d"
TREASURY10Y_FALLBACK_API_URL = "https://query1.finance.yahoo.com/v8/finance/chart/%5ETNX?range=18mo&interval=1d"
TREASURY10Y_SOURCE_URL = "https://finance.yahoo.com/quote/%5ETNX/history/"
TWSE_MARGIN_API_URL = "https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN"
TWSE_PRICE_API_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"
TWSE_MARGIN_SOURCE_URL = "https://www.twse.com.tw/zh/trading/margin/mi-margn.html"
PREFIX = "window.FX_RATE_DATA = "
TZ = timezone(timedelta(hours=8))


def load_previous() -> dict:
    try:
        text = OUTPUT_PATH.read_text(encoding="utf-8-sig").strip()
        if text.startswith(PREFIX):
            value = json.loads(text[len(PREFIX):].rstrip(";"))
            if isinstance(value, dict):
                return value
    except (OSError, ValueError):
        pass
    return {}


def fetch_json(url: str, timeout: int = 30):
    request = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "Accept": "application/json",
            "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
            "Referer": "https://finance.yahoo.com/",
        },
    )
    try:
        response = urlopen(request, timeout=timeout)
    except Exception as error:
        # Compatibility retry is restricted to the two hard-coded data URLs.
        if (url not in {
            CBC_API_URL,
            DXY_API_URL,
            DXY_FALLBACK_API_URL,
            TREASURY10Y_API_URL,
            TREASURY10Y_FALLBACK_API_URL,
        } and urlparse(url).hostname not in {"www.twse.com.tw"}) or not isinstance(
            getattr(error, "reason", None), ssl.SSLCertVerificationError
        ):
            raise
        response = urlopen(request, timeout=timeout, context=ssl._create_unverified_context())
    with response:
        return json.loads(response.read().decode("utf-8-sig"))


def fetch_first_json(urls: tuple[str, ...]):
    last_error: Exception | None = None
    for url in urls:
        try:
            return fetch_json(url)
        except Exception as error:
            last_error = error
    if last_error:
        raise last_error
    raise ValueError("No data URL configured")


def cutoff_date() -> str:
    return (datetime.now(TZ).date() - timedelta(days=380)).isoformat()


def normalize_usd_twd(rows: list[dict]) -> list[dict]:
    cutoff = cutoff_date().replace("-", "")
    by_date: dict[str, dict] = {}
    for row in rows:
        day = str(row.get("日期") or "").strip()
        value_text = str(row.get("NTD_USD") or row.get("NTD/USD") or "").replace(",", "").strip()
        if len(day) != 8 or not day.isdigit() or day < cutoff:
            continue
        try:
            value = float(value_text)
        except ValueError:
            continue
        if value <= 0:
            continue
        date_text = f"{day[:4]}-{day[4:6]}-{day[6:]}"
        by_date[date_text] = {"date": date_text, "value": round(value, 5)}
    points = sorted(by_date.values(), key=lambda item: item["date"])
    if len(points) < 200:
        raise ValueError(f"Insufficient USD/TWD history: {len(points)} points")
    return points


def normalize_yahoo_series(payload: dict, label: str) -> list[dict]:
    results = ((payload.get("chart") or {}).get("result") or []) if isinstance(payload, dict) else []
    if not results:
        raise ValueError(f"{label} API returned no chart result")
    result = results[0]
    timestamps = result.get("timestamp") or []
    quote_sets = ((result.get("indicators") or {}).get("quote") or [])
    closes = quote_sets[0].get("close") or [] if quote_sets else []
    cutoff = cutoff_date()
    by_date: dict[str, dict] = {}
    for timestamp, close in zip(timestamps, closes):
        if close is None:
            continue
        try:
            value = float(close)
            date_text = datetime.fromtimestamp(int(timestamp), timezone.utc).date().isoformat()
        except (TypeError, ValueError, OSError):
            continue
        if date_text < cutoff or value <= 0:
            continue
        by_date[date_text] = {"date": date_text, "value": round(value, 5)}
    points = sorted(by_date.values(), key=lambda item: item["date"])
    if len(points) < 200:
        raise ValueError(f"Insufficient {label} history: {len(points)} points")
    return points


def current_or_previous(label: str, loader, previous_points: list[dict]) -> tuple[list[dict], str]:
    try:
        return loader(), "updated"
    except Exception as error:
        if previous_points:
            print(f"{label} update failed; kept previous series: {type(error).__name__}: {error}")
            return previous_points, "kept_previous"
        raise


def number(value) -> float | None:
    text = str(value or "").replace(",", "").replace("X", "").strip()
    if not text or text in {"--", "---", "-"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def table_rows(payload: dict, required_fields: tuple[str, ...]) -> tuple[list[str], list[list]]:
    for table in payload.get("tables") or []:
        fields = [str(item) for item in (table.get("fields") or [])]
        if all(field in fields for field in required_fields):
            rows = [row for row in (table.get("data") or []) if isinstance(row, list)]
            return fields, rows
    return [], []


def fetch_twse_margin_day(day) -> dict | None:
    date_text = day.strftime("%Y%m%d")
    margin_url = TWSE_MARGIN_API_URL + "?" + urlencode({
        "date": date_text,
        "selectType": "ALL",
        "response": "json",
    })
    price_url = TWSE_PRICE_API_URL + "?" + urlencode({
        "date": date_text,
        "type": "ALLBUT0999",
        "response": "json",
    })
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            margin_payload = fetch_json(margin_url, timeout=20)
            price_payload = fetch_json(price_url, timeout=20)
            if margin_payload.get("stat") != "OK" or price_payload.get("stat") != "OK":
                return None

            _, summary_rows = table_rows(margin_payload, ("項目", "今日餘額"))
            margin_money_thousand = next(
                (number(row[-1]) for row in summary_rows if row and "融資金額" in str(row[0])),
                None,
            )
            margin_fields, margin_rows = table_rows(margin_payload, ("代號", "名稱", "今日餘額"))
            price_fields, price_rows = table_rows(price_payload, ("證券代號", "證券名稱", "收盤價"))
            if not margin_money_thousand or not margin_rows or not price_rows:
                return None

            price_code_index = price_fields.index("證券代號")
            close_index = price_fields.index("收盤價")
            prices = {
                str(row[price_code_index]).strip(): number(row[close_index])
                for row in price_rows
                if len(row) > max(price_code_index, close_index)
            }
            code_index = margin_fields.index("代號")
            balance_index = margin_fields.index("今日餘額")
            collateral_thousand = 0.0
            matched = 0
            for row in margin_rows:
                if len(row) <= max(code_index, balance_index):
                    continue
                close = prices.get(str(row[code_index]).strip())
                balance = number(row[balance_index])
                if close is None or balance is None or balance <= 0:
                    continue
                collateral_thousand += balance * close
                matched += 1
            if matched < 50 or collateral_thousand <= 0:
                return None
            return {
                "date": day.isoformat(),
                "value": round(collateral_thousand / margin_money_thousand * 100, 3),
                "marginBalanceBillion": round(margin_money_thousand / 100000, 2),
                "matchedSecurities": matched,
            }
        except Exception as error:
            last_error = error
            if attempt == 0:
                time.sleep(0.35)
    if last_error:
        print(f"TWSE margin maintenance {day.isoformat()} failed: {type(last_error).__name__}: {last_error}")
    return None


def update_margin_maintenance(previous_points: list[dict]) -> tuple[list[dict], str]:
    today = datetime.now(TZ).date()
    valid_previous = [
        point for point in previous_points
        if isinstance(point, dict) and point.get("date") and number(point.get("value")) is not None
    ]
    existing_dates = {point["date"] for point in valid_previous}
    missing_days = []
    cursor = today - timedelta(days=380)
    while cursor <= today:
        if cursor.weekday() < 5 and cursor.isoformat() not in existing_dates:
            missing_days.append(cursor)
        cursor += timedelta(days=1)
    # Newest gaps matter most for 1M/6M charts. Small daily batches avoid
    # triggering TWSE rate limits; repeated scheduled runs gradually fill 1Y.
    max_days = max(1, int(os.environ.get("TWSE_MARGIN_MAX_DAYS", "15")))
    days = sorted(missing_days, reverse=True)[:max_days]

    fetched: list[dict] = []
    if days:
        for day in days:
            point = fetch_twse_margin_day(day)
            if point:
                fetched.append(point)
            time.sleep(0.45)
    merged = {
        point["date"]: point
        for point in [*valid_previous, *fetched]
        if point.get("date") >= (today - timedelta(days=380)).isoformat()
    }
    points = sorted(merged.values(), key=lambda point: point["date"])
    if not points:
        raise ValueError("No TWSE margin maintenance points available")
    return points, "updated" if fetched else "kept_previous"


def main() -> int:
    previous = load_previous()
    previous_usd_twd = previous.get("usdTwdPoints") or [
        {"date": item.get("date"), "value": item.get("usdTwd")}
        for item in previous.get("points", [])
        if item.get("date") and item.get("usdTwd") is not None
    ]
    previous_dxy = previous.get("dxyPoints") or []
    previous_treasury10y = previous.get("treasury10yPoints") or []
    previous_margin_maintenance = previous.get("marginMaintenancePoints") or []

    usd_twd_points, usd_twd_status = current_or_previous(
        "USD/TWD",
        lambda: normalize_usd_twd(fetch_json(CBC_API_URL)),
        previous_usd_twd,
    )
    dxy_points, dxy_status = current_or_previous(
        "DXY",
        lambda: normalize_yahoo_series(
            fetch_first_json((DXY_API_URL, DXY_FALLBACK_API_URL)), "DXY"
        ),
        previous_dxy,
    )
    treasury10y_points, treasury10y_status = current_or_previous(
        "US10Y",
        lambda: normalize_yahoo_series(
            fetch_first_json((TREASURY10Y_API_URL, TREASURY10Y_FALLBACK_API_URL)), "US10Y"
        ),
        previous_treasury10y,
    )
    margin_maintenance_points, margin_maintenance_status = update_margin_maintenance(
        previous_margin_maintenance
    )

    payload = {
        "source": "中央銀行統計資料庫、Yahoo Finance",
        "fetchedAt": datetime.now(TZ).isoformat(),
        "latestDate": max(
            usd_twd_points[-1]["date"], dxy_points[-1]["date"], treasury10y_points[-1]["date"],
            margin_maintenance_points[-1]["date"]
        ),
        "usdTwdLatestDate": usd_twd_points[-1]["date"],
        "dxyLatestDate": dxy_points[-1]["date"],
        "treasury10yLatestDate": treasury10y_points[-1]["date"],
        "marginMaintenanceLatestDate": margin_maintenance_points[-1]["date"],
        "usdTwdPoints": usd_twd_points,
        "dxyPoints": dxy_points,
        "treasury10yPoints": treasury10y_points,
        "marginMaintenancePoints": margin_maintenance_points,
        "sources": {
            "usdTwd": {
                "name": "中央銀行銀行間市場收盤匯率",
                "url": CBC_SOURCE_URL,
                "apiUrl": CBC_API_URL,
                "status": usd_twd_status,
            },
            "dxy": {
                "name": "Yahoo Finance US Dollar Index (DX-Y.NYB)",
                "url": DXY_SOURCE_URL,
                "apiUrl": DXY_API_URL,
                "status": dxy_status,
            },
            "treasury10y": {
                "name": "Yahoo Finance CBOE 10 Year Treasury Note Yield (^TNX)",
                "url": TREASURY10Y_SOURCE_URL,
                "apiUrl": TREASURY10Y_API_URL,
                "status": treasury10y_status,
            },
            "marginMaintenance": {
                "name": "臺灣證券交易所上市市場融資維持率估算",
                "url": TWSE_MARGIN_SOURCE_URL,
                "apiUrl": TWSE_MARGIN_API_URL,
                "status": margin_maintenance_status,
                "formula": "個股融資餘額×收盤價合計÷集中市場融資金額餘額×100",
            },
        },
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp = OUTPUT_PATH.with_suffix(".tmp")
    temp.write_text(PREFIX + json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + ";\n", encoding="utf-8")
    temp.replace(OUTPUT_PATH)
    print(
        "Updated exchange data: "
        f"USD/TWD={len(usd_twd_points)} ({usd_twd_points[-1]['date']}), "
        f"DXY={len(dxy_points)} ({dxy_points[-1]['date']}), "
        f"US10Y={len(treasury10y_points)} ({treasury10y_points[-1]['date']}), "
        f"TWSE margin maintenance={len(margin_maintenance_points)} "
        f"({margin_maintenance_points[-1]['date']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
