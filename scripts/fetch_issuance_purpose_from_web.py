from __future__ import annotations

import base64
import csv
import html
import json
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

try:
    import certifi
except ImportError:  # pragma: no cover
    certifi = None


ROOT = Path(__file__).resolve().parents[1]
RECENT_DATA_PATH = ROOT / "outputs" / "recent-cb-data.js"
PURPOSE_PATH = ROOT / "data" / "cb-issuance-purpose.json"
LOG_PATH = ROOT / "outputs" / "issuance-purpose-fetch-log.csv"

TARGET_BOND_CODES = [
    "62236",
    "54642",
    "811211",
    "34913",
    "81551",
    "82103",
    "82102",
    "33247",
    "33246",
    "33245",
]

PURPOSE_KEYWORDS = [
    "募得價款之用途及運用計畫",
    "募集資金用途",
    "資金運用計畫",
    "發行目的",
    "本次募集資金",
    "充實營運資金",
    "購置機器設備",
    "購置設備",
    "償還銀行借款",
    "償還金融機構借款",
    "擴建廠房",
    "擴充產能",
    "轉投資",
    "研發",
]

CLASSIFICATION_RULES = [
    ("擴廠", ["擴建", "擴廠", "建廠", "新建廠房", "擴充產能", "產能擴充"]),
    ("購置設備", ["購置機器設備", "購置設備", "機器設備", "自動化設備", "測試設備"]),
    ("營運資金", ["充實營運資金", "營運資金"]),
    ("償還借款", ["償還銀行借款", "償還借款", "償還金融機構借款"]),
    ("研發", ["研發", "技術開發", "產品開發"]),
    ("轉投資", ["轉投資", "投資子公司", "長期股權投資"]),
]


@dataclass
class Candidate:
    url: str
    source: str
    query: str


def load_recent_rows() -> list[dict[str, Any]]:
    text = RECENT_DATA_PATH.read_text(encoding="utf-8")
    match = re.search(r"window\.RECENT_CB_DATA\s*=\s*(\{.*\})\s*;?\s*$", text, re.S)
    if not match:
        raise ValueError(f"Cannot parse {RECENT_DATA_PATH}")
    return json.loads(match.group(1)).get("rows", [])


def load_existing_purposes() -> dict[str, Any]:
    if not PURPOSE_PATH.exists():
        return {}
    return json.loads(PURPOSE_PATH.read_text(encoding="utf-8"))


def save_purposes(data: dict[str, Any]) -> None:
    PURPOSE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def fetch_url(url: str, timeout: int = 5) -> tuple[str, str, str]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
        },
    )
    context = None
    if url.lower().startswith("https://") and certifi:
        context = ssl.create_default_context(cafile=certifi.where())
    with urllib.request.urlopen(req, timeout=timeout, context=context) as resp:
        final_url = resp.geturl()
        content_type = resp.headers.get("Content-Type", "")
        raw = resp.read(900_000)
    charset_match = re.search(r"charset=([\w-]+)", content_type, re.I)
    charset = charset_match.group(1) if charset_match else "utf-8"
    try:
        return raw.decode(charset, errors="replace"), content_type, final_url
    except LookupError:
        return raw.decode("utf-8", errors="replace"), content_type, final_url


def html_to_text(source: str) -> str:
    source = re.sub(r"(?is)<(script|style).*?</\1>", " ", source)
    source = re.sub(r"(?is)<br\s*/?>", "\n", source)
    source = re.sub(r"(?is)</(p|div|tr|li|td|th|h\d)>", "\n", source)
    source = re.sub(r"(?is)<[^>]+>", " ", source)
    source = html.unescape(source)
    source = re.sub(r"[ \t\r\f\v]+", " ", source)
    source = re.sub(r"\n\s+", "\n", source)
    return source.strip()


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def extract_evidence(text: str) -> str:
    compact = normalize_text(text)
    for keyword in PURPOSE_KEYWORDS:
        pos = compact.find(keyword)
        if pos < 0:
            continue
        start = max(0, pos - 80)
        end = min(len(compact), pos + 520)
        evidence = compact[start:end]
        next_item = re.search(r"(?:^|[^\d])(?:11|12|13|14|15|16|17|18)\.", evidence)
        if next_item and next_item.start() > 80:
            evidence = evidence[: next_item.start()]
        return evidence.strip(" ：:，,。")
    return ""


def classify_purposes(evidence: str) -> list[str]:
    purposes: list[str] = []
    for label, keywords in CLASSIFICATION_RULES:
        if any(keyword in evidence for keyword in keywords):
            purposes.append(label)
    return purposes or (["其他"] if evidence else [])


