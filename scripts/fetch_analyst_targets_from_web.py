from __future__ import annotations

import argparse
import base64
import csv
import html
import json
import re
import ssl
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path
from statistics import mean
from typing import Any

try:
    import certifi
except ImportError:  # pragma: no cover
    certifi = None


ROOT = Path(__file__).resolve().parents[1]
EPS_PATH = ROOT / "outputs" / "eps-forecast-data.js"
RECENT_PATH = ROOT / "outputs" / "recent-cb-data.js"
LOG_PATH = ROOT / "outputs" / "analyst-target-fetch-log.csv"
CURRENT_YEAR = date.today().year
TARGET_YEARS = [CURRENT_YEAR, CURRENT_YEAR + 1, CURRENT_YEAR + 2]
LOG_FIELDS = [
    "stockId",
    "companyName",
    "query",
    "sourceTitle",
    "sourceUrl",
    "foundTargetPrice",
    "foundEpsYear",
    "foundEps",
    "reason",
    "classifiedYear",
]


def load_js_object(path: Path, variable_name: str) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    match = re.search(rf"window\.{re.escape(variable_name)}\s*=\s*(\{{.*\}})\s*;?\s*$", text, re.S)
    if not match:
        raise ValueError(f"Cannot parse {path}")
    return json.loads(match.group(1))


def save_eps(data: dict[str, Any]) -> None:
    EPS_PATH.write_text(
        "window.CB_EPS_FORECASTS = "
        + json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        + ";\n",
        encoding="utf-8",
    )


def issuer_map() -> dict[str, str]:
    rows = load_js_object(RECENT_PATH, "RECENT_CB_DATA").get("rows", [])
    result: dict[str, str] = {}
    for row in rows:
        code = str(row.get("issuerCode") or "").strip()
        name = str(row.get("issuerName") or row.get("issuerShortName") or "").strip()
        if code and name and code not in result:
            result[code] = re.sub(r"(股份有限公司|有限公司)$", "", name)
    return result


def fetch_url(url: str, timeout: int = 8) -> tuple[str, str, str]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
        },
    )
    context = ssl.create_default_context(cafile=certifi.where()) if certifi and url.startswith("https://") else None
    with urllib.request.urlopen(req, timeout=timeout, context=context) as resp:
        content_type = resp.headers.get("Content-Type", "")
        final_url = resp.geturl()
        raw = resp.read(1_200_000)
    charset = "utf-8"
    charset_match = re.search(r"charset=([\w-]+)", content_type, re.I)
    if charset_match:
        charset = charset_match.group(1)
    return raw.decode(charset, errors="replace"), content_type, final_url


def html_to_text(source: str) -> str:
    source = re.sub(r"(?is)<(script|style).*?</\1>", " ", source)
    source = re.sub(r"(?is)<br\s*/?>", "\n", source)
    source = re.sub(r"(?is)</(p|div|tr|li|td|th|h\d)>", "\n", source)
    source = re.sub(r"(?is)<[^>]+>", " ", source)
    return re.sub(r"\s+", " ", html.unescape(source)).strip()


def decode_bing_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    encoded = (urllib.parse.parse_qs(parsed.query).get("u") or [""])[0]
    if encoded.startswith("a1"):
        encoded = encoded[2:]
    if not encoded:
        return url
    try:
        padded = encoded + "=" * (-len(encoded) % 4)
        decoded = base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")
        return decoded if decoded.startswith("http") else url
    except Exception:
        return url


def clean_url(url: str) -> str:
    url = html.unescape(url)
    if "bing.com/ck/" in url:
        url = decode_bing_url(url)
    return url


def blocked_url(url: str) -> bool:
    return any(part in url for part in ["r.bing.com", "th.bing.com", ".css", ".js", "microsoft.com", "go.microsoft.com"])


def search_urls(query: str, timeout: int = 8) -> list[str]:
    urls: list[str] = []
    rss_url = "https://www.bing.com/search?" + urllib.parse.urlencode({"q": query, "format": "rss"})
    try:
        body, _, _ = fetch_url(rss_url, timeout=timeout)
        root = ET.fromstring(body)
        for item in root.findall(".//item"):
            link = item.findtext("link") or ""
            if link.startswith("http") and not blocked_url(link) and link not in urls:
                urls.append(link)
    except Exception:
        pass

    html_url = "https://www.bing.com/search?" + urllib.parse.urlencode({"q": query})
    try:
        body, _, _ = fetch_url(html_url, timeout=timeout)
    except Exception:
        return urls[:10]
    for link in re.findall(r'href="(https?://[^"]+)"', body):
        url = clean_url(link)
        if url.startswith("http") and not blocked_url(url) and url not in urls:
            urls.append(url)
    return urls[:10]


def yahoo_search_urls(query: str) -> list[str]:
    url = "https://tw.stock.yahoo.com/news?" + urllib.parse.urlencode({"keyword": query})
    try:
        body, _, _ = fetch_url(url, timeout=8)
    except Exception:
        return []
    results: list[str] = []
    for match in re.findall(r'href="([^"]*/news/[^"]+\.html[^"]*)"', body):
        item = html.unescape(match)
        if item.startswith("/"):
            item = urllib.parse.urljoin("https://tw.stock.yahoo.com", item)
        if item.startswith("http") and item not in results:
            results.append(item)
    return results[:6]


