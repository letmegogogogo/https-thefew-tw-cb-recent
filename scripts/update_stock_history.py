from __future__ import annotations

import concurrent.futures
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests


ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "outputs" / "recent-cb-data.js"
PREFIX = "window.RECENT_CB_DATA = "
TZ = timezone(timedelta(hours=8))


def number(value):
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def load_data():
    text = DATA_PATH.read_text(encoding="utf-8").strip()
    return json.loads(text.removeprefix(PREFIX).rstrip(";"))


def fetch_history(item):
    code, market = item
    symbol = code + (".TW" if market == "TWSE" else ".TWO")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    period2 = int(time.time()) + 86400
    payload = None
    for attempt in range(4):
        try:
            response = requests.get(
                url,
                params={"period1": 0, "period2": period2, "interval": "1d", "events": "history"},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=35,
            )
            response.raise_for_status()
            payload = response.json()
            break
        except Exception:
            time.sleep(0.8 * (attempt + 1))
    try:
        result = payload["chart"]["result"][0]
        timestamps = result.get("timestamp") or []
        closes = result["indicators"]["quote"][0].get("close") or []
    except (TypeError, KeyError, IndexError):
        return code, None
    points = []
    for timestamp, raw_close in zip(timestamps, closes):
        close = number(raw_close)
        if close is None:
            continue
        points.append({
            "date": datetime.fromtimestamp(timestamp, TZ).date().isoformat(),
            "close": close,
        })
    if len(points) < 2:
        return code, None
    current, previous = points[-1], points[-2]
    previous_high = max(point["close"] for point in points[:-1])
    latest_day = datetime.strptime(current["date"], "%Y-%m-%d").date()
    week_start = latest_day - timedelta(days=latest_day.weekday())
    previous_week = [
        point for point in points
        if datetime.strptime(point["date"], "%Y-%m-%d").date() < week_start
    ]
    week_base = previous_week[-1]["close"] if previous_week else None
    return code, {
        "stockClose": current["close"],
        "stockPrevClose": previous["close"],
        "stockChangePct": (current["close"] / previous["close"] - 1) * 100 if previous["close"] else None,
        "stockWeeklyChangePct": (current["close"] / week_base - 1) * 100 if week_base else None,
        "stockQuoteDate": current["date"],
        "stockHistoryHigh": previous_high,
        "stockHistoryStart": points[0]["date"],
        "stockHistoryCount": len(points) - 1,
        "stockHistoryValid": True,
        "stockIsRecordHigh": round(current["close"] * 100) >= round(previous_high * 100),
        "stockHistorySourceUrl": f"{url}?period1=0&period2={period2}&interval=1d",
    }


def main():
    data = load_data()
    rows = data.get("rows") or []
    issuers = {}
    for row in rows:
        code = str(row.get("issuerCode") or "").strip()
        if code:
            issuers[code] = row.get("stockMarket") or "OTC"
    histories = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(fetch_history, item) for item in issuers.items()]
        for future in concurrent.futures.as_completed(futures):
            code, result = future.result()
            if result:
                histories[code] = result
    for row in rows:
        history = histories.get(str(row.get("issuerCode") or ""))
        if history:
            row.update(history)

    industry_values = {}
    for code, market in issuers.items():
        result = histories.get(code)
        if not result or result.get("stockWeeklyChangePct") is None:
            continue
        industry = next((row.get("industryCategory") or "-" for row in rows if str(row.get("issuerCode") or "") == code), "-")
        industry_values.setdefault(industry, []).append(result["stockWeeklyChangePct"])
    industry_averages = {
        industry: sum(values) / len(values)
        for industry, values in industry_values.items()
        if values
    }
    for row in rows:
        average = industry_averages.get(row.get("industryCategory") or "-")
        row["industryWeeklyAveragePct"] = average
        row["industryTrendAlert"] = (
            "族群性上漲" if average is not None and average >= 10
            else "族群性下跌" if average is not None and average <= -10
            else None
        )
    updated_at = datetime.now(TZ).isoformat(timespec="seconds")
    data["stockHistoryUpdatedAt"] = updated_at
    data["stockHistorySource"] = "Yahoo Finance Taiwan listed/OTC complete daily close history"
    data["stockWeeklyChangeDefinition"] = "Latest close versus the final trading close before the current week"
    DATA_PATH.write_text(PREFIX + json.dumps(data, ensure_ascii=False, indent=2) + ";\n", encoding="utf-8")
    alerts = {industry: value for industry, value in industry_averages.items() if abs(value) >= 10}
    print(json.dumps({"issuers": len(issuers), "historyCovered": len(histories), "industryAlerts": alerts}, ensure_ascii=False))


if __name__ == "__main__":
    main()
