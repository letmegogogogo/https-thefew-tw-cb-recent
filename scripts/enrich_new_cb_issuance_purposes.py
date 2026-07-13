from __future__ import annotations

import argparse
import csv
import html
import io
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
RECENT_PATH = ROOT / "outputs" / "recent-cb-data.js"
PRIMARY_PATH = ROOT / "outputs" / "cb-primary-market-data.js"
PURPOSE_PATH = ROOT / "data" / "cb-issuance-purpose.json"
LOG_PATH = ROOT / "outputs" / "new-cb-issuance-purpose-log.csv"
RECENT_PREFIX = "window.RECENT_CB_DATA = "
PRIMARY_PREFIX = "window.CB_PRIMARY_MARKET_DATA = "
TZ = timezone(timedelta(hours=8))
HEADERS = {"User-Agent": "Mozilla/5.0 Chrome/126 Safari/537.36", "Accept-Language": "zh-TW,zh;q=0.9"}

OFFICIAL_DOMAINS = ("mops.twse.com.tw", "mopsov.twse.com.tw", "twse.com.tw", "tpex.org.tw", "twsa.org.tw")
PUBLIC_DOMAINS = ("tw.stock.yahoo.com", "moneydj.com", "m.moneydj.com", "wealth.firstbank.com.tw")
PENDING_SOURCES = {"", "pending", "needs_review", "search_pending", "legacy_excel_needs_recheck"}
PENDING_SUMMARIES = {
    "", "公開資料未整理", "未整理", "公開來源未能確認發債原因",
    "待查：尚未成功抓取官方資金用途文件", "待查：已找到官方文件但尚未成功解析資金用途",
}
PURPOSE_MARKERS = ("募得價款之用途及運用計畫", "資金運用計畫", "募集資金用途", "募集資金運用計畫", "發行目的", "資金用途")


def now_iso() -> str:
    return datetime.now(TZ).isoformat()


def today_text() -> str:
    return datetime.now(TZ).date().isoformat()


def parse_js(path: Path, prefix: str) -> dict:
    try:
        text = path.read_text(encoding="utf-8-sig").strip()
        return json.loads(text[len(prefix):].rstrip(";")) if text.startswith(prefix) else {}
    except (OSError, ValueError):
        return {}