def build_queries(stock_code: str, stock_name: str) -> list[str]:
    return [
        f"{stock_code} {stock_name} 目標價 EPS",
        f"{stock_code} {stock_name} 目標價 買進",
        f"{stock_code} {stock_name} 投顧 目標價",
        f"{stock_code} {stock_name} 券商 目標價",
        f"{stock_code} {stock_name} 法人 目標價",
        f"{stock_code} {stock_name} {CURRENT_YEAR} EPS 目標價",
        f"{stock_code} {stock_name} {CURRENT_YEAR + 1} EPS 目標價",
        f"{stock_code} {stock_name} 明年 EPS 目標價",
        f"{stock_code} {stock_name} 後年 EPS 目標價",
        f"{stock_name} 上調 目標價",
        f"{stock_name} 維持 買進 目標價",
        f"{stock_name} 目標價 上看",
    ]


def parse_report_date(text: str) -> str | None:
    for pattern in [r"(20\d{2})[/-](\d{1,2})[/-](\d{1,2})", r"(20\d{2})年(\d{1,2})月(\d{1,2})日"]:
        match = re.search(pattern, text)
        if match:
            year, month, day = match.groups()
            return f"{year}-{int(month):02d}-{int(day):02d}"
    return None


def parse_float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value.replace(",", ""))
    except Exception:
        return None


def infer_year(text: str, report_date: str | None) -> int | None:
    explicit = re.search(r"(20\d{2})\s*年?\s*(?:度|年)?\s*EPS", text, re.I)
    if explicit:
        year = int(explicit.group(1))
        return year if year in TARGET_YEARS else None
    base_year = int(report_date[:4]) if report_date else CURRENT_YEAR
    relative = [
        ("今年", 0),
        ("明年", 1),
        ("後年", 2),
    ]
    for label, offset in relative:
        if re.search(rf"{label}\s*EPS|{label}.*?每股盈餘", text, re.I):
            year = base_year + offset
            return year if year in TARGET_YEARS else None
    return None


def source_name_from_url(url: str) -> str:
    host = urllib.parse.urlparse(url).netloc.lower()
    if "moneydj" in host:
        return "MoneyDJ"
    if "cnyes" in host:
        return "鉅亨網"
    if "yahoo" in host:
        return "Yahoo奇摩股市"
    if "udn" in host:
        return "經濟日報"
    if "ctee" in host:
        return "工商時報"
    return host or "公開來源"


