import concurrent.futures
import html
import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "outputs"
PUBLIC_DIR = ROOT / "public"
DATA_FILE = OUTPUT_DIR / "recent-cb-data.js"
EPS_FILE = OUTPUT_DIR / "eps-forecast-data.js"
INSIGHTS_FILE = OUTPUT_DIR / "company-insights-data.js"
TZ = timezone(timedelta(hours=8))
CURRENT_YEAR = datetime.now(TZ).year
MOPS_PROFILE_URL = "https://mopsov.twse.com.tw/mops/web/ajax_t05st03"
MOPS_PROFILE_PAGE = "https://mopsov.twse.com.tw/mops/web/t05st03"


def load_js(path, prefix):
    text = path.read_text(encoding="utf-8").strip()
    return json.loads(text.removeprefix(prefix).rstrip(";"))


def write_js(path, prefix, payload):
    path.write_text(
        prefix + json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + ";\n",
        encoding="utf-8",
    )


def number(value):
    try:
        if value in (None, "", "-", "--"):
            return None
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def yahoo_auth():
    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0"
    session.get("https://fc.yahoo.com", timeout=20)
    crumb = session.get("https://query2.finance.yahoo.com/v1/test/getcrumb", timeout=20).text.strip()
    return session.cookies.get_dict(), crumb


def fetch_eps_forecast(item, cookies, crumb, old_forecast):
    issuer_code, company_name, market = item
    symbol = issuer_code + (".TW" if market == "TWSE" else ".TWO")
    url = f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{symbol}"
    payload = None
    for attempt in range(4):
        try:
            response = requests.get(
                url,
                params={"modules": "earningsTrend,financialData", "crumb": crumb},
                headers={"User-Agent": "Mozilla/5.0"},
                cookies=cookies,
                timeout=25,
            )
            if response.status_code == 404:
                break
            response.raise_for_status()
            payload = response.json()
            break
        except Exception:
            time.sleep(1.2 * (attempt + 1))

    trends = {}
    try:
        trend_rows = payload["quoteSummary"]["result"][0]["earningsTrend"]["trend"]
        trends = {row.get("period"): row for row in trend_rows}
    except (TypeError, KeyError, IndexError):
        pass

    try:
        financial_data = payload["quoteSummary"]["result"][0].get("financialData", {})
    except (TypeError, KeyError, IndexError):
        financial_data = {}

    output = {
        "issuerCode": issuer_code,
        "symbol": symbol,
        "updatedAt": datetime.now(TZ).isoformat(timespec="seconds"),
        "source": "Yahoo Finance 分析師 EPS 共識",
        "sourceUrl": f"https://finance.yahoo.com/quote/{symbol}/analysis/",
        "years": {},
        "targetPrice": {
            "mean": number((financial_data.get("targetMeanPrice") or {}).get("raw")),
            "median": number((financial_data.get("targetMedianPrice") or {}).get("raw")),
            "low": number((financial_data.get("targetLowPrice") or {}).get("raw")),
            "high": number((financial_data.get("targetHighPrice") or {}).get("raw")),
            "analysts": int(number((financial_data.get("numberOfAnalystOpinions") or {}).get("raw")) or 0),
        },
    }
    for offset, period in ((0, "0y"), (1, "+1y"), (2, "+2y")):
        trend = trends.get(period, {})
        estimate = trend.get("earningsEstimate", {})
        output["years"][str(CURRENT_YEAR + offset)] = {
            "eps": number((estimate.get("avg") or {}).get("raw")),
            "low": number((estimate.get("low") or {}).get("raw")),
            "high": number((estimate.get("high") or {}).get("raw")),
            "analysts": int(number((estimate.get("numberOfAnalysts") or {}).get("raw")) or 0),
        }

    current_trend = trends.get("0y", {})
    output["outlook"] = {
        "epsGrowth": number((current_trend.get("earningsEstimate", {}).get("growth") or {}).get("raw")),
        "revenueGrowth": number((current_trend.get("revenueEstimate", {}).get("growth") or {}).get("raw")),
        "revisionsUp30d": int(number((current_trend.get("epsRevisions", {}).get("upLast30days") or {}).get("raw")) or 0),
        "revisionsDown30d": int(number((current_trend.get("epsRevisions", {}).get("downLast30days") or {}).get("raw")) or 0),
    }
    output["news"] = []

    # Preserve manually collected analyst-report target prices; Yahoo does not provide target year attribution.
    old = old_forecast.get(issuer_code) or {}
    for key in ("analystReportTargetsByYear", "unclassifiedAnalystTargets", "analystReportTargetUpdatedAt"):
        if key in old:
            output[key] = old[key]
    return issuer_code, output


def fetch_primary_products_services(issuer_code):
    payload = {
        "step": "1",
        "firstin": "ture",
        "off": "1",
        "keyword4": "",
        "code1": "",
        "TYPEK2": "",
        "checkbtn": "",
        "queryName": "co_id",
        "inpuType": "co_id",
        "TYPEK": "all",
        "co_id": issuer_code,
    }
    headers = {"User-Agent": "Mozilla/5.0", "Referer": MOPS_PROFILE_PAGE}
    label = "\u4e3b\u8981\u7d93\u71df\u696d\u52d9"
    for attempt in range(4):
        try:
            response = requests.post(MOPS_PROFILE_URL, data=payload, headers=headers, timeout=25)
            response.raise_for_status()
            response.encoding = "utf-8"
            soup = BeautifulSoup(response.text, "html.parser")
            for th in soup.find_all("th"):
                if label in th.get_text("", strip=True):
                    td = th.find_next_sibling("td")
                    value = td.get_text("、", strip=True) if td else ""
                    value = html.unescape(value).replace("\xa0", " ")
                    value = re.sub(r"\s*、\s*", "、", value)
                    value = re.sub(r"\s+", " ", value).strip(" 、")
                    return issuer_code, value or None
        except Exception:
            pass
        time.sleep(0.9 * (attempt + 1))
    return issuer_code, None