def summarize_evidence(evidence: str) -> str:
    if not evidence:
        return "公開資料未整理"
    for keyword in PURPOSE_KEYWORDS:
        pos = evidence.find(keyword)
        if pos >= 0:
            return evidence[pos : pos + 170].strip(" ：:，,。")
    return evidence[:170].strip(" ：:，,。")


def decode_ddg_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    params = urllib.parse.parse_qs(parsed.query)
    if params.get("uddg"):
        return params["uddg"][0]
    return url


def decode_bing_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    params = urllib.parse.parse_qs(parsed.query)
    encoded = (params.get("u") or [""])[0]
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


def clean_result_url(url: str) -> str:
    url = html.unescape(url)
    if url.startswith("//"):
        url = "https:" + url
    if "duckduckgo.com/l/" in url:
        url = decode_ddg_url(url)
    if "bing.com/ck/" in url:
        url = decode_bing_url(url)
    return url


def yahoo_search_urls(query: str) -> list[str]:
    urls: list[str] = []
    search_url = "https://tw.stock.yahoo.com/news?" + urllib.parse.urlencode({"keyword": query})
    try:
        body, _, _ = fetch_url(search_url, timeout=5)
    except Exception:
        return urls
    for match in re.findall(r'href="([^"]*/news/[^"]+\.html[^"]*)"', body):
        url = clean_result_url(match)
        if url.startswith("/"):
            url = urllib.parse.urljoin("https://tw.stock.yahoo.com", url)
        if "tw.stock.yahoo.com/news/" in url and url not in urls:
            urls.append(url)
    return urls[:3]


def search_engine_urls(query: str) -> list[str]:
    urls: list[str] = []
    searches = [
        ("https://www.bing.com/search?" + urllib.parse.urlencode({"q": query}), r'href="(https?://[^"]+)"'),
    ]
    for search_url, pattern in searches:
        try:
            body, _, _ = fetch_url(search_url, timeout=5)
        except Exception:
            continue
        links = re.findall(pattern, body)
        if "duckduckgo" in search_url and not links:
            links = re.findall(r'class="result__a"[^>]+href="([^"]+)"', body)
        for link in links[:15]:
            url = clean_result_url(link)
            if not url.startswith("http"):
                continue
            if any(blocked in url for blocked in [
                "bing.com/search",
                "microsoft.com",
                "go.microsoft.com",
                "r.bing.com",
                "th.bing.com",
                "/rs/",
                ".css",
                ".js",
            ]):
                continue
            if url not in urls:
                urls.append(url)
    return urls


def bond_short_name(row: dict[str, Any]) -> str:
    return str(row.get("bondShortName") or row.get("bondName") or row.get("bondAbbr") or "").strip()


def issuer_short_name(row: dict[str, Any]) -> str:
    name = str(row.get("issuerName") or "").strip()
    return re.sub(r"(股份有限公司|有限公司)$", "", name)


def build_queries(row: dict[str, Any]) -> list[str]:
    bond_code = str(row.get("bondCode") or "").strip()
    bond_name = bond_short_name(row)
    issuer = issuer_short_name(row)
    bond_round = re.sub(rf"^{re.escape(issuer)}", "", bond_name).strip() if issuer else bond_name

    queries = [
        f"site:tw.stock.yahoo.com/news {bond_code} {bond_name} 募得價款之用途及運用計畫",
        f"site:tw.stock.yahoo.com/news {bond_name} 可轉換公司債 募得價款",
        f"site:tw.stock.yahoo.com/news {issuer} 發行 國內 {bond_round} 無擔保 轉換公司債",
    ]
    return [q for q in queries if q.strip()]


def guess_source(url: str) -> str:
    host = urllib.parse.urlparse(url).netloc.lower()
    if "tw.stock.yahoo.com" in host:
        return "Yahoo奇摩股市公告"
    if "mops" in host or "twse.com.tw" in host:
        return "公開資訊觀測站"
    if "tpex.org.tw" in host:
        return "櫃買公告"
    return "公開來源"