def extract_title(body: str, fallback: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", body, re.I | re.S)
    return html_to_text(match.group(1))[:120] if match else fallback


def extract_target_prices(text: str) -> list[tuple[float, int, int]]:
    patterns = [
        r"目標價由\s*[0-9,]+(?:\.\d+)?\s*元?\s*(?:調升|上調|調高|提高|升至|調整)?至\s*([0-9,]+(?:\.\d+)?)\s*元",
        r"目標價(?:上調至|調升至|調高至|提高至|升至|維持|為|達|至|看)?\s*([0-9,]+(?:\.\d+)?)\s*元",
        r"(?:上看|股價上看)\s*([0-9,]+(?:\.\d+)?)\s*元",
        r"([0-9,]+(?:\.\d+)?)\s*元\s*目標價",
    ]
    results: list[tuple[float, int, int]] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            price = parse_float(match.group(1))
            if price is not None:
                results.append((price, match.start(), match.end()))
    return results


def extract_eps(text: str) -> float | None:
    patterns = [
        r"EPS\s*(?:估|預估|為|達|上看)?\s*([0-9,]+(?:\.\d+)?)\s*元?",
        r"每股盈餘\s*(?:估|預估|為|達)?\s*([0-9,]+(?:\.\d+)?)\s*元?",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return parse_float(match.group(1))
    return None


def extract_multiple(text: str) -> float | None:
    match = re.search(r"([0-9,]+(?:\.\d+)?)\s*倍\s*(?:本益比|PE|P/E|PER)?", text, re.I)
    return parse_float(match.group(1)) if match else None


def extract_rating(text: str) -> str | None:
    match = re.search(r"(買進|增加持股|加碼|優於大盤|中立|持有|賣出)", text)
    return match.group(1) if match else None


def extract_target_records(stock_code: str, stock_name: str, url: str, body: str) -> tuple[list[dict[str, Any]], str]:
    text = html_to_text(body)
    if "目標價" not in text and "上看" not in text:
        return [], "no_target_keyword"
    title = extract_title(body, url)
    report_date = parse_report_date(text)
    records: list[dict[str, Any]] = []
    for price, start_pos, end_pos in extract_target_prices(text):
        start = max(0, start_pos - 260)
        end = min(len(text), end_pos + 360)
        evidence = text[start:end]
        if stock_code not in evidence and stock_name not in evidence and stock_code not in title and stock_name not in title:
            continue
        estimate_year = infer_year(evidence, report_date)
        record = {
            "stockCode": stock_code,
            "companyName": stock_name,
            "reportDate": report_date,
            "sourceName": source_name_from_url(url),
            "targetPrice": price,
            "rating": extract_rating(evidence),
            "estimateYear": estimate_year,
            "eps": extract_eps(evidence),
            "valuationMultiple": extract_multiple(evidence),
            "sourceUrl": url,
            "sourceTitle": title,
            "evidenceText": evidence[:420],
        }
        records.append(record)
    dedup: dict[tuple[float, str, int | None], dict[str, Any]] = {}
    for record in records:
        dedup[(record["targetPrice"], record["sourceUrl"], record["estimateYear"])] = record
    return list(dedup.values()), "found" if records else "target_price_without_company_marker"


def aggregate(records: list[dict[str, Any]]) -> dict[str, Any]:
    prices = [float(item["targetPrice"]) for item in records if item.get("targetPrice") is not None]
    if not prices:
        return {"mean": None, "low": None, "high": None, "count": 0, "sources": []}
    return {"mean": round(mean(prices), 2), "low": min(prices), "high": max(prices), "count": len(prices), "sources": records[:10]}


def update_forecast_entry(entry: dict[str, Any], records: list[dict[str, Any]]) -> None:
    by_year: dict[str, list[dict[str, Any]]] = {str(year): [] for year in TARGET_YEARS}
    unclassified: list[dict[str, Any]] = []
    for record in records:
        year = record.get("estimateYear")
        if year in TARGET_YEARS:
            by_year[str(year)].append(record)
        else:
            unclassified.append(record)
    entry["analystReportTargetsByYear"] = {year: aggregate(items) for year, items in by_year.items()}
    entry["unclassifiedAnalystTargets"] = aggregate(unclassified)
    entry["analystReportTargetUpdatedAt"] = date.today().isoformat()


def log_row(stock_code: str, stock_name: str, query: str, title: str, url: str, record: dict[str, Any] | None, reason: str) -> dict[str, Any]:
    return {
        "stockId": stock_code,
        "companyName": stock_name,
        "query": query,
        "sourceTitle": title,
        "sourceUrl": url,
        "foundTargetPrice": record.get("targetPrice") if record else "",
        "foundEpsYear": record.get("estimateYear") if record else "",
        "foundEps": record.get("eps") if record else "",
        "reason": reason,
        "classifiedYear": record.get("estimateYear") if record and record.get("estimateYear") else "",
    }


def collect_urls(query: str) -> list[str]:
    urls: list[str] = []
    for url in search_urls(query) + yahoo_search_urls(query):
        if url not in urls:
            urls.append(url)
    return urls[:12]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=20, help="最多查找幾家公司")
    parser.add_argument("--codes", default="", help="逗號分隔股票代號，例如 6223,3491")
    args = parser.parse_args()

    forecasts = load_js_object(EPS_PATH, "CB_EPS_FORECASTS")
    names = issuer_map()
    for entry in forecasts.values():
        if "analystReportTargetsByYear" not in entry:
            update_forecast_entry(entry, [])

    selected = [code.strip() for code in args.codes.split(",") if code.strip()] or list(forecasts.keys())[: args.limit]
    log_rows: list[dict[str, Any]] = []

    for stock_code in selected[: args.limit if not args.codes else len(selected)]:
        entry = forecasts.get(stock_code)
        if not entry:
            continue
        stock_name = names.get(stock_code, stock_code)
        records: list[dict[str, Any]] = []
        tried_urls: set[str] = set()
        for query in build_queries(stock_code, stock_name):
            for url in collect_urls(query):
                if url in tried_urls:
                    continue
                tried_urls.add(url)
                try:
                    body, content_type, final_url = fetch_url(url)
                except Exception as exc:
                    log_rows.append(log_row(stock_code, stock_name, query, "", url, None, f"request_failed:{type(exc).__name__}"))
                    continue
                title = extract_title(body, final_url)
                if "pdf" in content_type.lower() or final_url.lower().endswith(".pdf"):
                    log_rows.append(log_row(stock_code, stock_name, query, title, final_url, None, "pdf_not_parsed"))
                    continue
                found, reason = extract_target_records(stock_code, stock_name, final_url, body)
                if not found:
                    log_rows.append(log_row(stock_code, stock_name, query, title, final_url, None, reason))
                    continue
                for record in found:
                    records.append(record)
                    reason_text = "classified" if record.get("estimateYear") else "unclassified_no_eps_year"
                    log_rows.append(log_row(stock_code, stock_name, query, record["sourceTitle"], final_url, record, reason_text))
                if len(records) >= 10:
                    break
            if len(records) >= 10:
                break
        update_forecast_entry(entry, records)
        if not tried_urls:
            log_rows.append(log_row(stock_code, stock_name, "", "", "", None, "no_search_results"))

    save_eps(forecasts)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=LOG_FIELDS)
        writer.writeheader()
        writer.writerows(log_rows)
    found = sum(1 for row in log_rows if row["foundTargetPrice"])
    classified = sum(1 for row in log_rows if row["classifiedYear"])
    print(f"Checked {len(selected)} stocks, target records {found}, classified {classified}, log: {LOG_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
