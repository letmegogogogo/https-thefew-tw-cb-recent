from __future__ import annotations

import json
import ssl
from datetime import datetime, timedelta, timezone
from pathlib import Path
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
        if url not in {
            CBC_API_URL,
            DXY_API_URL,
            DXY_FALLBACK_API_URL,
            TREASURY10Y_API_URL,
            TREASURY10Y_FALLBACK_API_URL,
        } or not isinstance(
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


def main() -> int:
    previous = load_previous()
    previous_usd_twd = previous.get("usdTwdPoints") or [
        {"date": item.get("date"), "value": item.get("usdTwd")}
        for item in previous.get("points", [])
        if item.get("date") and item.get("usdTwd") is not None
    ]
    previous_dxy = previous.get("dxyPoints") or []
    previous_treasury10y = previous.get("treasury10yPoints") or []

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

    payload = {
        "source": "中央銀行統計資料庫、Yahoo Finance",
        "fetchedAt": datetime.now(TZ).isoformat(),
        "latestDate": max(
            usd_twd_points[-1]["date"], dxy_points[-1]["date"], treasury10y_points[-1]["date"]
        ),
        "usdTwdLatestDate": usd_twd_points[-1]["date"],
        "dxyLatestDate": dxy_points[-1]["date"],
        "treasury10yLatestDate": treasury10y_points[-1]["date"],
        "usdTwdPoints": usd_twd_points,
        "dxyPoints": dxy_points,
        "treasury10yPoints": treasury10y_points,
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
        f"US10Y={len(treasury10y_points)} ({treasury10y_points[-1]['date']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
