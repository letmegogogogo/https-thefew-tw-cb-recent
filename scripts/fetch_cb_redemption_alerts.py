from __future__ import annotations

import argparse
import csv
import html
import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, unquote, urlparse
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
RECENT_DATA_PATH = ROOT / "outputs" / "recent-cb-data.js"
ALERTS_PATH = ROOT / "data" / "cb-redemption-alerts.json"
LOG_PATH = ROOT / "outputs" / "cb-redemption-alerts-log.csv"
PREFIX = "window.RECENT_CB_DATA = "
TZ = timezone(timedelta(hours=8))
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.6",
}

REDEMPTION_KEYWORDS = [
    "行使債券贖回權",
    "行使贖回權",
    "債券收回",
    "收回通知",
    "強制贖回",
    "終止櫃檯買賣",
    "停止受理轉換",
    "轉換公司債帳簿劃撥轉換",
    "贖回",
    "收回",
]

CONFIRMED_REDEMPTION_PHRASES = [
    ("發行公司行使債券贖回權", "終止櫃檯買賣"),
    ("行使債券贖回權", "終止櫃檯買賣"),
    ("行使債券贖回權暨訂於", "終止櫃檯買賣"),
    ("債券贖回權", "終止櫃檯買賣等相關事宜"),
]

OFFICIAL_REDEMPTION_KEYWORDS = [
    "發行公司行使債券贖回權",
    "行使債券贖回權",
    "行使債券贖回權暨訂於",
    "債券贖回權",
    "終止櫃檯買賣",
    "終止買賣",
]

TRUSTED_DOMAINS = (
    "tw.stock.yahoo.com",
    "mops.twse.com.tw",
    "tpex.org.tw",
    "www.tpex.org.tw",
    "twse.com.tw",
    "www.twse.com.tw",
)