def load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def first(row: dict, *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def load_candidates() -> list[dict]:
    candidates: dict[str, dict] = {}
    for row in parse_js(RECENT_PATH, RECENT_PREFIX).get("rows", []):
        code = first(row, "bondCode")
        if not code:
            continue
        candidates[code] = {
            "bondCode": code, "bondName": first(row, "bondShortName", "bondName"),
            "issuerCode": first(row, "issuerCode"), "issuerName": first(row, "issuerName"),
            "issueDate": first(row, "issueDate", "listingDate"),
            "foundInRecentData": True, "foundInPrimaryMarketData": False,
        }
    for section in parse_js(PRIMARY_PATH, PRIMARY_PREFIX).get("sections", []):
        for row in section.get("rows", []):
            code = first(row, "CB代碼", "bondCode")
            if not code:
                continue
            item = candidates.setdefault(code, {
                "bondCode": code, "bondName": first(row, "標的名稱", "債券名稱", "bondName"),
                "issuerCode": first(row, "公司代號", "issuerCode"),
                "issuerName": first(row, "公司名稱", "companyName", "標的名稱"),
                "issueDate": first(row, "掛牌日", "發行日", "listingDate", "issueDate", "公告日期"),
                "foundInRecentData": False, "foundInPrimaryMarketData": True,
            })
            item["foundInPrimaryMarketData"] = True
    return list(candidates.values())


def useful_existing(record: dict) -> bool:
    if not isinstance(record, dict):
        return False
    source = str(record.get("source") or "").strip()
    summary = str(record.get("summary") or "").strip()
    purposes = record.get("purposes") if isinstance(record.get("purposes"), list) else []
    evidence = str(record.get("evidenceText") or "")
    return (
        source not in PENDING_SOURCES
        and "excel" not in source.lower()
        and summary not in PENDING_SUMMARIES
        and bool(purposes)
        and bool(record.get("sourceUrl") or evidence)
        and any(marker in evidence or marker in summary for marker in PURPOSE_MARKERS)
    )


def clean_html(text: str) -> str:
    text = re.sub(r"(?is)<script.*?</script>|<style.*?</style>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"(?s)<[^>]+>", " ", text))).strip()


def fetch_bytes(url: str, timeout: int) -> tuple[bytes, str]:
    with urlopen(Request(url, headers=HEADERS), timeout=timeout) as response:
        return response.read(), response.headers.get_content_type()


def decode_bytes(raw: bytes) -> str:
    for encoding in ("utf-8", "big5", "cp950"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            pass
    return raw.decode("utf-8", errors="ignore")


def fetch_document(url: str, timeout: int) -> tuple[str, str]:
    try:
        raw, content_type = fetch_bytes(url, timeout)
    except TimeoutError:
        return "", "official_source_timeout"
    except Exception:
        return "", "official_source_blocked"
    is_pdf = "pdf" in content_type or raw.startswith(b"%PDF") or urlparse(url).path.lower().endswith(".pdf")
    if is_pdf:
        try:
            from pypdf import PdfReader  # optional; no new dependency required
            text = " ".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(raw)).pages)
            return re.sub(r"\s+", " ", text), ""
        except Exception:
            return "", "official_pdf_found_but_not_parsed"
    return clean_html(decode_bytes(raw)), ""


def host_allowed(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return any(host == domain or host.endswith("." + domain) for domain in OFFICIAL_DOMAINS + PUBLIC_DOMAINS)


def search_result_urls(query: str, timeout: int, limit: int) -> list[str]:
    result: list[str] = []
    for search_url in (
        f"https://duckduckgo.com/html/?q={quote_plus(query)}",
        f"https://tw.search.yahoo.com/search?p={quote_plus(query)}",
    ):
        try:
            raw, _ = fetch_bytes(search_url, timeout)
            page = decode_bytes(raw)
        except Exception:
            continue
        for match in re.finditer(r'href=["\']([^"\']+)["\']', page):
            url = html.unescape(match.group(1))
            if "uddg=" in url:
                url = unquote(parse_qs(urlparse(url).query).get("uddg", [""])[0])
            elif "/RU=" in url:
                url = unquote(url.split("/RU=", 1)[1].split("/RK=", 1)[0])
            if url.startswith("//"):
                url = "https:" + url
            if url.startswith("http") and host_allowed(url) and url not in result:
                result.append(url)
                if len(result) >= limit:
                    return result
    return result


def clean_company_name(value: str) -> str:
    return re.sub(r"(股份有限公司|有限公司)$", "", str(value or "").strip())


def identity_matched(text: str, row: dict) -> bool:
    code = row.get("bondCode") or ""
    bond_name = row.get("bondName") or ""
    issuer = clean_company_name(row.get("issuerName") or "")
    return bool((code and code in text) or (bond_name and bond_name in text) or (issuer and issuer in text and "轉換公司債" in text))


def classify_purposes(text: str) -> list[str]:
    rules = [
        ("償還借款", ("償還銀行借款", "償還金融機構借款", "償還借款")),
        ("充實營運資金", ("充實營運資金", "營運週轉金", "充實營運週轉")),
        ("購置設備", ("購置機器設備", "購置設備", "取得設備", "機器設備")),
        ("建置廠房", ("興建廠房", "新建廠房", "建置廠房")),
        ("擴廠", ("擴建廠房", "擴充產能", "產能擴充", "擴產", "擴廠")),
        ("轉投資", ("轉投資子公司", "轉投資", "增加投資", "長期股權投資")),
        ("研發支出", ("研發支出", "產品開發", "技術開發", "研發")),
        ("原物料採購", ("原物料採購", "購置原料", "採購原料")),
    ]
    return [label for label, words in rules if any(word in text for word in words)]


def extract_purpose(text: str) -> tuple[str, str, list[str]]:
    positions = [text.find(marker) for marker in PURPOSE_MARKERS if text.find(marker) >= 0]
    if not positions:
        return "", "", []
    start = max(0, min(positions) - 40)
    evidence = text[start:min(len(text), min(positions) + 360)].strip()
    purposes = classify_purposes(evidence)
    if not purposes:
        return "", "", []
    summary = "募集資金用途：" + "、".join(purposes)
    return evidence, summary, purposes


def source_name(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if "twsa.org.tw" in host: return "official_twsa"
    if "mops" in host: return "official_mops"
    if "tpex" in host: return "official_tpex"
    if "twse" in host: return "official_twse"
    if "yahoo" in host: return "yahoo_news_with_explicit_use_of_proceeds"
    if "moneydj" in host: return "moneydj_with_explicit_use_of_proceeds"
    return "public_announcement_with_explicit_use_of_proceeds"


def queries(row: dict) -> list[str]:
    code, name, issuer = row.get("bondCode", ""), row.get("bondName", ""), clean_company_name(row.get("issuerName", ""))
    return [
        f"{code} {name} 募得價款之用途及運用計畫",
        f"{issuer} 轉換公司債 募得價款之用途及運用計畫",
        f"{issuer} 轉換公司債 募集資金用途",
        f"{issuer} 轉換公司債 資金運用計畫",
        f"{issuer} 董事會決議發行國內轉換公司債",
    ]


def discover(row: dict, timeout: int, max_candidates: int) -> list[str]:
    urls: list[str] = []
    for query in queries(row):
        for url in search_result_urls(query, timeout, max_candidates):
            if url not in urls:
                urls.append(url)
    return urls[: max(4, max_candidates * 3)]


def find_purpose(row: dict, timeout: int, max_candidates: int) -> tuple[dict | None, str, list[str]]:
    urls = discover(row, timeout, max_candidates)
    row["_searchedUrls"] = urls
    pdf_url = ""
    for url in urls:
        text, reason = fetch_document(url, timeout)
        if reason == "official_pdf_found_but_not_parsed":
            pdf_url = pdf_url or url
            continue
        if not text or not identity_matched(text, row):
            continue
        evidence, summary, purposes = extract_purpose(text)
        if evidence:
            return {
                "bondCode": row["bondCode"], "issuerCode": row.get("issuerCode", ""),
                "issuerName": row.get("issuerName", ""), "purposes": purposes, "summary": summary,
                "source": source_name(url), "sourceUrl": url, "evidenceText": evidence,
                "retry": False, "updatedAt": today_text(),
            }, "matched_explicit_use_of_proceeds", urls
    return None, "official_pdf_found_but_not_parsed" if pdf_url else "official_document_not_yet_fetched", urls


def priority(row: dict) -> tuple:
    try:
        issue = date.fromisoformat(str(row.get("issueDate") or "")[:10])
    except ValueError:
        issue = date.min
    today = datetime.now(TZ).date()
    return (0 if issue >= today else 1, abs((issue - today).days), row.get("bondCode", ""))


def pending_record(row: dict, reason: str, urls: list[str]) -> dict:
    pdf = next((url for url in urls if urlparse(url).path.lower().endswith(".pdf")), "")
    return {
        "bondCode": row["bondCode"], "issuerCode": row.get("issuerCode", ""),
        "issuerName": row.get("issuerName", ""), "purposes": [],
        "summary": "待查：已找到官方文件但尚未成功解析資金用途" if reason == "official_pdf_found_but_not_parsed" else "待查：尚未成功抓取官方資金用途文件",
        "source": "search_pending", "sourceUrl": pdf, "evidenceText": "", "retry": True,
        "updatedAt": today_text(), "lastAttemptAt": now_iso(),
    }


def write_log(items: list[dict]) -> None:
    fields = ["checkedAt", "bondCode", "bondName", "issuerCode", "issuerName", "foundInRecentData",
              "foundInPrimaryMarketData", "existingPurposeStatus", "searchedUrls", "action", "purposes",
              "summary", "source", "sourceUrl", "evidenceText", "reason", "retry"]
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader(); writer.writerows(items)


def log_row(row: dict, existing_status: str, action: str, record: dict, reason: str) -> dict:
    return {
        "checkedAt": now_iso(), "bondCode": row.get("bondCode", ""), "bondName": row.get("bondName", ""),
        "issuerCode": row.get("issuerCode", ""), "issuerName": row.get("issuerName", ""),
        "foundInRecentData": row.get("foundInRecentData", False),
        "foundInPrimaryMarketData": row.get("foundInPrimaryMarketData", False),
        "existingPurposeStatus": existing_status, "searchedUrls": " | ".join(row.get("_searchedUrls", [])),
        "action": action, "purposes": "、".join(record.get("purposes", [])),
        "summary": record.get("summary", ""), "source": record.get("source", ""),
        "sourceUrl": record.get("sourceUrl", ""), "evidenceText": record.get("evidenceText", ""),
        "reason": reason, "retry": record.get("retry", False),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--codes", default="")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--timeout", type=int, default=10)
    parser.add_argument("--max-candidates", type=int, default=3)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    requested = {value.strip() for value in args.codes.split(",") if value.strip()}
    purposes = load_json(PURPOSE_PATH)
    rows = load_candidates()
    for code in requested:
        if not any(row["bondCode"] == code for row in rows):
            rows.append({"bondCode": code, "bondName": "", "issuerCode": "", "issuerName": "", "issueDate": "", "foundInRecentData": False, "foundInPrimaryMarketData": False})
    logs: list[dict] = []
    processed = enriched = pending = 0
    for row in sorted(rows, key=priority):
        code = row["bondCode"]
        if requested and code not in requested:
            continue
        existing = purposes.get(code, {})
        status = str(existing.get("source") or "missing")
        if useful_existing(existing) and not args.force:
            logs.append(log_row(row, status, "skipped_existing", existing, "existing_confirmed")); continue
        if (
            not requested and not args.force
            and status in PENDING_SOURCES
            and str(existing.get("updatedAt") or "") == today_text()
        ):
            logs.append(log_row(row, status, "deferred", existing, "checked_today_retry_next_day")); continue
        if processed >= args.limit:
            logs.append(log_row(row, status, "deferred", existing, "limit_reached_retry_next_run")); continue
        record, reason, urls = find_purpose(row, args.timeout, args.max_candidates)
        processed += 1
        if record:
            purposes[code] = record; enriched += 1
            logs.append(log_row(row, status, "enriched", record, reason))
        else:
            record = pending_record(row, reason, urls); purposes[code] = record; pending += 1
            logs.append(log_row(row, status, "search_pending", record, reason))
    if processed:
        PURPOSE_PATH.write_text(json.dumps(purposes, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_log(logs)
    print(f"processed={processed} enriched={enriched} search_pending={pending}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
