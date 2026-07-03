from __future__ import annotations

import argparse
import csv
import html
import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote_plus, unquote, urlparse, parse_qs, urljoin
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
RECENT_DATA_PATH = ROOT / "outputs" / "recent-cb-data.js"
PRIMARY_DATA_PATH = ROOT / "outputs" / "cb-primary-market-data.js"
PURPOSES_PATH = ROOT / "data" / "cb-issuance-purpose.json"
LOG_PATH = ROOT / "outputs" / "new-cb-issuance-purpose-log.csv"
RECENT_PREFIX = "window.RECENT_CB_DATA = "
PRIMARY_PREFIX = "window.CB_PRIMARY_MARKET_DATA = "
TZ = timezone(timedelta(hours=8))
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.6",
}
OFFICIAL_DOMAINS = (
    "mops.twse.com.tw",
    "mopsov.twse.com.tw",
    "twse.com.tw",
    "www.twse.com.tw",
    "tpex.org.tw",
    "www.tpex.org.tw",
    "web.twsa.org.tw",
    "web2.twsa.org.tw",
    "twsa.org.tw",
    "www.twsa.org.tw",
)
YAHOO_DOMAINS = (
    "tw.stock.yahoo.com",
)
TWSA_UNDERWRITING_URL = "https://web.twsa.org.tw/Edoc2/Default.aspx?Year={year}"
PURPOSE_KEYWORDS = [
    "募得價款之用途及運用計畫",
    "募集資金用途",
    "募集資金運用計畫",
    "資金運用計畫",
    "發行目的",
    "發債原因",
    "本次發行轉換公司債之資金用途",
    "計畫項目",
    "充實營運資金",
    "營運週轉金",
    "充實營運週轉",
    "償還銀行借款",
    "償還金融機構借款",
    "償還借款",
    "購置機器設備",
    "購置設備",
    "取得設備",
    "興建廠房",
    "擴建廠房",
    "擴充產能",
    "擴建",
    "擴廠",
    "轉投資",
    "轉投資子公司",
    "研發",
    "研發支出",
    "原物料採購",
]
PENDING_SUMMARIES = {"", "公開資料未整理", "未整理", "公開來源未能確認發債原因", "待查：尚未成功抓取官方資金用途文件"}
PENDING_SOURCES = {"", "pending", "needs_review", "search_pending", "legacy_excel_needs_recheck"}
LEGACY_EXCEL_SOURCES = {"excel", "old_excel", "imported_excel", "手動Excel", "舊整理檔"}


def now_iso() -> str:
    return datetime.now(TZ).isoformat()


def today_text() -> str:
    return datetime.now(TZ).date().isoformat()