def catalyst_text(forecast):
    outlook = forecast.get("outlook") or {}
    catalysts = []
    if outlook.get("epsGrowth") is not None:
        catalysts.append(f"法人共識預估 {CURRENT_YEAR} 年 EPS 年增 {outlook['epsGrowth'] * 100:.1f}%")
    if outlook.get("revenueGrowth") is not None:
        catalysts.append(f"法人共識預估 {CURRENT_YEAR} 年營收年增 {outlook['revenueGrowth'] * 100:.1f}%")
    up = int(outlook.get("revisionsUp30d") or 0)
    down = int(outlook.get("revisionsDown30d") or 0)
    if up or down:
        catalysts.append(f"近 30 日 EPS 預估上修 {up} 次、下修 {down} 次")
    return catalysts


def clean_conference_reasons(reasons):
    cleaned = []
    drop_terms = (
        "not responsible for alerting",
        "confidential",
        "taiwan (r.o.c.)",
        "county",
        "address",
    )
    keep_terms = (
        "%",
        "AI",
        "HPC",
        "5G",
        "EV",
        "growth",
        "capacity",
        "expansion",
        "demand",
        "market",
        "new product",
        "new customer",
        "CAGR",
    )
    for reason in reasons or []:
        text = re.sub(r"\s+", " ", str(reason)).strip()
        lower = text.lower()
        if not text or any(term in lower for term in drop_terms):
            continue
        if any(term.lower() in lower for term in keep_terms) or re.search(r"\d+(?:\.\d+)?\s*%", text):
            cleaned.append(text)
        if len(cleaned) >= 3:
            break
    return cleaned


def main():
    data = load_js(DATA_FILE, "window.RECENT_CB_DATA = ")
    old_eps = load_js(EPS_FILE, "window.CB_EPS_FORECASTS = ")
    old_insights = load_js(INSIGHTS_FILE, "window.CB_COMPANY_INSIGHTS = ")

    issuer_markets = {}
    for row in data.get("rows", []):
        code = str(row.get("issuerCode") or "").strip()
        if code and code not in issuer_markets:
            issuer_markets[code] = (row.get("issuerName") or row.get("issuerShortName") or "", row.get("market") or "TWSE")

    cookies, crumb = yahoo_auth()
    forecasts = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = [
            pool.submit(fetch_eps_forecast, (code, values[0], values[1]), cookies, crumb, old_eps)
            for code, values in issuer_markets.items()
        ]
        for index, future in enumerate(concurrent.futures.as_completed(futures), 1):
            code, forecast = future.result()
            forecasts[code] = forecast
            if index % 50 == 0:
                print(f"EPS {index}/{len(futures)}", flush=True)
    write_js(EPS_FILE, "window.CB_EPS_FORECASTS = ", forecasts)

    primary = {}
    failed_primary = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(fetch_primary_products_services, code) for code in issuer_markets]
        for index, future in enumerate(concurrent.futures.as_completed(futures), 1):
            code, value = future.result()
            if value:
                primary[code] = value
            else:
                failed_primary.append(code)
                old_value = (old_insights.get(code) or {}).get("primaryProductsServices")
                if old_value:
                    primary[code] = old_value
            if index % 50 == 0:
                print(f"MOPS {index}/{len(futures)}", flush=True)

    today = datetime.now(TZ).date().isoformat()
    insights = {}
    for code in issuer_markets:
        old = old_insights.get(code) or {}
        forecast = forecasts.get(code) or {}
        insights[code] = {
            "issuerCode": code,
            "bestMarginProduct": None,
            "bestMarginBasis": "公司公開資訊未揭露可核對的單一最高毛利產品；頁面不以未揭露作為展示內容",
            "bestMarginSourceUrl": None,
            "primaryProductsServices": primary.get(code),
            "primaryProductsBasis": "公開資訊觀測站公司基本資料：主要經營業務",
            "primaryProductsSourceUrl": MOPS_PROFILE_PAGE,
            "primaryProductsVerifiedAt": today if code not in failed_primary else old.get("primaryProductsVerifiedAt"),
            "conferenceGrowthReasons": clean_conference_reasons(old.get("conferenceGrowthReasons")),
            "conferenceDate": old.get("conferenceDate"),
            "conferenceSummary": old.get("conferenceSummary"),
            "conferenceFileName": old.get("conferenceFileName"),
            "conferenceSourceUrl": old.get("conferenceSourceUrl"),
            "catalysts": catalyst_text(forecast),
            "news": [],
            "verifiedAt": today,
            "source": forecast.get("source"),
        }
    write_js(INSIGHTS_FILE, "window.CB_COMPANY_INSIGHTS = ", insights)

    for name in ("eps-forecast-data.js", "company-insights-data.js"):
        source = OUTPUT_DIR / name
        target = PUBLIC_DIR / name
        if target.parent.exists():
            target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    eps_covered = sum(
        any((item.get("years", {}).get(str(CURRENT_YEAR + offset), {}) or {}).get("eps") is not None for offset in range(3))
        for item in forecasts.values()
    )
    topic_covered = sum(bool(item.get("catalysts") or item.get("conferenceGrowthReasons")) for item in insights.values())
    print(json.dumps({
        "issuers": len(issuer_markets),
        "epsCovered": eps_covered,
        "primaryCovered": sum(bool(insights[code].get("primaryProductsServices")) for code in issuer_markets),
        "topicCovered": topic_covered,
        "failedPrimary": failed_primary,
        "updatedAt": today,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
