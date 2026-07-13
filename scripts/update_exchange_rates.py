from __future__ import annotations

import json
import ssl
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = ROOT / "outputs" / "exchange-rate-data.js"
SOURCE_URL = "https://cpx.cbc.gov.tw/api/OpenData/FTDOpenData_Day"
SOURCE_PAGE_URL = "https://data.gov.tw/dataset/7232"
PREFIX = "window.FX_RATE_DATA = "
TZ = timezone(timedelta(hours=8))


def load_previous() -> dict:
    try:
        text = OUTPUT_PATH.read_text(encoding="utf-8-sig").strip()
        if text.startswith(PREFIX):
            value = json.loads(text[len(PREFIX):].rstrip(";"))
            if isinstance(value, dict) and value.get("points"):
                return value
    except (OSError, ValueError):
        pass
    return {}


def fetch_rows(timeout: int = 30) -> list[dict]:
    request = Request(
        SOURCE_URL,
        headers={
            "User-Agent": "Mozilla/5.0 Chrome/126 Safari/537.36",
            "Accept": "application/json",
        },
    )
    try:
        response = urlopen(request, timeout=timeout)
    except Exception as error:
        # Some Windows Python builds reject the CBC certificate chain. This
        # compatibility retry is restricted to the hard-coded official URL.
        if not isinstance(getattr(error, "reason", None), ssl.SSLCertVerificationError):
            raise
        response = urlopen(request, timeout=timeout, context=ssl._create_unverified_context())
    with response:
        payload = json.loads(response.read().decode("utf-8-sig"))
    if not isinstance(payload, list):
        raise ValueError("Central Bank API returned a non-list payload")
    return payload


def normalize(rows: list[dict]) -> list[dict]:
    cutoff = (datetime.now(TZ).date() - timedelta(days=380)).strftime("%Y%m%d")
    by_date: dict[str, dict] = {}
    for row in rows:
        day = str(row.get("日期") or "").strip()
        value_text = str(row.get("NTD_USD") or row.get("NTD/USD") or "").replace(",", "").strip()
        if len(day) != 8 or not day.isdigit() or day < cutoff:
            continue
        try:
            usd_twd = float(value_text)
        except ValueError:
            continue
        if usd_twd <= 0:
            continue
        date_text = f"{day[:4]}-{day[4:6]}-{day[6:]}"
        by_date[date_text] = {
            "date": date_text,
            "usdTwd": round(usd_twd, 5),
            "twdUsd": round(1 / usd_twd, 8),
        }
    points = sorted(by_date.values(), key=lambda item: item["date"])
    if len(points) < 200:
        raise ValueError(f"Insufficient exchange-rate history: {len(points)} points")
    return points


def main() -> int:
    previous = load_previous()
    try:
        points = normalize(fetch_rows())
    except Exception as error:
        if previous:
            print(f"Exchange-rate update failed; kept previous data: {type(error).__name__}: {error}")
            return 0
        raise

    payload = {
        "pair": "USD/TWD",
        "unit": "TWD per USD",
        "source": "中央銀行統計資料庫",
        "sourceUrl": SOURCE_PAGE_URL,
        "apiUrl": SOURCE_URL,
        "methodology": "銀行間市場新臺幣對美元每日收盤匯率",
        "fetchedAt": datetime.now(TZ).isoformat(),
        "latestDate": points[-1]["date"],
        "points": points,
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp = OUTPUT_PATH.with_suffix(".tmp")
    temp.write_text(PREFIX + json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + ";\n", encoding="utf-8")
    temp.replace(OUTPUT_PATH)
    print(f"Updated exchange rates: {len(points)} points, latest={points[-1]['date']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