def fetch_text(url: str, timeout: int = 12) -> str:
    request = Request(url, headers=HEADERS)
    with urlopen(request, timeout=timeout) as response:
        raw = response.read()
    for encoding in ("utf-8", "big5", "cp950"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def load_recent_rows() -> list[dict]:
    text = RECENT_DATA_PATH.read_text(encoding="utf-8").strip()
    if not text.startswith(PREFIX):
        raise ValueError("recent-cb-data.js format is invalid")
    payload = json.loads(text[len(PREFIX) :].rstrip(";"))
    rows = payload.get("rows", [])
    return rows if isinstance(rows, list) else []


def load_alerts() -> dict:
    try:
        payload = json.loads(ALERTS_PATH.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError):
        return {}


def save_alerts(alerts: dict) -> None:
    ALERTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    ALERTS_PATH.write_text(
        json.dumps(alerts, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def active_cb_rows(rows: list[dict], codes: set[str] | None = None, limit: int | None = None) -> list[dict]:
    active: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        code = str(row.get("bondCode") or "").strip()
        if not code or code in seen:
            continue
        if codes and code not in codes:
            continue
        remaining = row.get("remainingAmount")
        try:
            if remaining is not None and float(str(remaining).replace(",", "")) <= 0:
                continue
        except ValueError:
            pass
        seen.add(code)
        active.append(row)
        if limit and len(active) >= limit:
            break
    return active


def clean_text(text: str) -> str:
    text = re.sub(r"(?is)<script.*?</script>|<style.*?</style>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def extract_title(page: str) -> str:
    match = re.search(r"(?is)<title[^>]*>(.*?)</title>", page)
    return clean_text(match.group(1)) if match else ""


def is_trusted_url(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return any(host == domain or host.endswith("." + domain) for domain in TRUSTED_DOMAINS)


def extract_search_urls(page: str) -> list[str]:
    urls: list[str] = []
    for match in re.finditer(r'href=["\']([^"\']+)["\']', page):
        href = html.unescape(match.group(1))
        if "uddg=" in href:
            parsed = parse_qs(urlparse(href).query).get("uddg", [""])[0]
            href = unquote(parsed)
        elif "/RU=" in href:
            part = href.split("/RU=", 1)[1].split("/RK=", 1)[0]
            href = unquote(part)
        if href.startswith("//"):
            href = "https:" + href
        if href.startswith("http") and is_trusted_url(href) and href not in urls:
            urls.append(href)
    return urls


def search_web(query: str, timeout: int = 12) -> list[str]:
    search_urls = [
        f"https://duckduckgo.com/html/?q={quote_plus(query)}",
        f"https://tw.search.yahoo.com/search?p={quote_plus(query)}",
    ]
    results: list[str] = []
    for url in search_urls:
        try:
            results.extend(extract_search_urls(fetch_text(url, timeout=timeout)))
        except Exception:
            continue
    unique: list[str] = []
    for item in results:
        if item not in unique:
            unique.append(item)
    return unique


def build_broad_queries() -> list[str]:
    return [
        "site:tw.stock.yahoo.com/news 行使贖回權 轉換公司債",
        "site:tw.stock.yahoo.com/news 終止櫃檯買賣 轉換公司債",
        "site:tw.stock.yahoo.com/news 停止受理轉換 可轉換公司債",
        "site:tw.stock.yahoo.com/news 債券收回 可轉換公司債",
        "site:tw.stock.yahoo.com/news 強制贖回 可轉換公司債",
    ]


def build_queries(row: dict) -> list[str]:
    bond_code = str(row.get("bondCode") or "").strip()
    bond_name = str(row.get("bondShortName") or "").strip()
    issuer = str(row.get("issuerName") or "").replace("股份有限公司", "").strip()
    base = [
        f"site:tw.stock.yahoo.com/news {bond_code} {bond_name} 行使贖回權",
        f"site:tw.stock.yahoo.com/news {bond_name} 終止櫃檯買賣",
        f"site:tw.stock.yahoo.com/news {issuer} 可轉換公司債 行使贖回權",
    ]
    return [query for query in base if query.strip()]


def has_identity(text: str, row: dict) -> bool:
    bond_code = str(row.get("bondCode") or "").strip()
    bond_name = str(row.get("bondShortName") or "").strip()
    issuer = str(row.get("issuerName") or "").replace("股份有限公司", "").strip()
    return bool((bond_code and bond_code in text) or (bond_name and bond_name in text) or (issuer and issuer in text and "可轉換公司債" in text))


def extract_evidence(text: str) -> str:
    phrase_positions = [
        text.find(keyword)
        for keyword in OFFICIAL_REDEMPTION_KEYWORDS
        if text.find(keyword) >= 0
    ]
    if phrase_positions:
        start = max(0, min(phrase_positions) - 120)
        end = min(len(text), max(phrase_positions) + 260)
        return text[start:end].strip()
    for keyword in REDEMPTION_KEYWORDS:
        idx = text.find(keyword)
        if idx >= 0:
            start = max(0, idx - 80)
            end = min(len(text), idx + 220)
            return text[start:end].strip()
    return ""


def is_confirmed_redemption_notice(text: str, row: dict) -> bool:
    if not any(first in text and second in text for first, second in CONFIRMED_REDEMPTION_PHRASES):
        return False
    return has_identity(text, row)


def extract_date_after(labels: list[str], text: str) -> str:
    date_pattern = r"(\d{3,4}[./-]\d{1,2}[./-]\d{1,2})"
    for label in labels:
        idx = text.find(label)
        if idx < 0:
            continue
        snippet = text[idx : idx + 120]
        match = re.search(date_pattern, snippet)
        if match:
            return normalize_date(match.group(1))
    return ""


def normalize_date(value: str) -> str:
    parts = re.split(r"[./-]", value)
    if len(parts) != 3:
        return value
    year = int(parts[0])
    if year < 1911:
        year += 1911
    return f"{year:04d}-{int(parts[1]):02d}-{int(parts[2]):02d}"


def make_summary(evidence: str) -> str:
    if "終止櫃檯買賣" in evidence:
        return "已公告收回並將終止櫃檯買賣。"
    if "停止受理轉換" in evidence:
        return "已公告行使贖回權並停止受理轉換。"
    if "行使" in evidence and "贖回權" in evidence:
        return "已公告行使債券贖回權。"
    if "債券收回" in evidence or "強制贖回" in evidence or "收回" in evidence:
        return "已公告債券收回相關事項。"
    return "已公告贖回相關事項。"


def parse_alert_with_reason(row: dict, url: str, page: str) -> tuple[dict | None, str]:
    text = clean_text(page)
    title = extract_title(page)
    full_text = f"{title} {text}"
    if not has_identity(full_text, row):
        return None, "identity_not_matched"
    if not any(keyword in full_text for keyword in OFFICIAL_REDEMPTION_KEYWORDS):
        return None, "no_required_official_phrase"
    evidence = extract_evidence(full_text)
    if not evidence:
        return None, "no_required_official_phrase"
    if not is_confirmed_redemption_notice(evidence, row):
        return None, "not_confirmed_redemption_notice"
    return {
        "bondCode": str(row.get("bondCode") or "").strip(),
        "bondName": row.get("bondShortName") or "",
        "issuerCode": str(row.get("issuerCode") or "").strip(),
        "issuerName": row.get("issuerName") or "",
        "status": "已公告收回",
        "alertLevel": "warning",
        "summary": make_summary(evidence),
        "redemptionStartDate": extract_date_after(["收回期間", "贖回期間", "債券收回基準日"], text),
        "redemptionEndDate": extract_date_after(["停止受理轉換", "最後申請轉換日", "收回終止日"], text),
        "delistDate": extract_date_after(["終止櫃檯買賣日", "終止買賣日", "停止交易日"], text),
        "source": "公開公告",
        "sourceUrl": url,
        "evidenceText": evidence,
        "updatedAt": datetime.now(TZ).date().isoformat(),
    }, "found_confirmed_redemption_notice"


def parse_alert(row: dict, url: str, page: str) -> dict | None:
    alert, _reason = parse_alert_with_reason(row, url, page)
    return alert


def find_alert_for_row(row: dict, timeout: int, max_candidates: int) -> tuple[dict | None, list[dict]]:
    logs: list[dict] = []
    for query in build_queries(row):
        urls = search_web(query, timeout=timeout)[:max_candidates]
        if not urls:
            logs.append(log_row(row, query, "", "not_found", "搜尋結果無可信來源"))
            continue
        for url in urls:
            try:
                page = fetch_text(url, timeout=timeout)
                alert, reason = parse_alert_with_reason(row, url, page)
            except Exception as error:
                logs.append(log_row(row, query, url, "error", type(error).__name__))
                continue
            if alert:
                logs.append(log_row(row, query, url, "found", reason))
                return alert, logs
            logs.append(log_row(row, query, url, "not_matched", reason))
            time.sleep(0.2)
    return None, logs


def find_alerts_from_broad_search(rows: list[dict], timeout: int, max_candidates: int) -> tuple[dict, list[dict]]:
    alerts: dict[str, dict] = {}
    logs: list[dict] = []
    candidates: list[tuple[str, str]] = []
    for query in build_broad_queries():
        urls = search_web(query, timeout=timeout)[:max_candidates]
        if not urls:
            logs.append(log_row({}, query, "", "not_found", "廣泛搜尋無可信來源"))
        for url in urls:
            if (query, url) not in candidates:
                candidates.append((query, url))

    for query, url in candidates:
        try:
            page = fetch_text(url, timeout=timeout)
        except Exception as error:
            logs.append(log_row({}, query, url, "error", type(error).__name__))
            continue
        matched = 0
        for row in rows:
            code = str(row.get("bondCode") or "").strip()
            if code in alerts:
                continue
            alert, reason = parse_alert_with_reason(row, url, page)
            if alert:
                alerts[code] = alert
                matched += 1
                logs.append(log_row(row, query, url, "found", reason))
            elif code:
                logs.append(log_row(row, query, url, "not_matched", reason))
        if matched == 0:
            logs.append(log_row({}, query, url, "not_matched", "候選頁未對應目前存續 CB 贖回公告"))
        time.sleep(0.2)
    return alerts, logs


def log_row(row: dict, query: str, url: str, status: str, reason: str) -> dict:
    return {
        "bondCode": row.get("bondCode") or "",
        "bondName": row.get("bondShortName") or "",
        "issuerCode": row.get("issuerCode") or "",
        "issuerName": row.get("issuerName") or "",
        "query": query,
        "sourceUrl": url,
        "status": status,
        "reason": reason,
        "checkedAt": datetime.now(TZ).isoformat(),
    }


def write_log(rows: list[dict]) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    fields = ["bondCode", "bondName", "issuerCode", "issuerName", "query", "sourceUrl", "status", "reason", "checkedAt"]
    with LOG_PATH.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--codes", help="逗號分隔的 CB 代碼，例如 62236,81551")
    parser.add_argument("--limit", type=int, help="最多檢查幾檔 CB；預設檢查全部")
    parser.add_argument("--timeout", type=int, default=10)
    parser.add_argument("--max-candidates", type=int, default=2)
    parser.add_argument("--max-seconds", type=int, default=480, help="整體最多執行秒數，避免自動部署卡住")
    args = parser.parse_args()

    codes = {item.strip() for item in args.codes.split(",")} if args.codes else None
    rows = active_cb_rows(load_recent_rows(), codes=codes, limit=args.limit)
    alerts: dict[str, dict] = {}
    logs: list[dict] = []
    found = 0
    started_at = time.monotonic()
    broad_alerts, broad_logs = find_alerts_from_broad_search(rows, timeout=args.timeout, max_candidates=max(args.max_candidates * 5, 10))
    logs.extend(broad_logs)
    for code, alert in broad_alerts.items():
        if alert.get("sourceUrl") and alert.get("evidenceText"):
            alerts[code] = alert
            found += 1

    should_run_row_search = bool(codes) or (args.limit is not None and args.limit <= 30)
    if should_run_row_search:
        for index, row in enumerate(rows):
            if str(row.get("bondCode") or "").strip() in alerts:
                continue
            if args.max_seconds and time.monotonic() - started_at >= args.max_seconds:
                for skipped in rows[index:]:
                    logs.append(log_row(skipped, "", "", "skipped", "達到整體執行時間上限，留待下次排程檢查"))
                break
            alert, row_logs = find_alert_for_row(row, timeout=args.timeout, max_candidates=args.max_candidates)
            logs.extend(row_logs)
            if alert and alert.get("sourceUrl") and alert.get("evidenceText"):
                alerts[alert["bondCode"]] = alert
                found += 1
    save_alerts(alerts)
    write_log(logs)
    print(f"checked={len(rows)} found={found} alerts={len(alerts)} log={LOG_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
