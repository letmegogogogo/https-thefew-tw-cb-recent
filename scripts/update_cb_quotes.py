from __future__ import annotations

import csv
import io
import json
import re
import ssl
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "outputs" / "recent-cb-data.js"
HISTORY_DIR = ROOT / "outputs" / "cb-history"
STOCK_TAGS_PATH = ROOT / "data" / "tw-stock-tags.json"
ISSUANCE_PURPOSE_PATH = ROOT / "data" / "cb-issuance-purpose.json"
REDEMPTION_ALERTS_PATH = ROOT / "data" / "cb-redemption-alerts.json"
WEEKLY_TOP30_TRACKER_PATH = ROOT / "data" / "weekly-stock-top30-tracker.json"
PREFIX = "window.RECENT_CB_DATA = "
TZ = timezone(timedelta(hours=8))
HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://mis.twse.com.tw/stock/fibest.jsp",
}


def fetch_json(url: str):
    request = Request(url, headers=HEADERS)
    try:
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception:
        if "openapi.twse.com.tw" not in url:
            raise
        context = ssl._create_unverified_context()
        with urlopen(request, timeout=30, context=context) as response:
            return json.loads(response.read().decode("utf-8"))


def number(value):
    try:
        if value in (None, "", "-"):
            return None
        text = str(value).strip().replace(",", "").replace("--", "")
        return float(text) if text else None
    except (TypeError, ValueError):
        return None


def price_units(value) -> int | None:
    parsed = number(value)
    return round(parsed * 100) if parsed is not None else None


def load_data() -> dict:
    text = DATA_PATH.read_text(encoding="utf-8").strip()
    if not text.startswith(PREFIX):
        raise ValueError("recent-cb-data.js format is invalid")
    return json.loads(text[len(PREFIX) :].rstrip(";"))


def load_stock_tags() -> dict:
    try:
        payload = json.loads(STOCK_TAGS_PATH.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError):
        return {}


def load_issuance_purposes() -> dict:
    try:
        payload = json.loads(ISSUANCE_PURPOSE_PATH.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError):
        return {}


def load_redemption_alerts() -> dict:
    try:
        payload = json.loads(REDEMPTION_ALERTS_PATH.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError):
        return {}


def load_weekly_top30_tracker() -> dict:
    try:
        payload = json.loads(WEEKLY_TOP30_TRACKER_PATH.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError):
        return {}