def build_candidates(row: dict[str, Any]) -> list[Candidate]:
    candidates: list[Candidate] = []
    seen: set[str] = set()

    queries = build_queries(row)
    for query in queries[:1]:
        for url in search_engine_urls(query):
            if url not in seen:
                candidates.append(Candidate(url, guess_source(url), query))
                seen.add(url)

    for query in queries:
        for url in yahoo_search_urls(query):
            if url not in seen:
                candidates.append(Candidate(url, "Yahoo奇摩股市公告", query))
                seen.add(url)

    detail_url = row.get("detailUrl")
    if isinstance(detail_url, str) and detail_url.startswith("http") and detail_url not in seen:
        candidates.append(Candidate(detail_url, "公開資訊觀測站", "detailUrl"))
        seen.add(detail_url)

    fallback_queries = [
        f"{row.get('bondCode') or ''} {bond_short_name(row)} 募得價款之用途及運用計畫",
        f"{issuer_short_name(row)} 可轉換公司債 募得價款",
    ]

    for query in fallback_queries:
        for url in search_engine_urls(query):
            if url not in seen:
                candidates.append(Candidate(url, guess_source(url), query))
                seen.add(url)

    return candidates[:8]


def inspect_candidate(candidate: Candidate) -> tuple[str, str]:
    body, content_type, final_url = fetch_url(candidate.url, timeout=5)
    if "pdf" in content_type.lower() or final_url.lower().endswith(".pdf"):
        return "", "pdf_not_parsed"
    evidence = extract_evidence(html_to_text(body))
    return evidence, "ok" if evidence else "no_keyword"


def pending_record(row: dict[str, Any], updated_at: str) -> dict[str, Any]:
    return {
        "bondCode": str(row.get("bondCode") or ""),
        "issuerCode": str(row.get("issuerCode") or ""),
        "issuerName": str(row.get("issuerName") or ""),
        "purposes": [],
        "summary": "公開資料未整理",
        "source": "pending",
        "sourceUrl": "",
        "evidenceText": "",
        "updatedAt": updated_at,
    }


def found_record(row: dict[str, Any], candidate: Candidate, evidence: str, updated_at: str) -> dict[str, Any]:
    return {
        "bondCode": str(row.get("bondCode") or ""),
        "issuerCode": str(row.get("issuerCode") or ""),
        "issuerName": str(row.get("issuerName") or ""),
        "purposes": classify_purposes(evidence),
        "summary": summarize_evidence(evidence),
        "source": candidate.source,
        "sourceUrl": candidate.url,
        "evidenceText": evidence[:500],
        "updatedAt": updated_at,
    }


def main() -> int:
    today = date.today().isoformat()
    rows = load_recent_rows()
    rows_by_bond = {str(row.get("bondCode")): row for row in rows}
    existing = load_existing_purposes()
    log_rows: list[dict[str, str]] = []

    for bond_code in TARGET_BOND_CODES:
        row = rows_by_bond.get(bond_code)
        if not row:
            log_rows.append({
                "bondCode": bond_code,
                "issuerName": "",
                "status": "missing_cb_row",
                "summary": "",
                "purposes": "",
                "sourceUrl": "",
                "evidenceText": "",
                "note": "recent-cb-data.js 找不到此 CB",
            })
            continue

        best_record: dict[str, Any] | None = None
        best_note = "找不到可靠公開來源"
        candidates = build_candidates(row)
        for candidate in candidates:
            try:
                evidence, note = inspect_candidate(candidate)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                best_note = f"request_failed: {type(exc).__name__}"
                continue
            if evidence:
                best_record = found_record(row, candidate, evidence, today)
                best_note = f"found via {candidate.source}"
                break
            best_note = note

        previous = existing.get(bond_code)
        previous_is_verified = bool(previous and previous.get("source") != "pending" and previous.get("sourceUrl"))
        if best_record:
            existing[bond_code] = best_record
            status = "found"
        elif previous_is_verified:
            status = "kept_existing"
            best_note = "本次未重新抓到，保留既有可靠來源"
        else:
            existing[bond_code] = pending_record(row, today)
            status = "pending"

        record = existing[bond_code]
        log_rows.append({
            "bondCode": bond_code,
            "issuerName": str(row.get("issuerName") or ""),
            "status": status,
            "summary": str(record.get("summary") or ""),
            "purposes": "、".join(record.get("purposes") or []),
            "sourceUrl": str(record.get("sourceUrl") or ""),
            "evidenceText": str(record.get("evidenceText") or ""),
            "note": best_note,
        })

    save_purposes(existing)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "bondCode",
                "issuerName",
                "status",
                "summary",
                "purposes",
                "sourceUrl",
                "evidenceText",
                "note",
            ],
        )
        writer.writeheader()
        writer.writerows(log_rows)

    found_count = sum(1 for item in log_rows if item["status"] == "found")
    print(f"Checked {len(TARGET_BOND_CODES)} CBs, found {found_count}, log: {LOG_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