def fetch_text(url: str, timeout: int) -> str:
    request = Request(url, headers=HEADERS)
    with urlopen(request, timeout=timeout) as response:
        raw = response.read()
    for encoding in ("utf-8", "big5", "cp950"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def clean_text(text: str) -> str:
    text = re.sub(r"(?is)<script.*?</script>|<style.*?</style>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def is_official_url(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return any(host == domain or host.endswith("." + domain) for domain in OFFICIAL_DOMAINS)


def is_yahoo_url(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return any(host == domain or host.endswith("." + domain) for domain in YAHOO_DOMAINS)


def extract_search_urls(page: str, predicate=is_official_url) -> list[str]:
    urls: list[str] = []
    for match in re.finditer(r'href=["\']([^"\']+)["\']', page):
        href = html.unescape(match.group(1))
        if "uddg=" in href:
            href = unquote(parse_qs(urlparse(href).query).get("uddg", [""])[0])
        elif "/RU=" in href:
            href = unquote(href.split("/RU=", 1)[1].split("/RK=", 1)[0])
        if href.startswith("//"):
            href = "https:" + href
        if href.startswith("http") and predicate(href) and href not in urls:
            urls.append(href)
    return urls


def search_official_urls(query: str, timeout: int, max_candidates: int) -> list[str]:
    urls: list[str] = []
    search_urls = [
        f"https://duckduckgo.com/html/?q={quote_plus(query)}",
        f"https://tw.search.yahoo.com/search?p={quote_plus(query)}",
    ]
    for url in search_urls:
        try:
            urls.extend(extract_search_urls(fetch_text(url, timeout=timeout)))
        except Exception:
            continue
    unique: list[str] = []
    for url in urls:
        if url not in unique:
            unique.append(url)
    return unique[:max_candidates]


def search_yahoo_urls(query: str, timeout: int, max_candidates: int) -> list[str]:
    urls: list[str] = []
    search_urls = [
        f"https://duckduckgo.com/html/?q={quote_plus('site:tw.stock.yahoo.com/news ' + query)}",
        f"https://tw.search.yahoo.com/search?p={quote_plus('site:tw.stock.yahoo.com/news ' + query)}",
    ]
    for url in search_urls:
        try:
            urls.extend(extract_search_urls(fetch_text(url, timeout=timeout), predicate=is_yahoo_url))
        except Exception:
            continue
    unique: list[str] = []
    for url in urls:
        if url not in unique:
            unique.append(url)
    return unique[:max_candidates]


def parse_js_data(path: Path, prefix: str) -> dict:
    text = path.read_text(encoding="utf-8").strip()
    if not text.startswith(prefix):
        return {}
    return json.loads(text[len(prefix) :].rstrip(";"))


def load_purposes() -> dict:
    try:
        payload = json.loads(PURPOSES_PATH.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError):
        return {}


def save_purposes(purposes: dict) -> None:
    PURPOSES_PATH.write_text(
        json.dumps(purposes, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def primary_value(row: dict, keys: list[str]) -> str:
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def load_candidates() -> list[dict]:
    candidates: dict[str, dict] = {}
    recent = parse_js_data(RECENT_DATA_PATH, RECENT_PREFIX)
    for row in recent.get("rows", []):
        code = str(row.get("bondCode") or "").strip()
        if not code:
            continue
        candidates.setdefault(code, {
            "bondCode": code,
            "bondName": row.get("bondShortName") or row.get("bondName") or "",
            "issuerCode": str(row.get("issuerCode") or "").strip(),
            "issuerName": row.get("issuerName") or "",
            "foundInRecentData": True,
            "foundInPrimaryMarketData": False,
        })

    primary = parse_js_data(PRIMARY_DATA_PATH, PRIMARY_PREFIX)
    for section in primary.get("sections", []):
        for row in section.get("rows", []):
            code = primary_value(row, ["CB代碼", "bondCode"])
            if not code:
                continue
            item = candidates.setdefault(code, {
                "bondCode": code,
                "bondName": primary_value(row, ["債券名稱", "標的名稱", "bondName"]),
                "issuerCode": primary_value(row, ["公司代號", "issuerCode"]),
                "issuerName": primary_value(row, ["公司名稱", "標的名稱", "issuerName"]),
                "foundInRecentData": False,
                "foundInPrimaryMarketData": True,
            })
            item["foundInPrimaryMarketData"] = True
    return list(candidates.values())


def should_process(code: str, existing: dict, force: bool) -> bool:
    if force:
        return True
    if not existing:
        return True
    source = str(existing.get("source") or "").strip()
    summary = str(existing.get("summary") or "").strip()
    purposes = existing.get("purposes") if isinstance(existing.get("purposes"), list) else []
    evidence = str(existing.get("evidenceText") or "")
    has_evidence = bool(existing.get("sourceUrl") or evidence)
    has_purpose_keyword = any(keyword in evidence or keyword in summary for keyword in PURPOSE_KEYWORDS)
    if source in LEGACY_EXCEL_SOURCES or "excel" in source.lower() or "Excel" in source:
        return True
    if (
        source
        and source not in PENDING_SOURCES
        and summary not in PENDING_SUMMARIES
        and has_evidence
        and has_purpose_keyword
        and purposes
    ):
        return False
    return True


def identity_matched(text: str, row: dict) -> bool:
    bond_code = str(row.get("bondCode") or "").strip()
    bond_name = str(row.get("bondName") or "").strip()
    issuer = str(row.get("issuerName") or "").replace("股份有限公司", "").strip()
    return bool(
        (bond_code and bond_code in text)
        or (bond_name and bond_name in text)
        or (issuer and issuer in text and ("轉換公司債" in text or "可轉換公司債" in text))
    )


def extract_links(page: str, base_url: str) -> list[str]:
    links: list[str] = []
    for match in re.finditer(r'href=["\']([^"\']+)["\']', page):
        href = html.unescape(match.group(1)).strip()
        if not href or href.startswith("#") or href.lower().startswith("javascript:"):
            continue
        url = urljoin(base_url, href)
        if url not in links:
            links.append(url)
    return links


def fetch_twsa_underwriting_documents(year: int, row: dict, timeout: int) -> list[str]:
    entry_url = TWSA_UNDERWRITING_URL.format(year=year)
    try:
        page = fetch_text(entry_url, timeout=timeout)
    except Exception:
        return []
    documents: list[str] = []
    for tr in re.findall(r"(?is)<tr[^>]*>(.*?)</tr>", page):
        text = clean_text(tr)
        if not identity_matched(text, row):
            continue
        if "轉換公司債" not in text and "可轉換公司債" not in text:
            continue
        for link in extract_links(tr, entry_url):
            if link not in documents:
                documents.append(link)
    return documents


def current_twsa_years() -> list[int]:
    year = datetime.now(TZ).year
    return list(dict.fromkeys([year, year - 1911, year - 1, year - 1912]))


def fetch_document_text(url: str, timeout: int) -> tuple[str, str]:
    if urlparse(url).path.lower().endswith(".pdf"):
        return "", "official_pdf_found_but_not_parsed"
    try:
        return clean_text(fetch_text(url, timeout=timeout)), ""
    except TimeoutError:
        return "", "official_source_timeout"
    except Exception:
        return "", "official_source_format_changed"


def extract_use_of_proceeds(text: str) -> tuple[str, str, list[str]]:
    for keyword in PURPOSE_KEYWORDS:
        idx = text.find(keyword)
        if idx >= 0:
            start = max(0, idx - 60)
            end = min(len(text), idx + 260)
            evidence = text[start:end].strip()
            purposes = classify_purposes(evidence)
            if not purposes and any(label in evidence for label in ["資金運用計畫", "募集資金用途", "發行目的", "計畫項目"]):
                purposes = ["其他"]
            return evidence, make_summary(evidence), purposes
    return "", "", []


def extract_evidence(text: str) -> str:
    evidence, _summary, _purposes = extract_use_of_proceeds(text)
    return evidence


def classify_purposes(text: str) -> list[str]:
    rules = [
        ("償還借款", ["償還銀行借款", "償還金融機構借款", "償還借款"]),
        ("充實營運資金", ["充實營運資金", "營運週轉金", "充實營運週轉", "營運資金"]),
        ("購置設備", ["購置機器設備", "購置設備", "取得設備", "機器設備"]),
        ("建置廠房", ["興建廠房", "建置廠房", "擴建廠房", "購置廠房"]),
        ("擴廠", ["擴充產能", "產能擴充", "擴建", "擴廠", "擴產"]),
        ("轉投資", ["轉投資子公司", "增加投資", "轉投資", "投資子公司"]),
        ("研發支出", ["研發設備", "研發支出", "產品開發", "研發", "技術開發"]),
        ("原物料採購", ["原物料採購", "購買原料", "採購原料"]),
    ]
    purposes: list[str] = []
    for label, keywords in rules:
        if any(keyword in text for keyword in keywords):
            purposes.append(label)
    return purposes


def make_summary(evidence: str) -> str:
    text = evidence
    for prefix in ["募得價款之用途及運用計畫:", "募得價款之用途及運用計畫：", "募集資金用途:", "募集資金用途："]:
        text = text.replace(prefix, "")
    text = text.strip(" 。；;")
    return (text[:120] + "…") if len(text) > 120 else text


def build_queries(row: dict) -> list[str]:
    code = row.get("bondCode") or ""
    name = row.get("bondName") or ""
    issuer_code = row.get("issuerCode") or ""
    issuer = row.get("issuerName") or ""
    return [
        f"{code} {name} 募得價款之用途及運用計畫",
        f"{code} {name} 資金運用計畫",
        f"{code} 公開說明書 資金運用計畫",
        f"{issuer_code} {issuer} 可轉換公司債 公開說明書",
        f"{issuer} 轉換公司債 資金運用計畫",
        f"{issuer} 轉換公司債 募集資金用途",
        f"{issuer} 轉換公司債 發行辦法",
        f"{issuer} 轉換公司債 承銷公告",
        f"{issuer} 發行 國內 轉換公司債 募得價款",
        f"{code} {issuer} 發行辦法 公開說明書 募集資金用途",
    ]


def build_yahoo_queries(row: dict) -> list[str]:
    code = row.get("bondCode") or ""
    name = row.get("bondName") or ""
    issuer = row.get("issuerName") or ""
    return [
        f"{issuer} 轉換公司債 資金用途",
        f"{issuer} CB 資金用途",
        f"{issuer} 可轉債 償還借款",
        f"{issuer} 可轉債 充實營運資金",
        f"{issuer} 轉換公司債 公開說明書",
        f"{name} 資金用途",
        f"{code} 資金用途",
    ]


def source_from_url(url: str, yahoo: bool = False) -> str:
    if yahoo:
        return "yahoo_news_with_explicit_use_of_proceeds"
    host = urlparse(url).netloc.lower()
    if "twsa.org.tw" in host:
        return "official_twsa"
    if "mops" in host:
        return "official_mops"
    if "tpex" in host:
        return "official_tpex"
    if "twse" in host:
        return "official_twse"
    return "official_public_document"


def discover_official_document_urls(row: dict, timeout: int, max_candidates: int) -> list[str]:
    urls: list[str] = []
    for year in current_twsa_years():
        urls.extend(fetch_twsa_underwriting_documents(year, row, timeout=timeout))
    for query in build_queries(row):
        urls.extend(search_official_urls(query, timeout=timeout, max_candidates=max_candidates))
    unique: list[str] = []
    for url in urls:
        if url not in unique:
            unique.append(url)
    return unique


def discover_yahoo_news_urls(row: dict, timeout: int, max_candidates: int) -> list[str]:
    urls: list[str] = []
    for query in build_yahoo_queries(row):
        urls.extend(search_yahoo_urls(query, timeout=timeout, max_candidates=max_candidates))
    unique: list[str] = []
    for url in urls:
        if url not in unique:
            unique.append(url)
    return unique


def find_official_purpose(row: dict, timeout: int, max_candidates: int) -> tuple[dict | None, str, list[str]]:
    checked_urls: list[str] = []
    row["_searchedOfficialUrls"] = []
    row["_searchedYahooUrls"] = []
    row["_foundDocumentUrls"] = []
    pdf_found = False
    official_urls = discover_official_document_urls(row, timeout=timeout, max_candidates=max_candidates)
    row["_searchedOfficialUrls"] = official_urls
    for url in official_urls:
        if url in checked_urls:
            continue
        checked_urls.append(url)
        row["_foundDocumentUrls"].append(url)
        text, fetch_reason = fetch_document_text(url, timeout=timeout)
        if fetch_reason == "official_pdf_found_but_not_parsed":
            pdf_found = True
            continue
        if not text or not identity_matched(text, row):
            continue
        evidence, summary, purposes = extract_use_of_proceeds(text)
        if evidence and purposes:
            return {
                "bondCode": row["bondCode"],
                "issuerCode": row.get("issuerCode") or "",
                "issuerName": row.get("issuerName") or "",
                "purposes": purposes,
                "summary": summary,
                "source": source_from_url(url),
                "sourceUrl": url,
                "evidenceText": evidence,
                "retry": False,
                "updatedAt": today_text(),
            }, "matched_official_source", checked_urls

    yahoo_urls = discover_yahoo_news_urls(row, timeout=timeout, max_candidates=max_candidates)
    row["_searchedYahooUrls"] = yahoo_urls
    for url in yahoo_urls:
        if url in checked_urls:
            continue
        checked_urls.append(url)
        text, _fetch_reason = fetch_document_text(url, timeout=timeout)
        if not text or not identity_matched(text, row):
            continue
        evidence, summary, purposes = extract_use_of_proceeds(text)
        if evidence and purposes:
            return {
                "bondCode": row["bondCode"],
                "issuerCode": row.get("issuerCode") or "",
                "issuerName": row.get("issuerName") or "",
                "purposes": purposes,
                "summary": summary,
                "source": "yahoo_news_with_explicit_use_of_proceeds",
                "sourceUrl": url,
                "evidenceText": evidence,
                "retry": False,
                "updatedAt": today_text(),
            }, "matched_yahoo_explicit_use_of_proceeds", checked_urls
        time.sleep(0.2)
    return None, "official_pdf_found_but_not_parsed" if pdf_found else "official_document_not_yet_fetched", checked_urls


def log_row(row: dict, action: str, record: dict | None, reason: str) -> dict:
    record = record or {}
    existing_status = row.get("existingPurposeStatus") or ""
    return {
        "checkedAt": now_iso(),
        "bondCode": row.get("bondCode") or record.get("bondCode") or "",
        "bondName": row.get("bondName") or "",
        "issuerCode": row.get("issuerCode") or record.get("issuerCode") or "",
        "issuerName": row.get("issuerName") or record.get("issuerName") or "",
        "foundInRecentData": row.get("foundInRecentData", False),
        "foundInPrimaryMarketData": row.get("foundInPrimaryMarketData", False),
        "existingPurposeStatus": existing_status,
        "searchedOfficialUrls": " | ".join(row.get("_searchedOfficialUrls") or []),
        "searchedYahooUrls": " | ".join(row.get("_searchedYahooUrls") or []),
        "foundDocumentUrls": " | ".join(row.get("_foundDocumentUrls") or []),
        "action": action,
        "purposes": "、".join(record.get("purposes") or []),
        "summary": record.get("summary") or "",
        "source": record.get("source") or "",
        "sourceUrl": record.get("sourceUrl") or "",
        "evidenceText": record.get("evidenceText") or "",
        "reason": reason,
        "retry": record.get("retry", False),
    }


def write_log(rows: list[dict]) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    fields = ["checkedAt", "bondCode", "bondName", "issuerCode", "issuerName", "foundInRecentData", "foundInPrimaryMarketData", "existingPurposeStatus", "searchedOfficialUrls", "searchedYahooUrls", "foundDocumentUrls", "sourceUrl", "action", "purposes", "summary", "source", "evidenceText", "reason", "retry"]
    with LOG_PATH.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--codes", help="指定 CB 代碼，逗號分隔，例如 36054,65843")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--timeout", type=int, default=12)
    parser.add_argument("--max-candidates", type=int, default=4)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    purposes = load_purposes()
    logs: list[dict] = []
    processed = enriched = search_pending = legacy_recheck = 0
    candidates = {row["bondCode"]: row for row in load_candidates()}
    requested_codes = {code.strip() for code in args.codes.split(",") if code.strip()} if args.codes else set()
    for code in requested_codes:
        candidates.setdefault(code, {
            "bondCode": code,
            "bondName": "",
            "issuerCode": "",
            "issuerName": "",
            "foundInRecentData": False,
            "foundInPrimaryMarketData": False,
        })

    for row in candidates.values():
        code = row["bondCode"]
        if requested_codes and code not in requested_codes:
            continue
        existing = purposes.get(code, {})
        if not existing:
            row["existingPurposeStatus"] = "missing"
        else:
            row["existingPurposeStatus"] = str(existing.get("source") or "existing")
        if not should_process(code, existing, args.force):
            logs.append(log_row(row, "skipped_existing", existing, "existing_confirmed"))
            continue
        if processed >= args.limit:
            logs.append(log_row(row, "skipped_existing", existing, "limit_reached"))
            continue
        existing_source = str(existing.get("source") or "").strip()
        if existing_source in LEGACY_EXCEL_SOURCES or "excel" in existing_source.lower() or "Excel" in existing_source:
            existing = {
                **existing,
                "source": "legacy_excel_needs_recheck",
                "retry": True,
                "updatedAt": today_text(),
            }
            purposes[code] = existing
            legacy_recheck += 1
            logs.append(log_row(row, "legacy_excel_needs_recheck", existing, "legacy_excel_source_requires_official_recheck"))
        record, reason, _urls = find_official_purpose(row, timeout=args.timeout, max_candidates=args.max_candidates)
        processed += 1
        if record and record.get("sourceUrl") and record.get("evidenceText"):
            purposes[code] = record
            enriched += 1
            logs.append(log_row(row, "enriched", record, reason))
        else:
            pending_source_url = (row.get("_foundDocumentUrls") or [""])[0] if reason == "official_pdf_found_but_not_parsed" else ""
            pending_record = {
                "bondCode": code,
                "issuerCode": row.get("issuerCode") or "",
                "issuerName": row.get("issuerName") or "",
                "purposes": [],
                "summary": "待查：已找到官方文件但尚未成功解析資金用途" if reason == "official_pdf_found_but_not_parsed" else "待查：尚未成功抓取官方資金用途文件",
                "source": "search_pending",
                "sourceUrl": pending_source_url,
                "evidenceText": "",
                "retry": True,
                "updatedAt": today_text(),
            }
            purposes[code] = pending_record
            search_pending += 1
            logs.append(log_row(row, "search_pending", pending_record, reason))

    if processed:
        save_purposes(purposes)
    write_log(logs)
    print(f"processed={processed} enriched={enriched} search_pending={search_pending} legacy_recheck={legacy_recheck} log={LOG_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