def update_weekly_top30_tracker(rows: list[dict], updated_at: str) -> dict:
    tracker = load_weekly_top30_tracker()
    snapshots = tracker.get("weeklySnapshots")
    if not isinstance(snapshots, dict):
        snapshots = {}

    update_day = datetime.fromisoformat(updated_at).date()
    week_start = update_day - timedelta(days=update_day.weekday())
    week_key = week_start.isoformat()
    issuers: dict[str, dict] = {}
    for row in rows:
        code = str(row.get("issuerCode") or "").strip()
        weekly_change = number(row.get("stockWeeklyChangePct"))
        if not code or weekly_change is None:
            continue
        item = issuers.get(code)
        if item is None or weekly_change > item["weeklyChangePct"]:
            issuers[code] = {
                "stockCode": code,
                "stockName": row.get("issuerName") or row.get("stockName") or code,
                "weeklyChangePct": weekly_change,
                "fineIndustryTags": row.get("fineIndustryTags") or [],
                "groupTags": row.get("groupTags") or [],
            }

    top30 = sorted(issuers.values(), key=lambda item: item["weeklyChangePct"], reverse=True)[:30]
    snapshots[week_key] = {
        "updatedAt": updated_at,
        "top30": [
            {
                **item,
                "rank": index + 1,
            }
            for index, item in enumerate(top30)
        ],
    }

    counts: dict[str, int] = {}
    last_hit_week: dict[str, date] = {}
    names: dict[str, str] = {}
    latest_item: dict[str, dict] = {}
    for key in sorted(snapshots):
        try:
            current_week = date.fromisoformat(key)
        except ValueError:
            continue
        for code, last_week in list(last_hit_week.items()):
            if (current_week - last_week).days >= 30:
                counts[code] = 0
                del last_hit_week[code]
        for item in snapshots.get(key, {}).get("top30", []):
            code = str(item.get("stockCode") or "")
            if not code:
                continue
            counts[code] = counts.get(code, 0) + 1
            last_hit_week[code] = current_week
            names[code] = item.get("stockName") or code
            latest_item[code] = item

    for code, last_week in list(last_hit_week.items()):
        if (week_start - last_week).days >= 30:
            counts[code] = 0
            del last_hit_week[code]

    ranking = []
    for code, count in counts.items():
        if count <= 0:
            continue
        item = latest_item.get(code, {})
        ranking.append({
            "stockCode": code,
            "stockName": names.get(code) or item.get("stockName") or code,
            "count": count,
            "lastEnteredWeek": last_hit_week.get(code).isoformat() if code in last_hit_week else None,
            "latestWeeklyChangePct": item.get("weeklyChangePct"),
            "fineIndustryTags": item.get("fineIndustryTags") or [],
            "groupTags": item.get("groupTags") or [],
        })
    ranking.sort(key=lambda item: (item["count"], item.get("latestWeeklyChangePct") or -999), reverse=True)
    for index, item in enumerate(ranking):
        item["rank"] = index + 1

    tracker = {
        "updatedAt": updated_at,
        "definition": "每週以股票收盤價本週漲幅取前 30 名；同一週快照覆寫且只計一次；連續 30 天未再入榜則累積次數歸零。",
        "currentWeek": week_key,
        "weeklySnapshots": snapshots,
        "ranking": ranking,
    }
    WEEKLY_TOP30_TRACKER_PATH.parent.mkdir(parents=True, exist_ok=True)
    WEEKLY_TOP30_TRACKER_PATH.write_text(json.dumps(tracker, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rank_by_code = {item["stockCode"]: item for item in ranking}
    current_top_by_code = {item["stockCode"]: item for item in snapshots[week_key]["top30"]}
    for row in rows:
        code = str(row.get("issuerCode") or "").strip()
        row["weeklyTop30Rank"] = current_top_by_code.get(code, {}).get("rank")
        row["weeklyTop30Count"] = rank_by_code.get(code, {}).get("count", 0)
    return tracker


def load_cached_history(code: str) -> dict:
    candidates = []
    json_path = HISTORY_DIR / f"{code}.json"
    try:
        candidates.append(json.loads(json_path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass

    script_path = HISTORY_DIR / f"{code}.js"
    try:
        match = re.search(r"\]\s*=\s*(\{.*\});\s*$", script_path.read_text(encoding="utf-8"), re.DOTALL)
        if match:
            candidates.append(json.loads(match.group(1)))
    except (OSError, ValueError):
        pass

    valid = [
        payload for payload in candidates
        if payload.get("dataKind") == "actual-market-ohlcv"
        and isinstance(payload.get("points"), list)
    ]
    return max(valid, key=lambda payload: (bool(payload.get("complete")), len(payload["points"])), default={})


def has_consistent_ohlc(point: dict) -> bool:
    open_price = number(point.get("open"))
    high = number(point.get("high"))
    low = number(point.get("low"))
    close = number(point.get("close"))
    if close is None:
        return False
    if None in (open_price, high, low):
        return True
    return low <= min(open_price, close) and high >= max(open_price, close) and high >= low


def merge_history_points(*series: list[dict] | None) -> list[dict]:
    merged = {}
    for points in series:
        for point in points or []:
            if not isinstance(point, dict) or not point.get("date") or not has_consistent_ohlc(point):
                continue
            previous = merged.get(point["date"], {})
            merged[point["date"]] = {
                "date": point["date"],
                "open": point.get("open") if point.get("open") is not None else previous.get("open"),
                "high": point.get("high") if point.get("high") is not None else previous.get("high"),
                "low": point.get("low") if point.get("low") is not None else previous.get("low"),
                "close": point.get("close"),
                "volume": point.get("volume") if point.get("volume") is not None else previous.get("volume"),
            }
    return sorted(merged.values(), key=lambda point: point["date"])


def write_history_payload(code: str, payload: dict) -> None:
    compact = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    (HISTORY_DIR / f"{code}.json").write_text(compact, encoding="utf-8")
    script = (
        "window.CB_HISTORY_DATA = window.CB_HISTORY_DATA || {};\n"
        f'window.CB_HISTORY_DATA[{json.dumps(code)}] = {compact};\n'
    )
    (HISTORY_DIR / f"{code}.js").write_text(script, encoding="utf-8")


def iso_date(value: str | None) -> str | None:
    text = str(value or "").strip()
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return None


def parse_iso_date(value) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def is_recent_or_upcoming_preserved_row(row: dict) -> bool:
    status_text = " ".join(
        str(row.get(key) or "")
        for key in ("sourceType", "source", "status", "primaryMarketStatus", "validationStatus")
    ).lower()
    if any(
        marker in status_text
        for marker in ("upcoming", "bookbuilding_auction", "filing", "board_approved", "pending")
    ):
        return True
    if not (row.get("listingDate") or row.get("listedDateROC")):
        return True
    row_date = parse_iso_date(
        row.get("issueDate")
        or row.get("listingDate")
        or row.get("listedDateROC")
        or row.get("announcementDate")
    )
    if not row_date:
        return False
    today = datetime.now(TZ).date()
    return today - timedelta(days=90) <= row_date <= today + timedelta(days=90)


def financial_quarter_label(row: dict) -> str:
    year_text = str(
        row.get("Year")
        or row.get("年度")
        or row.get("年")
        or row.get("資料年度")
        or ""
    ).strip()
    quarter_text = str(
        row.get("季別")
        or row.get("Quarter")
        or row.get("季度")
        or row.get("資料季別")
        or ""
    ).strip()
    if not year_text:
        return ""
    try:
        year = int(float(year_text))
        if year < 1911:
            year += 1911
    except ValueError:
        return ""
    quarter_digits = re.sub(r"\D", "", quarter_text)
    return f"{year}Q{quarter_digits}" if quarter_digits else str(year)


def sync_active_rows(data: dict) -> list[dict]:
    issues = fetch_json("https://www.tpex.org.tw/openapi/v1/bond_ISSBD5_data")
    try:
        twse = fetch_json("https://openapi.twse.com.tw/v1/opendata/t187ap03_L")
    except Exception:
        twse = []
    try:
        tpex = fetch_json("https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O")
    except Exception:
        tpex = []
    try:
        twse_industry = fetch_json("https://openapi.twse.com.tw/v1/opendata/t187ap14_L")
    except Exception:
        twse_industry = []
    try:
        tpex_industry = fetch_json("https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap14_O")
    except Exception:
        tpex_industry = []
    revenue_rows = []
    for url in (
        "https://openapi.twse.com.tw/v1/opendata/t187ap05_L",
        "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O",
    ):
        try:
            revenue_rows += fetch_json(url)
        except Exception:
            pass
    margin_rows = []
    for url in (
        "https://openapi.twse.com.tw/v1/opendata/t187ap17_L",
        "https://www.tpex.org.tw/openapi/v1/mopsfin_187ap17_O",
    ):
        try:
            margin_rows += fetch_json(url)
        except Exception:
            pass

    existing = {str(row.get("bondCode")): row for row in data.get("rows", [])}
    twse_company = {str(row.get("公司代號")): row for row in twse}
    tpex_company = {str(row.get("SecuritiesCompanyCode")): row for row in tpex}
    industries = {
        **{str(row.get("公司代號")): row.get("產業別") for row in twse_industry},
        **{str(row.get("SecuritiesCompanyCode")): row.get("產業別") for row in tpex_industry},
    }
    revenues = {
        str(row.get("公司代號") or row.get("SecuritiesCompanyCode")): number(
            row.get("營業收入-去年同月增減(%)")
        )
        for row in revenue_rows
    }
    revenue_mom = {
        str(row.get("公司代號") or row.get("SecuritiesCompanyCode")): number(
            row.get("營業收入-上月比較增減(%)")
        )
        for row in revenue_rows
    }
    revenue_periods = {
        str(row.get("公司代號") or row.get("SecuritiesCompanyCode")): (
            row.get("資料年月")
            or row.get("DataYearMonth")
            or row.get("YearMonth")
            or row.get("年月")
            or row.get("出表日期")
        )
        for row in revenue_rows
    }
    margins = {
        str(row.get("公司代號") or row.get("SecuritiesCompanyCode")): number(
            row.get("毛利率(%)(營業毛利)/(營業收入)") or row.get("毛利率")
        )
        for row in margin_rows
    }
    margin_periods = {
        str(row.get("公司代號") or row.get("SecuritiesCompanyCode")): financial_quarter_label(row)
        for row in margin_rows
    }

    active_rows = []
    active_codes: set[str] = set()
    for item in issues:
        bond_code = str(item.get("BondCode") or "").strip()
        issue_amount = number(item.get("IssueAmount"))
        outstanding = number(item.get("OutstandingAmount"))
        if not bond_code or item.get("ListingStatus") != "2" or not item.get("ListingDate"):
            continue
        if outstanding is None or outstanding <= 0:
            continue

        issuer_code = str(item.get("IssuerCode") or "").strip()
        previous = existing.get(bond_code, {})
        market = "TWSE" if issuer_code in twse_company else "OTC"
        company = twse_company.get(issuer_code, {}).get("公司名稱")
        company = company or tpex_company.get(issuer_code, {}).get("CompanyName")
        conversion_start = iso_date(item.get("Conversion/ExchangePeriodStartDate"))
        conversion_end = iso_date(item.get("Conversion/ExchangePeriodEndDate"))

        row = dict(previous)
        previous_margin_record = number(previous.get("grossMarginRecord"))
        revenue_yoy = revenues.get(issuer_code) if issuer_code in revenues else previous.get("revenueYoY")
        revenue_mom_value = revenue_mom.get(issuer_code) if issuer_code in revenue_mom else previous.get("revenueMoM")
        revenue_period = revenue_periods.get(issuer_code) if issuer_code in revenue_periods else previous.get("revenueYearMonth")
        gross_margin = margins.get(issuer_code) if issuer_code in margins else previous.get("grossMargin")
        margin_period = margin_periods.get(issuer_code) if issuer_code in margin_periods else previous.get("grossMarginQuarter")
        row.update(
            {
                "issuerCode": issuer_code,
                "issuerName": company or previous.get("issuerName") or item.get("IssuerName"),
                "bondName": previous.get("bondName") or item.get("ShortName"),
                "bondCode": bond_code,
                "bondShortName": item.get("ShortName") or previous.get("bondShortName"),
                "issueDate": iso_date(item.get("IssueDate")),
                "maturityDate": iso_date(item.get("MaturityDate")),
                "listingDate": iso_date(item.get("ListingDate")),
                "issueAmount": issue_amount,
                "remainingAmount": outstanding,
                "convertedPct": (
                    max(0, (1 - outstanding / issue_amount) * 100)
                    if issue_amount not in (None, 0)
                    else None
                ),
                "conversionWindow": (
                    f"{conversion_start}～{conversion_end}"
                    if conversion_start and conversion_end
                    else previous.get("conversionWindow")
                ),
                "conversionPrice": previous.get("conversionPrice")
                or number(item.get("Conversion/ExchangePriceAtIssuance")),
                "stockMarket": market,
                "industryCategory": industries.get(issuer_code)
                or previous.get("industryCategory")
                or "-",
                "revenueYoY": revenue_yoy,
                "revenueMoM": revenue_mom_value,
                "revenueYearMonth": revenue_period,
                "revenuePeriod": revenue_period,
                "grossMargin": gross_margin,
                "grossMarginQuarter": margin_period,
                "grossMarginPeriod": margin_period,
                "grossMarginIsRecordHigh": (
                    gross_margin is not None
                    and previous_margin_record is not None
                    and gross_margin >= previous_margin_record
                ),
                "grossMarginRecord": (
                    max(gross_margin, previous_margin_record)
                    if gross_margin is not None and previous_margin_record is not None
                    else gross_margin if gross_margin is not None else previous_margin_record
                ),
                "listingStatus": item.get("ListingStatus"),
                "officialDataDate": iso_date(item.get("Date")),
            }
        )
        active_rows.append(row)
        active_codes.add(bond_code)

    preserved_rows = []
    for bond_code, previous in existing.items():
        if not bond_code or bond_code in active_codes:
            continue
        if is_recent_or_upcoming_preserved_row(previous):
            preserved_rows.append(previous)

    rows = active_rows + preserved_rows
    rows.sort(key=lambda row: row.get("issueDate") or row.get("listingDate") or "", reverse=True)
    data["officialDataDate"] = iso_date(issues[0].get("Date")) if issues else None
    data["scope"] = "All listed convertible bonds with outstanding balance"
    return rows


def fetch_mis_channels(channels: list[str]) -> dict[str, dict]:
    output = {}
    for index in range(0, len(channels), 70):
        encoded = quote("|".join(channels[index : index + 70]))
        url = f"https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch={encoded}&json=1&delay=0"
        payload = fetch_json(url)
        output.update(
            {item.get("c"): item for item in payload.get("msgArray", []) if item.get("c")}
        )
    return output


def fetch_mis_quotes(codes: list[str]) -> dict[str, dict]:
    return fetch_mis_channels([f"otc_{code}.tw" for code in codes])


def fetch_mis_stock_quotes(rows: list[dict]) -> dict[str, dict]:
    unique = {}
    for row in rows:
        code = str(row.get("issuerCode", "")).strip()
        if code:
            unique[code] = row.get("stockMarket")
    channels = [
        f"{'tse' if market == 'TWSE' else 'otc'}_{code}.tw"
        for code, market in unique.items()
    ]
    return fetch_mis_channels(channels)


def fetch_tpex_daily_quotes() -> tuple[str | None, dict[str, dict]]:
    today = datetime.now(TZ).date()
    for days_back in range(8):
        trade_day = today - timedelta(days=days_back)
        stamp = trade_day.strftime("%Y%m%d")
        url = (
            "https://www.tpex.org.tw/storage/bond_zone/tradeinfo/cb/"
            f"{trade_day:%Y}/{trade_day:%Y%m}/RSta0113.{stamp}-C.csv"
        )
        try:
            request = Request(url, headers=HEADERS)
            with urlopen(request, timeout=30) as response:
                text = response.read().decode("cp950")
        except Exception:
            continue
        quotes = {}
        for item in csv.reader(io.StringIO(text)):
            if len(item) < 12 or item[0] != "BODY" or not item[1].strip():
                continue
            close = number(item[4])
            if close is None:
                continue
            change = number(item[5])
            quotes[item[1].strip()] = {
                "date": trade_day.isoformat(),
                "close": close,
                "previous": close - change if change is not None else None,
                "open": number(item[6]),
                "high": number(item[7]),
                "low": number(item[8]),
                "volume": number(item[10]),
            }
        if quotes:
            return stamp, quotes
    return None, {}


def fetch_yahoo_history(
    symbol: str,
    current_date: str | None = None,
    start_date: str | None = None,
    include_series: bool = False,
) -> tuple[float | None, float | None, float | None, str | None, int, list[dict], float | None]:
    period2 = int(datetime.now(TZ).timestamp()) + 86400
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?period1=0&period2={period2}&interval=1d&events=history"
    try:
        payload = fetch_json(url)
        result = (payload.get("chart", {}).get("result") or [None])[0] or {}
        meta = result.get("meta", {})
        timestamps = result.get("timestamp", [])
        quote_data = result.get("indicators", {}).get("quote", [{}])[0]
        closes = quote_data.get("close", [])
        opens = quote_data.get("open", [])
        highs = quote_data.get("high", [])
        lows = quote_data.get("low", [])
        volumes = quote_data.get("volume", [])
        first_day = datetime.strptime(start_date, "%Y-%m-%d").date() if start_date else None
        points = []
        for index, timestamp in enumerate(timestamps):
            close = number(closes[index]) if index < len(closes) else None
            day = datetime.fromtimestamp(timestamp, TZ).date()
            if close is None or (first_day is not None and day < first_day):
                continue
            points.append(
                {
                    "date": day.isoformat(),
                    "open": number(opens[index]) if index < len(opens) else None,
                    "high": number(highs[index]) if index < len(highs) else None,
                    "low": number(lows[index]) if index < len(lows) else None,
                    "close": close,
                    "volume": number(volumes[index]) if index < len(volumes) else None,
                }
            )
        target_day = None
        if current_date and len(current_date) == 8 and current_date.isdigit():
            target_day = datetime.strptime(current_date, "%Y%m%d").date()
        elif meta.get("regularMarketTime"):
            target_day = datetime.fromtimestamp(meta["regularMarketTime"], TZ).date()
        elif points:
            target_day = datetime.strptime(points[-1]["date"], "%Y-%m-%d").date()
        prior_closes = [
            point["close"]
            for point in points
            for day in [datetime.strptime(point["date"], "%Y-%m-%d").date()]
            if (target_day is None or day < target_day)
        ]
        weekly_change = None
        if points:
            latest_day = datetime.strptime(points[-1]["date"], "%Y-%m-%d").date()
            week_start = latest_day - timedelta(days=latest_day.weekday())
            previous_week_points = [
                point for point in points
                if datetime.strptime(point["date"], "%Y-%m-%d").date() < week_start
            ]
            if previous_week_points and previous_week_points[-1]["close"] not in (None, 0):
                weekly_change = (points[-1]["close"] / previous_week_points[-1]["close"] - 1) * 100
        latest_close = points[-1]["close"] if points else None
        previous_close = points[-2]["close"] if len(points) >= 2 else None
        return (
            latest_close,
            previous_close,
            max(prior_closes) if prior_closes else None,
            points[0]["date"] if points else None,
            len(prior_closes),
            points if include_series else [],
            weekly_change,
        )
    except Exception:
        return None, None, None, None, 0, [], None


def parse_tpex_date(value: str) -> str | None:
    parts = str(value or "").strip().split("/")
    if len(parts) != 3:
        return None
    try:
        year, month, day = (int(part) for part in parts)
        if year < 1911:
            year += 1911
        return datetime(year, month, day).date().isoformat()
    except ValueError:
        return None


def month_starts(start_date: str, end_date: date):
    current = datetime.strptime(start_date, "%Y-%m-%d").date().replace(day=1)
    while current <= end_date.replace(day=1):
        yield current
        current = (current.replace(day=28) + timedelta(days=4)).replace(day=1)


def summarize_history(points: list[dict], current_date: str | None = None):
    points = sorted(points, key=lambda point: point["date"])
    target_day = None
    if current_date and len(current_date) == 8 and current_date.isdigit():
        target_day = datetime.strptime(current_date, "%Y%m%d").date()
    elif points:
        target_day = datetime.strptime(points[-1]["date"], "%Y-%m-%d").date()
    prior_closes = [
        point["close"]
        for point in points
        if target_day is None
        or datetime.strptime(point["date"], "%Y-%m-%d").date() < target_day
    ]
    latest = points[-1]["close"] if points else None
    previous = prior_closes[-1] if prior_closes else None
    return (
        latest,
        previous,
        max(prior_closes) if prior_closes else None,
        points[0]["date"] if points else None,
        len(prior_closes),
        points,
    )


def fetch_tpex_history(
    code: str,
    start_date: str | None,
    current_date: str | None = None,
    cached_points: list[dict] | None = None,
) -> tuple[float | None, float | None, float | None, str | None, int, list[dict]]:
    if not start_date:
        return None, None, None, None, 0, []
    first_day = datetime.strptime(start_date, "%Y-%m-%d").date()
    today = datetime.now(TZ).date()
    if first_day > today:
        return None, None, None, None, 0, []

    points_by_date = {
        point["date"]: point
        for point in (cached_points or [])
        if isinstance(point, dict) and point.get("date") and number(point.get("close")) is not None
    }
    fetch_start = max(points_by_date)[:7] + "-01" if points_by_date else start_date
    for month in month_starts(fetch_start, today):
        date_value = month.strftime("%Y/%m/01")
        url = (
            "https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingInfo"
            f"?code={quote(code)}&date={quote(date_value)}&id=&response=json"
        )
        try:
            payload = fetch_json(url)
        except Exception:
            break
        tables = payload.get("tables") or []
        rows = tables[0].get("data", []) if tables else []
        for item in rows:
            if not isinstance(item, list) or len(item) < 7:
                continue
            day = parse_tpex_date(item[0])
            close = number(item[6])
            if not day or day < start_date or close is None:
                continue
            points_by_date[day] = {
                "date": day,
                "open": number(item[3]),
                "high": number(item[4]),
                "low": number(item[5]),
                "close": close,
                "volume": number(item[1]),
            }
    return summarize_history(list(points_by_date.values()), current_date)


def main() -> int:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    data = load_data()
    try:
        rows = sync_active_rows(data)
        data.pop("issuanceSyncWarning", None)
    except Exception as error:
        rows = data.get("rows", [])
        data["issuanceSyncWarning"] = f"Using cached active list: {type(error).__name__}"
    stock_tags = load_stock_tags()
    issuance_purposes = load_issuance_purposes()
    redemption_alerts = load_redemption_alerts()
    for row in rows:
        tag = stock_tags.get(str(row.get("issuerCode") or ""), {})
        row["fineIndustryTags"] = tag.get("fineIndustries", [])
        row["productTags"] = tag.get("productTags", [])
        row["themeTags"] = tag.get("themeTags", [])
        row["groupTags"] = tag.get("groupTags", [])
        row["tagConfidence"] = tag.get("confidence")
        row["tagSource"] = tag.get("source")
        row["tagUpdatedAt"] = tag.get("updatedAt")
        purpose = issuance_purposes.get(str(row.get("bondCode") or "").strip(), {})
        row["issuancePurpose"] = purpose.get("summary") or "公開資料未整理"
        row["issuancePurposeTags"] = purpose.get("purposes") if isinstance(purpose.get("purposes"), list) else []
        row["issuancePurposeSource"] = purpose.get("source") or "pending"
        row["issuancePurposeUpdatedAt"] = purpose.get("updatedAt")
        redemption = redemption_alerts.get(str(row.get("bondCode") or "").strip(), {})
        row["redemptionStatus"] = redemption.get("status") or "normal"
        row["redemptionAlertLevel"] = redemption.get("alertLevel") or ""
        row["redemptionSummary"] = redemption.get("summary") or ""
        row["redemptionStartDate"] = redemption.get("redemptionStartDate") or ""
        row["redemptionEndDate"] = redemption.get("redemptionEndDate") or ""
        row["redemptionDelistDate"] = redemption.get("delistDate") or ""
        row["redemptionSourceUrl"] = redemption.get("sourceUrl") or ""
    data["rows"] = rows
    codes = [str(row.get("bondCode", "")).strip() for row in rows if row.get("bondCode")]
    try:
        mis = fetch_mis_quotes(codes)
    except Exception:
        mis = {}
    try:
        stock_mis = fetch_mis_stock_quotes(rows)
    except Exception:
        stock_mis = {}
    try:
        tpex_quote_date, tpex_daily = fetch_tpex_daily_quotes()
    except Exception:
        tpex_quote_date, tpex_daily = None, {}
    updated_at = datetime.now(TZ).isoformat()
    updated = 0

    stock_quotes = {}
    for row in rows:
        issuer_code = str(row.get("issuerCode", "")).strip()
        if not issuer_code or issuer_code in stock_quotes:
            continue
        suffix = ".TW" if row.get("stockMarket") == "TWSE" else ".TWO"
        stock_row = stock_mis.get(issuer_code, {})
        stock_quotes[issuer_code] = fetch_yahoo_history(
            issuer_code + suffix,
            stock_row.get("d") if number(stock_row.get("z")) is not None else None,
        )

    for row in rows:
        code = str(row.get("bondCode", "")).strip()
        if not code:
            continue
        mis_row = mis.get(code, {})
        mis_close = number(mis_row.get("z"))
        yahoo_close, yahoo_prev, cb_history_high, cb_history_start, cb_history_count, cb_history_points, _ = fetch_yahoo_history(
            code + ".TWO",
            mis_row.get("d") if mis_close is not None else None,
            row.get("listingDate") or row.get("issueDate"),
            include_series=True,
        )
        history_source = "Yahoo Finance Taipei Exchange daily OHLCV"
        history_period2 = int(datetime.now(TZ).timestamp()) + 86400
        history_source_url = f"https://query1.finance.yahoo.com/v8/finance/chart/{code}.TWO?period1=0&period2={history_period2}&interval=1d&events=history"
        cached_payload = load_cached_history(code)
        fresh_history_points = cb_history_points
        cb_history_points = merge_history_points(cached_payload.get("points"), cb_history_points)
        listing_date = row.get("listingDate") or row.get("issueDate")
        yahoo_history_complete = (
            len(fresh_history_points) >= 2
            and listing_date
            and datetime.strptime(fresh_history_points[0]["date"], "%Y-%m-%d").date()
            <= datetime.strptime(listing_date, "%Y-%m-%d").date() + timedelta(days=7)
        )
        if not yahoo_history_complete:
            yahoo_close, yahoo_prev, cb_history_high, cb_history_start, cb_history_count, cb_history_points = fetch_tpex_history(
                code,
                listing_date,
                mis_row.get("d") if mis_close is not None else None,
                cb_history_points,
            )
            history_source = "TPEx official individual security daily trading information"
            history_source_url = "https://www.tpex.org.tw/zh-tw/afterTrading/tradingInfo.html"
        else:
            yahoo_close, yahoo_prev, cb_history_high, cb_history_start, cb_history_count, cb_history_points = summarize_history(
                cb_history_points,
                mis_row.get("d") if mis_close is not None else None,
            )
            if str(cached_payload.get("source", "")).startswith("TPEx"):
                history_source = "Yahoo Finance daily OHLCV + verified TPEx cache"
                history_source_url = "https://www.tpex.org.tw/zh-tw/bond/info/statistics-cb/day.html"
        official_quote = tpex_daily.get(code)
        if official_quote:
            cb_history_points = merge_history_points(cb_history_points, [official_quote])
            yahoo_close, yahoo_prev, cb_history_high, cb_history_start, cb_history_count, cb_history_points = summarize_history(
                cb_history_points, tpex_quote_date
            )
            history_source = "TPEx official daily convertible bond trading report"
            history_source_url = "https://www.tpex.org.tw/zh-tw/bond/info/statistics-cb/day.html"
        close = official_quote["close"] if official_quote else (mis_close if mis_close is not None else yahoo_close)
        previous = official_quote.get("previous") if official_quote else (
            number(mis_row.get("y")) if number(mis_row.get("y")) is not None else yahoo_prev
        )

        issuer_code = str(row.get("issuerCode", "")).strip()
        stock_close, stock_previous, stock_history_high, stock_history_start, stock_history_count, _, stock_weekly_change = stock_quotes.get(
            issuer_code, (None, None, None, None, 0, [], None)
        )
        stock_close = number(stock_mis.get(issuer_code, {}).get("z")) or stock_close
        stock_previous = number(stock_mis.get(issuer_code, {}).get("y")) or stock_previous
        if stock_close is not None:
            row["stockClose"] = stock_close
            row["stockPrevClose"] = stock_previous
            row["stockChangePct"] = (
                (stock_close / stock_previous - 1) * 100
                if stock_previous not in (None, 0)
                else None
            )
            row["stockHistoryHigh"] = stock_history_high
            row["stockHistoryStart"] = stock_history_start
            row["stockHistoryCount"] = stock_history_count
            row["stockHistoryValid"] = bool(stock_history_start and stock_history_count >= 2 and stock_history_high is not None)
            row["stockWeeklyChangePct"] = stock_weekly_change
            stock_close_units = price_units(stock_close)
            stock_high_units = price_units(stock_history_high)
            row["stockIsRecordHigh"] = (
                row["stockHistoryValid"]
                and stock_close_units is not None
                and stock_high_units is not None
                and stock_close_units >= stock_high_units
            )

        latest_history_date = cb_history_points[-1]["date"].replace("-", "") if cb_history_points else None
        cb_quote_date = tpex_quote_date if official_quote else (
            mis_row.get("d") if mis_close is not None else latest_history_date
        )
        cb_points_for_change = sorted(
            [point for point in cb_history_points if number(point.get("close")) is not None],
            key=lambda point: point["date"],
        )
        cb_latest_point = cb_points_for_change[-1] if cb_points_for_change else None
        cb_prev_point = cb_points_for_change[-2] if len(cb_points_for_change) >= 2 else None
        cb_prev_trade_date = cb_prev_point.get("date") if cb_prev_point else None
        cb_latest_trade_date = cb_latest_point.get("date") if cb_latest_point else None
        cb_days_from_prev_trade = (
            (
                datetime.strptime(cb_latest_trade_date, "%Y-%m-%d").date()
                - datetime.strptime(cb_prev_trade_date, "%Y-%m-%d").date()
            ).days
            if cb_latest_trade_date and cb_prev_trade_date
            else None
        )
        cb_latest_incomplete = bool(
            cb_latest_point
            and any(cb_latest_point.get(key) is None for key in ("open", "high", "low", "volume"))
        )
        cb_non_daily_change = bool(
            cb_days_from_prev_trade is not None
            and (cb_days_from_prev_trade > 4 or cb_latest_incomplete)
        )

        row["cbClose"] = close
        row["cbPrevClose"] = previous
        row["cbChangePct"] = (
            (close / previous - 1) * 100 if close is not None and previous not in (None, 0) else None
        )
        row["cbQuoteDate"] = cb_quote_date
        row["cbPrevTradeDate"] = cb_prev_trade_date
        row["cbLatestTradeDate"] = cb_latest_trade_date
        row["cbDaysFromPrevTrade"] = cb_days_from_prev_trade
        row["cbChangeBasis"] = "previous_trade" if cb_non_daily_change else "daily"
        row["cbUpdatedAt"] = updated_at
        row["cbHistoryHigh"] = cb_history_high
        row["cbHistoryStart"] = cb_history_start
        row["cbHistoryCount"] = cb_history_count
        close_units = price_units(close)
        previous_units = price_units(previous)
        history_high_units = price_units(cb_history_high)
        listing_day = datetime.strptime(
            row.get("listingDate") or row.get("issueDate"), "%Y-%m-%d"
        ).date()
        history_start_day = (
            datetime.strptime(cb_history_start, "%Y-%m-%d").date()
            if cb_history_start
            else None
        )
        history_valid = (
            history_start_day is not None
            and history_start_day <= listing_day + timedelta(days=7)
            and cb_history_count >= 2
            and previous_units is not None
            and history_high_units is not None
            and history_high_units >= previous_units
        )
        row["cbHistoryValid"] = history_valid
        is_record_high = (
            history_valid
            and cb_quote_date == datetime.now(TZ).strftime("%Y%m%d")
            and close_units is not None
            and close_units > history_high_units
        )
        row["cbIsRecordHigh"] = is_record_high
        row["cbHighAuditStatus"] = (
            "record_high"
            if is_record_high
            else "not_record_high" if history_valid else "history_incomplete"
        )
        if cb_history_points:
            history_payload = {
                "bondCode": code,
                "bondName": row.get("bondShortName"),
                "issueDate": row.get("issueDate"),
                "updatedAt": updated_at,
                "complete": history_valid,
                "dataKind": "actual-market-ohlcv",
                "interval": "1d",
                "timezone": "Asia/Taipei",
                "source": history_source,
                "sourceUrl": history_source_url,
                "points": cb_history_points,
            }
            write_history_payload(code, history_payload)
        conversion_price = number(row.get("conversionPrice"))
        conversion_value = (
            number(row.get("stockClose")) / conversion_price * 100
            if number(row.get("stockClose")) is not None and conversion_price not in (None, 0)
            else None
        )
        row["conversionValue"] = conversion_value
        row["conversionPremium"] = (
            (close / conversion_value - 1) * 100
            if close is not None and conversion_value not in (None, 0)
            else None
        )
        if close is not None:
            updated += 1

    industry_changes = {}
    seen_issuers = set()
    for row in rows:
        issuer_code = str(row.get("issuerCode") or "")
        industry = row.get("industryCategory") or "-"
        weekly_change = number(row.get("stockWeeklyChangePct"))
        key = (industry, issuer_code)
        if weekly_change is None or key in seen_issuers:
            continue
        seen_issuers.add(key)
        industry_changes.setdefault(industry, []).append(weekly_change)
    industry_averages = {
        industry: sum(values) / len(values)
        for industry, values in industry_changes.items()
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

    weekly_top30_tracker = update_weekly_top30_tracker(rows, updated_at)

    data["stockHistorySource"] = "Yahoo Finance Taiwan listed/OTC daily close history"
    data["stockWeeklyChangeDefinition"] = "Latest close versus the final trading close before the current week"
    data["weeklyStockTop30Tracker"] = {
        "updatedAt": weekly_top30_tracker.get("updatedAt"),
        "definition": weekly_top30_tracker.get("definition"),
        "currentWeek": weekly_top30_tracker.get("currentWeek"),
        "ranking": weekly_top30_tracker.get("ranking", []),
        "currentTop30": weekly_top30_tracker.get("weeklySnapshots", {}).get(weekly_top30_tracker.get("currentWeek"), {}).get("top30", []),
    }
    data["cbQuotesUpdatedAt"] = updated_at
    data["cbQuoteSource"] = "TPEx official daily report / TWSE MIS / Yahoo Finance"
    data["fetchedAt"] = updated_at
    data["source"] = (
        "TPEx active convertible bond issuance + MOPS details + TWSE MIS/Yahoo market quotes "
        "+ TWSE/TPEx industry, monthly revenue and quarterly margin data"
    )
    DATA_PATH.write_text(PREFIX + json.dumps(data, ensure_ascii=False, indent=2) + ";\n", encoding="utf-8")
    print(f"Updated {updated}/{len(rows)} CB quotes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
