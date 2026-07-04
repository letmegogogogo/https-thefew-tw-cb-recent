#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Update CB primary-market pages from official sources only.

Purpose:
- Classify official public information into:
  1. bookbuilding_auction: 詢圈 / 競拍
  2. filing: 送件中 / 申報生效中
  3. board_approved: 董事會通過
- Do not use Fubon Excel fallback for website data.
- When official sources cannot be fetched or parsed, output empty sections and
  make the failure reason explicit in logs. This avoids mistaking "抓不到" for
  "官方真的 0 筆".
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
import ssl
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.parse import parse_qs, quote_plus, unquote, urlencode, urljoin, urlparse
from urllib.request import Request, urlopen

try:
    import certifi  # type: ignore
except Exception:  # pragma: no cover - optional dependency if already present
    certifi = None


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "outputs"
DATA_PATH = OUTPUT_DIR / "cb-primary-market-data.js"
RECENT_CB_DATA_PATH = OUTPUT_DIR / "recent-cb-data.js"
FETCH_LOG_PATH = OUTPUT_DIR / "cb-primary-market-official-fetch-log.csv"
CLASSIFY_LOG_PATH = OUTPUT_DIR / "cb-primary-market-classify-log.csv"
REMOVED_LOG_PATH = OUTPUT_DIR / "cb-primary-market-removed-log.csv"
UPDATE_LOG_PATH = OUTPUT_DIR / "cb-primary-market-update-log.csv"
TARGET_SEARCH_LOG_PATH = OUTPUT_DIR / "cb-primary-market-target-search-log.csv"
OPTIONAL_SOURCES_PATH = ROOT / "data" / "cb-primary-market-official-sources.json"
TWSA_UNDERWRITING_URL = "https://web.twsa.org.tw/Edoc2/Default.aspx?Year={year}"

TZ = timezone(timedelta(hours=8))
NOW = datetime.now(TZ).replace(microsecond=0)
TODAY = NOW.date()

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

STATUS_PRIORITY = {
    "board_approved": 1,
    "filing": 2,
    "bookbuilding_auction": 3,
    "upcoming_listing": 4,
}

SECTION_META = {
    "bookbuilding_auction": ("auction", "詢圈 / 競拍"),
    "filing": ("filing", "送件中"),
    "board_approved": ("board", "董事會通過"),
    "upcoming_listing": ("upcoming_listing", "即將掛牌 / 即將發行"),
}

OUTPUT_HEADERS = [
    "CB代碼",
    "標的名稱",
    "公司代號",
    "公司名稱",
    "債券名稱",
    "發行期間年",
    "發行金額億",
    "公告日期",
    "董事會日期",
    "送件日",
    "生效日",
    "詢圈起日",
    "詢圈迄日",
    "競拍起日",
    "競拍迄日",
    "開標日",
    "掛牌日",
    "主辦承銷商",
    "信用等級 / 擔保行",
    "詢圈 / 競拍",
    "產業別",
    "資本額億",
    "status",
    "sourceType",
    "source",
    "sourceUrl",
    "officialSourceUrl",
    "officialEvidenceText",
    "updatedAt",
    "validationStatus",
    "staleReason",
]

OFFICIAL_SOURCE_TYPES = {
    "official_mops",
    "official_twse",
    "official_tpex",
    "official_csa",
    "official_underwriting",
}

ALLOWED_SOURCE_TYPES = OFFICIAL_SOURCE_TYPES | {"needs_review", "news_verified_candidate"}

OFFICIAL_SEARCHES = [
    {
        "sourceType": "official_csa",
        "source": "證券商業同業公會承銷公告",
        "queries": [
            "site:twsa.org.tw 可轉換公司債 詢價圈購",
            "site:web2.twsa.org.tw 可轉換公司債 詢價圈購",
            "site:csa.org.tw 可轉換公司債 詢價圈購",
            "site:csa.org.tw 可轉換公司債 競價拍賣",
            "site:csa.org.tw 可轉換公司債 承銷公告",
            "site:csa.org.tw 全數詢價圈購 可轉換公司債",
        ],
    },
    {
        "sourceType": "official_twse",
        "source": "證交所公開申購及競價拍賣公告",
        "queries": [
            "site:twse.com.tw 可轉換公司債 競價拍賣 公開申購",
            "site:twse.com.tw 轉換公司債 詢價圈購 承銷公告",
            "site:twse.com.tw IPO SPO 可轉換公司債 競價拍賣",
        ],
    },
    {
        "sourceType": "official_tpex",
        "source": "櫃買中心承銷與可轉換公司債公告",
        "queries": [
            "site:tpex.org.tw 可轉換公司債 競價拍賣 詢價圈購",
            "site:tpex.org.tw 可轉換公司債 公開申購 承銷公告",
            "site:tpex.org.tw 國內可轉換公司債 申報生效",
        ],
    },
    {
        "sourceType": "official_mops",
        "source": "公開資訊觀測站重大訊息",
        "queries": [
            "site:mops.twse.com.tw 董事會決議發行國內可轉換公司債",
            "site:mops.twse.com.tw 國內無擔保轉換公司債 申報生效",
            "site:mops.twse.com.tw 募集與發行有價證券 國內可轉換公司債",
        ],
    },
]

# Optional direct official pages. Some sites are dynamic and may only expose
# links through search engines; these are still useful for debug visibility.
DIRECT_OFFICIAL_URLS = [
    {
        "sourceType": "official_twse",
        "source": "證交所 IPO/SPO 資訊揭露專區",
        "url": "https://mopsov.twse.com.tw/ipospoinform",
    },
    {
        "sourceType": "official_twse",
        "source": "證交所 IPO/SPO 資訊揭露專區",
        "url": "https://www.twse.com.tw/zh/announcement/publicForm",
    },
    {
        "sourceType": "official_twse",
        "source": "證交所市場公告",
        "url": "https://www.twse.com.tw/zh/announcement/notice",
    },
    {
        "sourceType": "official_tpex",
        "source": "櫃買中心公告",
        "url": "https://www.tpex.org.tw/",
    },
    {
        "sourceType": "official_mops",
        "source": "公開資訊觀測站",
        "url": "https://mops.twse.com.tw/mops/web/index",
    },
    {
        "sourceType": "official_underwriting",
        "source": "券商公會承銷公告",
        "url": "https://www.twsa.org.tw/",
    },
    {
        "sourceType": "official_underwriting",
        "source": "券商公會承銷公告",
        "url": "https://web2.twsa.org.tw/",
    },
]

AUCTION_KEYWORDS = [
    "詢價圈購",
    "全數詢價圈購",
    "部分詢價圈購",
    "競價拍賣",
    "競拍",
    "競拍公告",
    "承銷公告",
    "公開申購",
]

FILING_KEYWORDS = [
    "申報生效",
    "募集與發行有價證券",
    "國內可轉換公司債申報",
    "轉換公司債申報",
]

BOARD_KEYWORDS = [
    "董事會決議發行國內可轉換公司債",
    "發行國內可轉換公司債",
    "國內無擔保轉換公司債",
    "國內有擔保轉換公司債",
]

CB_KEYWORDS = [
    "可轉換公司債",
    "轉換公司債",
    "國內無擔保轉換公司債",
    "國內有擔保轉換公司債",
]

ALL_DETECT_KEYWORDS = AUCTION_KEYWORDS + FILING_KEYWORDS + BOARD_KEYWORDS + CB_KEYWORDS

FOLLOW_LINK_KEYWORDS = [
    "更多部分詢圈公告",
    "更多全數詢圈公告",
    "更多申購公告",
    "更多競拍公告",
    "競價拍賣日程表",
    "競拍公告",
    "競拍申購公告",
    "詢價圈購",
    "全數詢價圈購",
    "部分詢價圈購部分公開申購",
    "可轉換公司債",
    "轉換公司債",
    "申報生效",
    "董事會決議",
]

TARGET_DISCOVERY_KEYWORDS = [
    "36054",
    "宏致四",
    "宏致",
    "65843",
    "南俊國際三",
    "南俊國際",
    "61876",
    "萬潤六",
    "萬潤",
]

TARGET_DISCOVERY_CASES = [
    {
        "bondCode": "36054",
        "bondName": "???",
        "issuerName": "??",
        "queries": ["36054 ???", "??? ???", "?? ?????", "?? CB"],
    },
    {
        "bondCode": "65843",
        "bondName": "?????",
        "issuerName": "????",
        "queries": ["65843 ?????", "????? ???", "???? ?????", "???? CB"],
    },
    {
        "bondCode": "61876",
        "bondName": "???",
        "issuerName": "??",
        "queries": ["61876 ???", "??? ???", "?? ?????", "?? CB"],
    },
]

NEWS_SEARCH_SOURCES = [
    ("Yahoo????", "site:tw.stock.yahoo.com/news"),
    ("MoneyDJ", "site:moneydj.com"),
    ("????", "site:ctee.com.tw"),
    ("????", "site:money.udn.com"),
    ("???", "site:news.cnyes.com"),
]

NEWS_DETAIL_KEYWORDS = [
    "???",
    "???",
    "???",
    "???",
    "?????",
    "??",
    "????",
    "????",
    "????",
    "????",
    "????",
    "?????",
    "?????",
    "???",
    "CB",
]



@dataclass
class FetchResult:
    source_type: str
    source: str
    url: str
    http_status: str
    content_type: str
    title: str
    text: str
    error: str = ""


@dataclass
class Candidate:
    source_type: str
    source: str
    source_url: str
    title: str
    text: str
    status: str
    evidence: str
    row: Dict[str, str] = field(default_factory=dict)
    validation_status: str = "valid"
    stale_reason: str = ""
    remove_reason: str = ""


def fetch_url(url: str, timeout: int) -> Tuple[bytes, str, str]:
    url = re.sub(r"\s+", "", url)
    req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    contexts = []
    if certifi:
        contexts.append(ssl.create_default_context(cafile=certifi.where()))
    contexts.append(None)
    last_error: Optional[Exception] = None
    for context in contexts:
        try:
            with urlopen(req, timeout=timeout, context=context) as resp:
                raw = resp.read()
                status = str(getattr(resp, "status", "200"))
                content_type = resp.headers.get("content-type", "")
            return raw, status, content_type
        except ssl.SSLError as exc:
            last_error = exc
            continue
    if last_error:
        raise last_error
    raise RuntimeError(f"Unable to fetch {url}")


def decode_bytes(raw: bytes, content_type: str) -> str:
    if not raw:
        return ""
    charset_match = re.search(r"charset=([\w\-.]+)", content_type or "", flags=re.I)
    encodings = []
    if charset_match:
        encodings.append(charset_match.group(1))
    encodings += ["utf-8", "big5", "cp950", "latin-1"]
    candidates: List[Tuple[int, int, str]] = []
    for enc in dict.fromkeys(encodings):
        try:
            text = raw.decode(enc, errors="replace")
        except LookupError:
            continue
        replacement_count = text.count("\ufffd")
        cjk_count = len(re.findall(r"[\u4e00-\u9fff]", text))
        candidates.append((replacement_count, -cjk_count, text))
    if not candidates:
        return raw.decode("utf-8", errors="replace")
    candidates.sort(key=lambda item: (item[0], item[1]))
    return candidates[0][2]


def extract_pdf_text(raw: bytes) -> str:
    # No extra package dependency: attempt a lightweight text extraction.
    # It is not perfect, but it prevents PDF URLs from being treated as binary
    # failures and gives logs a chance to show keyword presence.
    try:
        text = raw.decode("latin-1", errors="ignore")
    except Exception:
        return ""
    text = re.sub(r"\\[rn]", " ", text)
    text = re.sub(r"[^\x20-\x7E\u4e00-\u9fff]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def clean_document(raw: bytes, content_type: str, url: str) -> Tuple[str, str]:
    lowered = (content_type or "").lower()
    if ".pdf" in url.lower() or "pdf" in lowered:
        text = extract_pdf_text(raw)
        return Path(urlparse(url).path).name or "PDF", text

    text = decode_bytes(raw, content_type)
    if "json" in lowered or text.lstrip().startswith(("{", "[")):
        try:
            data = json.loads(text)
            flat = json.dumps(data, ensure_ascii=False)
            return "JSON/API", flat
        except Exception:
            return "JSON/API", text

    title_match = re.search(r"<title[^>]*>(.*?)</title>", text, flags=re.I | re.S)
    title = html.unescape(re.sub(r"\s+", " ", title_match.group(1)).strip()) if title_match else ""
    body = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text, flags=re.I | re.S)
    body = re.sub(r"<[^>]+>", " ", body)
    body = html.unescape(body)
    body = re.sub(r"\s+", " ", body).strip()
    return title, body


class SimpleTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: List[List[str]] = []
        self._row: Optional[List[str]] = None
        self._cell: Optional[List[str]] = None

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        if tag.lower() == "tr":
            self._row = []
        elif tag.lower() in {"td", "th"} and self._row is not None:
            self._cell = []

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"td", "th"} and self._row is not None and self._cell is not None:
            value = html.unescape("".join(self._cell))
            value = re.sub(r"\s+", " ", value).strip()
            self._row.append(value)
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if any(cell for cell in self._row):
                self.rows.append(self._row)
            self._row = None


def parse_twsa_underwriting_table(html_text: str) -> List[Dict[str, str]]:
    parser = SimpleTableParser()
    parser.feed(html_text)
    rows: List[Dict[str, str]] = []
    for cells in parser.rows:
        if len(cells) < 10:
            continue
        if not re.fullmatch(r"\d{5,6}", cells[0] or ""):
            continue
        row = {
            "序號": cells[0],
            "申報日期": cells[1] if len(cells) > 1 else "",
            "主辦承銷商": cells[2] if len(cells) > 2 else "",
            "案件名稱": cells[3] if len(cells) > 3 else "",
            "方式": cells[4] if len(cells) > 4 else "",
            "發行性質": cells[5] if len(cells) > 5 else "",
            "發行種類": cells[6] if len(cells) > 6 else "",
            "配售方式一": cells[7] if len(cells) > 7 else "",
            "配售方式二": cells[8] if len(cells) > 8 else "",
            "案件狀態": cells[9] if len(cells) > 9 else "",
            "公告檔": cells[10] if len(cells) > 10 else "",
        }
        rows.append(row)
    return rows


def build_auction_candidates_from_twsa(rows: List[Dict[str, str]], source_url: str) -> List[Candidate]:
    candidates: List[Candidate] = []
    for item in rows:
        issue_nature = item.get("發行性質", "")
        issue_type = item.get("發行種類", "")
        placement = f"{item.get('配售方式一', '')} {item.get('配售方式二', '')}".strip()
        if "公司債" not in issue_nature:
            continue
        if "轉換公司債" not in issue_type:
            continue
        if not any(word in placement for word in ("詢價圈購", "競價拍賣")):
            continue

        company_name = item.get("案件名稱", "").strip()
        evidence = (
            f"{item.get('序號', '')} {item.get('申報日期', '')} {item.get('主辦承銷商', '')} "
            f"{company_name} {item.get('方式', '')} {issue_nature} {issue_type} "
            f"{placement} {item.get('案件狀態', '')}"
        )
        row = {header: "" for header in OUTPUT_HEADERS}
        row[OUTPUT_HEADERS[0]] = ""
        row[OUTPUT_HEADERS[1]] = company_name
        row[OUTPUT_HEADERS[3]] = company_name
        row[OUTPUT_HEADERS[4]] = company_name
        row[OUTPUT_HEADERS[7]] = normalize_date(item.get("申報日期", ""))
        row[OUTPUT_HEADERS[17]] = item.get("主辦承銷商", "")
        row[OUTPUT_HEADERS[19]] = placement
        row["companyName"] = company_name
        row["bondName"] = company_name
        row["announcementDate"] = row[OUTPUT_HEADERS[7]]
        row["underwriter"] = item.get("主辦承銷商", "")
        row["issueType"] = issue_type
        row["offeringMethod"] = placement
        row["caseStatus"] = item.get("案件狀態", "")
        row["status"] = "bookbuilding_auction"
        row["sourceType"] = "official_underwriting"
        row["source"] = "證券商公會承銷公告"
        row["sourceUrl"] = source_url
        row["officialSourceUrl"] = source_url
        row["officialEvidenceText"] = evidence[:320]
        row["updatedAt"] = NOW.isoformat()
        row["validationStatus"] = "needs_review"
        row["staleReason"] = "詢圈/競拍階段官方承銷公告未提供CB代碼，保留待確認"
        candidates.append(Candidate(
            source_type="official_underwriting",
            source="證券商公會承銷公告",
            source_url=source_url,
            title="證券商公會承銷公告",
            text=evidence,
            status="bookbuilding_auction",
            evidence=evidence[:320],
            row=row,
            validation_status="needs_review",
            stale_reason="詢圈/競拍階段官方承銷公告未提供CB代碼，保留待確認",
        ))
    return candidates


def fetch_twsa_underwriting_announcements(year: int, timeout: int) -> Tuple[List[Candidate], Dict[str, str], List[Dict[str, str]]]:
    source_url = TWSA_UNDERWRITING_URL.format(year=year)
    try:
        raw, http_status, content_type = fetch_url(source_url, timeout)
        text = decode_bytes(raw, content_type)
        table_rows = parse_twsa_underwriting_table(text)
        candidates = build_auction_candidates_from_twsa(table_rows, source_url)
        fetch_log = {
            "fetchedAt": NOW.isoformat(),
            "sourceType": "official_underwriting",
            "sourceUrl": source_url,
            "fetchStatus": "success" if candidates else "official_parse_failed",
            "httpStatus": http_status,
            "rawCount": str(len(table_rows)),
            "parsedCount": str(len(candidates)),
            "error": "",
        }
        classify_logs = [{
            "fetchedAt": NOW.isoformat(),
            "companyCode": "",
            "companyName": cand.row.get(OUTPUT_HEADERS[3], ""),
            "bondCode": "",
            "bondName": cand.row.get(OUTPUT_HEADERS[4], ""),
            "detectedStatus": "bookbuilding_auction",
            "finalStatus": "bookbuilding_auction",
            "sourceType": "official_underwriting",
            "sourceUrl": source_url,
            "evidenceText": cand.row.get("officialEvidenceText", ""),
            "validationStatus": cand.validation_status,
            "reason": "twsa_underwriting_cb_auction",
        } for cand in candidates]
        return candidates, fetch_log, classify_logs
    except Exception as exc:  # noqa: BLE001
        return [], {
            "fetchedAt": NOW.isoformat(),
            "sourceType": "official_underwriting",
            "sourceUrl": source_url,
            "fetchStatus": "official_fetch_failed",
            "httpStatus": "",
            "rawCount": "0",
            "parsedCount": "0",
            "error": str(exc)[:260],
        }, []


def build_candidate_from_official_notice(
    *,
    status: str,
    source_type: str,
    source: str,
    source_url: str,
    company_code: str,
    company_name: str,
    subject: str,
    announcement_date: str,
    evidence: str,
) -> Candidate:
    row = {header: "" for header in OUTPUT_HEADERS}
    row[OUTPUT_HEADERS[1]] = company_name
    row[OUTPUT_HEADERS[2]] = company_code
    row[OUTPUT_HEADERS[3]] = company_name
    row[OUTPUT_HEADERS[4]] = subject or company_name
    row[OUTPUT_HEADERS[7]] = announcement_date
    if status == "board_approved":
        row[OUTPUT_HEADERS[8]] = announcement_date
        row["boardDate"] = announcement_date
    elif status == "filing":
        row[OUTPUT_HEADERS[9]] = announcement_date
        row["filingDate"] = announcement_date
    row["companyCode"] = company_code
    row["companyName"] = company_name
    row["bondName"] = subject or company_name
    row["announcementDate"] = announcement_date
    row["issueType"] = "國內可轉換公司債"
    row["status"] = status
    row["sourceType"] = source_type
    row["source"] = source
    row["sourceUrl"] = source_url
    row["officialSourceUrl"] = source_url
    row["officialEvidenceText"] = evidence[:320]
    row["updatedAt"] = NOW.isoformat()
    row["validationStatus"] = "needs_review"
    row["staleReason"] = "官方公告未提供CB代碼，保留待確認"
    return Candidate(
        source_type=source_type,
        source=source,
        source_url=source_url,
        title=subject,
        text=evidence,
        status=status,
        evidence=evidence[:320],
        row=row,
        validation_status="needs_review",
        stale_reason="官方公告未提供CB代碼，保留待確認",
    )


def parse_mops_notice_candidates(html_text: str, status: str, source_url: str, keyword: str) -> List[Candidate]:
    if "FOR SECURITY REASONS" in html_text or "安全性考量" in html_text:
        return []
    parser = SimpleTableParser()
    parser.feed(html_text)
    candidates: List[Candidate] = []
    for cells in parser.rows:
        joined = " ".join(cells)
        if not contains_any(joined, CB_KEYWORDS):
            continue
        if keyword and keyword not in joined and not contains_any(joined, BOARD_KEYWORDS + FILING_KEYWORDS):
            continue
        company_code = ""
        company_name = ""
        announcement_date = ""
        for cell in cells:
            if not company_code:
                m = re.search(r"(?<!\d)(\d{4})(?!\d)", cell)
                if m:
                    company_code = m.group(1)
            if not announcement_date:
                announcement_date = normalize_date(cell)
        for cell in cells:
            if re.search(r"[\u4e00-\u9fff]{2,}", cell) and not contains_any(cell, ALL_DETECT_KEYWORDS):
                company_name = cell.strip()
                break
        subject = next((cell for cell in cells if contains_any(cell, ALL_DETECT_KEYWORDS)), joined[:80])
        candidates.append(build_candidate_from_official_notice(
            status=status,
            source_type="official_mops",
            source="公開資訊觀測站重大訊息",
            source_url=source_url,
            company_code=company_code,
            company_name=company_name,
            subject=subject,
            announcement_date=announcement_date,
            evidence=joined,
        ))
    return candidates


def fetch_mops_material_information(kind: str, keyword: str, status: str, timeout: int) -> Tuple[List[Candidate], Dict[str, str]]:
    source_url = "https://mops.twse.com.tw/mops/web/ajax_t05st01"
    roc_year = NOW.year - 1911
    body = {
        "encodeURIComponent": "1",
        "step": "1",
        "firstin": "1",
        "off": "1",
        "keyword4": keyword,
        "code1": "",
        "TYPEK2": "",
        "checkbtn": "",
        "queryName": "co_id",
        "inpuType": "co_id",
        "TYPEK": "all",
        "co_id": "",
        "year": str(roc_year),
        "month": f"{NOW.month:02d}",
        "b_date": f"{roc_year}/01/01",
        "e_date": f"{roc_year}/{NOW.month:02d}/{NOW.day:02d}",
    }
    data = urlencode(body).encode("utf-8")
    try:
        req = Request(
            source_url,
            data=data,
            headers={
                "User-Agent": USER_AGENT,
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": "https://mops.twse.com.tw/mops/web/t05st01",
            },
        )
        contexts = []
        if certifi:
            contexts.append(ssl.create_default_context(cafile=certifi.where()))
        contexts.append(None)
        raw = b""
        http_status = ""
        content_type = ""
        last_error: Optional[Exception] = None
        for context in contexts:
            try:
                with urlopen(req, timeout=timeout, context=context) as resp:
                    raw = resp.read()
                    http_status = str(getattr(resp, "status", "200"))
                    content_type = resp.headers.get("content-type", "")
                break
            except ssl.SSLError as exc:
                last_error = exc
                continue
        if not raw and last_error:
            raise last_error
        text = decode_bytes(raw, content_type)
        candidates = parse_mops_notice_candidates(text, status, source_url, keyword)
        blocked = "FOR SECURITY REASONS" in text or "安全性考量" in text
        fetch_status = "success" if candidates else ("official_fetch_failed" if blocked else "official_parse_failed")
        error = "mops_security_blocked" if blocked else ""
        return candidates, {
            "fetchedAt": NOW.isoformat(),
            "sourceType": "official_mops",
            "sourceUrl": source_url,
            "fetchStatus": fetch_status,
            "httpStatus": http_status,
            "rawCount": "1" if text else "0",
            "parsedCount": str(len(candidates)),
            "error": f"{kind}:{error}".strip(":"),
        }
    except Exception as exc:  # noqa: BLE001
        return [], {
            "fetchedAt": NOW.isoformat(),
            "sourceType": "official_mops",
            "sourceUrl": source_url,
            "fetchStatus": "official_fetch_failed",
            "httpStatus": "",
            "rawCount": "0",
            "parsedCount": "0",
            "error": f"{kind}:{str(exc)[:220]}",
        }


def official_source_type_for_url(url: str, default: str) -> Optional[str]:
    host = urlparse(url).netloc.lower()
    if "mops.twse.com.tw" in host:
        return "official_mops"
    if "mopsov.twse.com.tw" in host or "twse.com.tw" in host:
        return "official_twse"
    if "tpex.org.tw" in host:
        return "official_tpex"
    if "csa.org.tw" in host:
        return "official_csa"
    if "twsa.org.tw" in host:
        return "official_underwriting"
    if default in OFFICIAL_SOURCE_TYPES:
        return default
    return None


def extract_follow_links(markup: str, base_url: str, limit: int = 30) -> List[Tuple[str, str]]:
    """Find likely official next-layer announcement/list URLs from an entrance page."""
    links: List[Tuple[str, str]] = []
    seen = set()
    for match in re.finditer(r"<a\b([^>]*)>(.*?)</a>", markup, flags=re.I | re.S):
        attrs, label_html = match.groups()
        href_match = re.search(r'href\s*=\s*["\']([^"\']+)["\']', attrs, flags=re.I)
        if not href_match:
            continue
        label = html.unescape(re.sub(r"<[^>]+>", " ", label_html))
        label = re.sub(r"\s+", " ", label).strip()
        href = html.unescape(href_match.group(1)).strip()
        if not href or href.startswith(("javascript:", "#", "mailto:")):
            continue
        combined = f"{label} {href}"
        if not contains_any(combined, FOLLOW_LINK_KEYWORDS):
            continue
        url = urljoin(base_url, href)
        if not official_source_type_for_url(url, ""):
            continue
        if url in seen:
            continue
        seen.add(url)
        links.append((url, label or href))
        if len(links) >= limit:
            break
    return links


def candidate_segments(text: str) -> List[str]:
    """Split a listing page into small announcement-like segments."""
    if not contains_any(text, ALL_DETECT_KEYWORDS):
        return []
    pieces = re.split(r"(?=公告|(?<!\d)\d{3,4}[./\-/年]\d{1,2}[./\-/月]\d{1,2}|董事會|申報生效|詢價圈購|競價拍賣)", text)
    segments = []
    for piece in pieces:
        piece = piece.strip()
        if len(piece) < 20:
            continue
        if contains_any(piece, ALL_DETECT_KEYWORDS) and contains_any(piece, CB_KEYWORDS):
            segments.append(piece[:1200])
        if len(segments) >= 20:
            break
    return segments


def duckduckgo_urls(query: str, limit: int, timeout: int) -> Tuple[List[str], str]:
    search_pages = [("duckduckgo", f"https://duckduckgo.com/html/?q={quote_plus(query)}")]
    if any(domain in query for _name, domain in NEWS_SEARCH_SOURCES):
        search_pages.append(("bing", f"https://www.bing.com/search?q={quote_plus(query)}"))
    last_error = ""
    for engine, url in search_pages:
        try:
            raw, http_status, content_type = fetch_url(url, min(timeout, 3))
        except Exception as exc:  # noqa: BLE001
            last_error = f"{engine}:{str(exc)[:120]}"
            continue
        text = decode_bytes(raw, content_type)
        urls: List[str] = []
        for href in re.findall(r'href=["\']([^"\']+)["\']', text):
            href = html.unescape(href)
            if "uddg=" in href:
                qs = parse_qs(urlparse(href).query)
                href = unquote(qs.get("uddg", [""])[0])
            if not href.startswith("http"):
                continue
            host = urlparse(href).netloc.lower()
            if any(blocked in host for blocked in ("duckduckgo.com", "bing.com", "microsoft.com")):
                continue
            if href not in urls:
                urls.append(href)
            if len(urls) >= limit:
                break
        if urls or engine == "bing":
            return urls, f"{engine}:{http_status}"
    raise RuntimeError(last_error or "search_failed")



def source_name_for_news_url(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if "tw.stock.yahoo.com" in host:
        return "Yahoo????"
    if "moneydj.com" in host:
        return "MoneyDJ"
    if "ctee.com.tw" in host:
        return "????"
    if "money.udn.com" in host or "udn.com" in host:
        return "????"
    if "cnyes.com" in host:
        return "???"
    return host or "????"


def target_identity_matched(text: str, target: Dict[str, str]) -> bool:
    compact = re.sub(r"\s+", "", text)
    return any(
        value and re.sub(r"\s+", "", value) in compact
        for value in (target.get("bondCode", ""), target.get("bondName", ""), target.get("issuerName", ""))
    )


def target_detail_matched(text: str) -> bool:
    return contains_any(text, CB_KEYWORDS) and contains_any(text, NEWS_DETAIL_KEYWORDS)


def snippet_for_target(text: str, target: Dict[str, str]) -> str:
    keys = [target.get("bondCode", ""), target.get("bondName", ""), target.get("issuerName", "")] + NEWS_DETAIL_KEYWORDS
    positions = [text.find(k) for k in keys if k and text.find(k) >= 0]
    if not positions:
        return text[:260].strip()
    pos = min(positions)
    return text[max(0, pos - 90): min(len(text), pos + 360)].strip()


def make_news_candidate(target: Dict[str, str], source_name: str, url: str, title: str, text: str) -> Candidate:
    evidence = snippet_for_target(f"{title} {text}", target)[:320]
    row = {
        "CB??": target.get("bondCode", ""),
        "????": target.get("bondName", "") or target.get("issuerName", ""),
        "????": "",
        "????": target.get("issuerName", ""),
        "????": target.get("bondName", ""),
        "?????": number_near(text, ["????", "????"]),
        "?????": number_near(text, ["????", "????", "?????"]),
        "????": date_near(text, ["????", "????", "??"]),
        "?????": "",
        "???": "",
        "???": "",
        "????": date_near(text, ["??????", "????", "????"]),
        "????": "",
        "????": date_near(text, ["??????", "????", "????"]),
        "????": "",
        "???": date_near(text, ["???", "??"]),
        "???": date_near(text, ["???", "???", "???", "?????", "???"]),
        "?????": "",
        "???? / ???": "",
        "?? / ??": "",
        "???": "",
        "????": "",
        "status": "upcoming_listing",
        "sourceType": "news_verified_candidate",
        "source": f"{source_name}????????????",
        "sourceUrl": url,
        "officialSourceUrl": "",
        "officialEvidenceText": evidence,
        "updatedAt": NOW.isoformat(),
        "validationStatus": "needs_review",
        "staleReason": "??????CB????????????URL??????",
    }
    return Candidate(
        source_type="news_verified_candidate",
        source=row["source"],
        source_url=url,
        title=title,
        text=text,
        status="upcoming_listing",
        evidence=evidence,
        row=row,
        validation_status="needs_review",
        stale_reason=row["staleReason"],
    )


def discover_target_news_clues(args, inspect_url_func) -> Tuple[List[Candidate], List[Dict[str, str]], List[str]]:
    candidates: List[Candidate] = []
    logs: List[Dict[str, str]] = []
    debug_lines: List[str] = []
    seen_news_urls = set()
    started_at = datetime.now(TZ)
    max_news_seconds = 45
    for target in TARGET_DISCOVERY_CASES:
        for base_query in target["queries"][:1]:
            for source_name, domain_query in NEWS_SEARCH_SOURCES:
                if (datetime.now(TZ) - started_at).total_seconds() > max_news_seconds:
                    logs.append({
                        "checkedAt": NOW.isoformat(),
                        "targetKeyword": base_query,
                        "sourceName": source_name,
                        "sourceUrl": f"news-search:{domain_query} {base_query}",
                        "matchedTitle": "",
                        "matchedText": "",
                        "matchedBondCode": target.get("bondCode", ""),
                        "matchedBondName": target.get("bondName", ""),
                        "matchedIssuerName": target.get("issuerName", ""),
                        "candidateAction": "no_match",
                        "reason": "news_search_time_budget_exceeded",
                    })
                    continue
                query = f"{domain_query} {base_query}"
                try:
                    urls, http_status = duckduckgo_urls(query, args.search_limit, args.timeout)
                except Exception as exc:  # noqa: BLE001
                    logs.append({
                        "checkedAt": NOW.isoformat(),
                        "targetKeyword": base_query,
                        "sourceName": source_name,
                        "sourceUrl": f"news-search:{query}",
                        "matchedTitle": "",
                        "matchedText": "",
                        "matchedBondCode": target.get("bondCode", ""),
                        "matchedBondName": target.get("bondName", ""),
                        "matchedIssuerName": target.get("issuerName", ""),
                        "candidateAction": "no_match",
                        "reason": f"search_failed:{str(exc)[:180]}",
                    })
                    continue
                matched_any = False
                for url in urls:
                    if url in seen_news_urls:
                        continue
                    seen_news_urls.add(url)
                    try:
                        raw, page_status, content_type = fetch_url(url, args.timeout)
                        title, page_text = clean_document(raw, content_type, url)
                        combined = f"{title} {page_text}"
                        if not target_identity_matched(combined, target):
                            logs.append({
                                "checkedAt": NOW.isoformat(),
                                "targetKeyword": base_query,
                                "sourceName": source_name_for_news_url(url) or source_name,
                                "sourceUrl": url,
                                "matchedTitle": title[:120],
                                "matchedText": snippet_for_target(combined, target)[:220],
                                "matchedBondCode": "",
                                "matchedBondName": "",
                                "matchedIssuerName": "",
                                "candidateAction": "no_match",
                                "reason": "identity_not_matched",
                            })
                            continue
                        official_links = [child for child, _label in extract_follow_links(decode_bytes(raw, content_type), url, args.follow_link_limit) if official_source_type_for_url(child)]
                        if official_links:
                            matched_any = True
                            for official_url in official_links[:3]:
                                logs.append({
                                    "checkedAt": NOW.isoformat(),
                                    "targetKeyword": base_query,
                                    "sourceName": source_name_for_news_url(url) or source_name,
                                    "sourceUrl": url,
                                    "matchedTitle": title[:120],
                                    "matchedText": snippet_for_target(combined, target)[:220],
                                    "matchedBondCode": target.get("bondCode", ""),
                                    "matchedBondName": target.get("bondName", ""),
                                    "matchedIssuerName": target.get("issuerName", ""),
                                    "candidateAction": "official_url_found",
                                    "reason": official_url,
                                })
                                inspect_url_func(official_source_type_for_url(official_url) or "official_mops", f"???????? / {base_query}", official_url)
                            continue
                        if target_detail_matched(combined):
                            matched_any = True
                            candidates.append(make_news_candidate(target, source_name_for_news_url(url) or source_name, url, title, page_text))
                            logs.append({
                                "checkedAt": NOW.isoformat(),
                                "targetKeyword": base_query,
                                "sourceName": source_name_for_news_url(url) or source_name,
                                "sourceUrl": url,
                                "matchedTitle": title[:120],
                                "matchedText": snippet_for_target(combined, target)[:220],
                                "matchedBondCode": target.get("bondCode", ""),
                                "matchedBondName": target.get("bondName", ""),
                                "matchedIssuerName": target.get("issuerName", ""),
                                "candidateAction": "news_verified_candidate",
                                "reason": "news_contains_identity_and_cb_details_no_official_url",
                            })
                        else:
                            logs.append({
                                "checkedAt": NOW.isoformat(),
                                "targetKeyword": base_query,
                                "sourceName": source_name_for_news_url(url) or source_name,
                                "sourceUrl": url,
                                "matchedTitle": title[:120],
                                "matchedText": snippet_for_target(combined, target)[:220],
                                "matchedBondCode": target.get("bondCode", ""),
                                "matchedBondName": target.get("bondName", ""),
                                "matchedIssuerName": target.get("issuerName", ""),
                                "candidateAction": "needs_review",
                                "reason": "identity_matched_but_no_explicit_cb_detail",
                            })
                    except Exception as exc:  # noqa: BLE001
                        logs.append({
                            "checkedAt": NOW.isoformat(),
                            "targetKeyword": base_query,
                            "sourceName": source_name,
                            "sourceUrl": url,
                            "matchedTitle": "",
                            "matchedText": "",
                            "matchedBondCode": target.get("bondCode", ""),
                            "matchedBondName": target.get("bondName", ""),
                            "matchedIssuerName": target.get("issuerName", ""),
                            "candidateAction": "no_match",
                            "reason": f"fetch_failed:{str(exc)[:180]}",
                        })
                if not matched_any:
                    logs.append({
                        "checkedAt": NOW.isoformat(),
                        "targetKeyword": base_query,
                        "sourceName": source_name,
                        "sourceUrl": f"news-search:{query}",
                        "matchedTitle": "",
                        "matchedText": "",
                        "matchedBondCode": target.get("bondCode", ""),
                        "matchedBondName": target.get("bondName", ""),
                        "matchedIssuerName": target.get("issuerName", ""),
                        "candidateAction": "no_match",
                        "reason": f"search_http={http_status}; no_verified_news_clue",
                    })
                debug_lines.append(f"[news-clue] {source_name} query={base_query} urls={len(urls)} matched={matched_any}")
    return candidates, logs, debug_lines

def contains_any(text: str, keywords: Iterable[str]) -> bool:
    return any(k in text for k in keywords)


def detect_status(text: str) -> Optional[str]:
    if contains_any(text, AUCTION_KEYWORDS) and contains_any(text, CB_KEYWORDS):
        return "bookbuilding_auction"
    if contains_any(text, FILING_KEYWORDS) and contains_any(text, CB_KEYWORDS):
        return "filing"
    if contains_any(text, BOARD_KEYWORDS) and contains_any(text, CB_KEYWORDS):
        return "board_approved"
    return None


def evidence_snippet(text: str) -> str:
    positions = [text.find(k) for k in ALL_DETECT_KEYWORDS if text.find(k) >= 0]
    if not positions:
        return ""
    pos = min(positions)
    start = max(0, pos - 90)
    end = min(len(text), pos + 280)
    return text[start:end].strip()


def normalize_date(value: str) -> str:
    if not value:
        return ""
    m = re.search(r"(\d{2,4})[./\-/年](\d{1,2})[./\-/月](\d{1,2})", value)
    if not m:
        return ""
    y, month, day = map(int, m.groups())
    if y < 1911:
        y += 1911
    try:
        return date(y, month, day).isoformat()
    except ValueError:
        return ""


def date_near(text: str, labels: Iterable[str], window: int = 120) -> str:
    for label in labels:
        idx = text.find(label)
        if idx < 0:
            continue
        parsed = normalize_date(text[idx : idx + window])
        if parsed:
            return parsed
    return ""


def number_near(text: str, labels: Iterable[str]) -> str:
    for label in labels:
        idx = text.find(label)
        if idx < 0:
            continue
        seg = text[idx : idx + 100]
        m = re.search(r"([\d,]+(?:\.\d+)?)\s*(億|萬元|元)?", seg)
        if not m:
            continue
        value = float(m.group(1).replace(",", ""))
        unit = m.group(2) or ""
        if unit == "元":
            value /= 100_000_000
        elif unit == "萬元":
            value /= 10_000
        return f"{value:g}"
    return ""


def extract_company_code(text: str) -> str:
    for pattern in (
        r"(?:公司代號|股票代號|證券代號)[：:\s]*(\d{4})",
        r"\((\d{4})\)",
    ):
        m = re.search(pattern, text)
        if m:
            return m.group(1)
    return ""


def extract_bond_code(text: str) -> str:
    for code in re.findall(r"(?<!\d)(\d{5,6})(?!\d)", text):
        if re.match(r"20\d{4}$", code):
            continue
        return code
    return ""


def extract_company_name(title: str, text: str) -> str:
    for src in (title, text[:500]):
        patterns = [
            r"(?:公告|代公告|補充公告)[：:\s]*(?:本公司)?([一-龥A-Za-z0-9\-]{2,14})(?:董事會|發行|國內|申報|可轉換公司債)",
            r"([一-龥A-Za-z0-9\-]{2,14})(?:董事會決議發行|發行國內|國內無擔保轉換公司債|國內有擔保轉換公司債)",
        ]
        for pattern in patterns:
            m = re.search(pattern, src)
            if m:
                name = m.group(1).strip("：: -　")
                if name and not any(bad in name for bad in ("公開資訊", "重大訊息", "有價證券", "公司債")):
                    return name
    return ""


def extract_bond_name(title: str, text: str) -> str:
    for src in (title, text[:800]):
        m = re.search(r"([一-龥A-Za-z0-9\-]{2,12}(?:一|二|三|四|五|六|七|八|九|十|十一|十二|[0-9]+))", src)
        if m:
            name = m.group(1)
            if name.isdigit() or re.fullmatch(r"\d{2,4}年?", name):
                continue
            return name
    return ""


def is_generic_portal_page(result: FetchResult) -> bool:
    parsed = urlparse(result.url)
    path = (parsed.path or "/").rstrip("/") or "/"
    generic_titles = ["全球資訊網", "證券櫃檯買賣中心", "公開資訊觀測站", "index"]
    if path == "/" and any(title in result.title for title in generic_titles):
        return True
    if path == "/" and not parsed.query:
        return True
    return False


def candidate_from_fetch(result: FetchResult) -> Optional[Candidate]:
    if is_generic_portal_page(result):
        return None
    combined = f"{result.title} {result.text}"
    status = detect_status(combined)
    evidence = evidence_snippet(combined)
    if not status or not evidence:
        return None
    if not contains_any(evidence, CB_KEYWORDS):
        return None

    source_type = official_source_type_for_url(result.url, result.source_type)
    if not source_type:
        return None

    company_name = extract_company_name(result.title, result.text)
    bond_name = extract_bond_name(result.title, result.text)
    row = {
        "CB代碼": extract_bond_code(combined),
        "標的名稱": company_name or bond_name,
        "公司代號": extract_company_code(combined),
        "公司名稱": company_name,
        "債券名稱": bond_name,
        "發行期間年": number_near(combined, ["發行期間", "發行年限"]),
        "發行金額億": number_near(combined, ["發行總額", "發行金額", "募集總金額"]),
        "公告日期": date_near(combined, ["公告日期", "發言日期", "日期"]),
        "董事會日期": date_near(combined, ["董事會日期", "決議日期", "董事會"]),
        "送件日": date_near(combined, ["送件日", "申報日期", "送件"]),
        "生效日": date_near(combined, ["生效日", "申報生效"]),
        "詢圈起日": date_near(combined, ["詢價圈購期間", "詢圈期間", "詢價圈購"]),
        "詢圈迄日": "",
        "競拍起日": date_near(combined, ["競價拍賣期間", "競拍期間", "競價拍賣"]),
        "競拍迄日": "",
        "開標日": date_near(combined, ["開標日", "開標"]),
        "掛牌日": date_near(combined, ["掛牌日", "上市日", "上櫃日", "開始買賣日"]),
        "主辦承銷商": "",
        "信用等級 / 擔保行": "",
        "詢圈 / 競拍": "詢圈 / 競拍" if status == "bookbuilding_auction" else "",
        "產業別": "",
        "資本額億": "",
        "status": status,
        "sourceType": source_type,
        "source": result.source,
        "sourceUrl": result.url,
        "officialSourceUrl": result.url,
        "officialEvidenceText": evidence[:320],
        "updatedAt": NOW.isoformat(),
        "validationStatus": "valid",
        "staleReason": "",
    }
    return Candidate(
        source_type=source_type,
        source=result.source,
        source_url=result.url,
        title=result.title,
        text=result.text,
        status=status,
        evidence=evidence[:320],
        row=row,
    )


def candidates_from_fetch(result: FetchResult) -> List[Candidate]:
    """Parse one official page/list into zero or more candidate records."""
    candidates: List[Candidate] = []
    whole = candidate_from_fetch(result)
    if whole:
        candidates.append(whole)

    for idx, segment in enumerate(candidate_segments(result.text)):
        segment_result = FetchResult(
            source_type=result.source_type,
            source=result.source,
            url=result.url,
            http_status=result.http_status,
            content_type=result.content_type,
            title=f"{result.title} #{idx + 1}",
            text=segment,
        )
        cand = candidate_from_fetch(segment_result)
        if cand and candidate_key(cand) not in {candidate_key(existing) for existing in candidates}:
            candidates.append(cand)
    return candidates


def load_optional_direct_sources() -> List[Dict[str, str]]:
    if not OPTIONAL_SOURCES_PATH.exists():
        return []
    try:
        data = json.loads(OPTIONAL_SOURCES_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []
    items = data if isinstance(data, list) else data.get("sources", [])
    out = []
    for item in items:
        source_type = str(item.get("sourceType", "")).strip()
        url = str(item.get("url", "")).strip()
        if source_type in OFFICIAL_SOURCE_TYPES and url:
            out.append({
                "sourceType": source_type,
                "source": str(item.get("source") or source_type),
                "url": url,
            })
    return out


def debug_line(result: FetchResult, parsed_count: int, reason: str) -> str:
    combined = f"{result.title} {result.text}"
    flags = [kw for kw in ["可轉換公司債", "詢價圈購", "競價拍賣", "申報生效", "董事會決議"] if kw in combined]
    title = result.title or Path(urlparse(result.url).path).name or "(no title)"
    return (
        f"[{result.source_type}] {result.http_status} parsed={parsed_count} "
        f"keywords={','.join(flags) or '-'} title={title[:80]} url={result.url} reason={reason}"
    )


def fetch_official_sources(args: argparse.Namespace) -> Tuple[List[Candidate], List[Dict[str, str]], List[str], int, int]:
    fetch_logs: List[Dict[str, str]] = []
    target_search_logs: List[Dict[str, str]] = []
    candidates: List[Candidate] = []
    debug_lines: List[str] = []
    seen_urls = set()
    official_pages_success = 0
    official_raw_count = 0

    def inspect_url(source_type: str, source: str, url: str, depth: int = 0) -> None:
        nonlocal official_pages_success
        if url in seen_urls:
            return
        url = re.sub(r"\s+", "", url)
        seen_urls.add(url)
        if not official_source_type_for_url(url, source_type):
            return
        http_status = ""
        try:
            raw, http_status, content_type = fetch_url(url, args.timeout)
            title, text = clean_document(raw, content_type, url)
            raw_text = decode_bytes(raw, content_type)
            result = FetchResult(
                source_type=official_source_type_for_url(url, source_type) or source_type,
                source=source,
                url=url,
                http_status=http_status,
                content_type=content_type,
                title=title,
                text=text,
            )
            official_pages_success += 1
            page_candidates = candidates_from_fetch(result)
            parsed_count = len(page_candidates)
            candidates.extend(page_candidates)
            follow_links = extract_follow_links(raw_text, url, args.follow_link_limit) if depth < 1 else []
            reason = "parsed" if page_candidates else "official_parse_failed_or_no_cb_keywords"
            fetch_logs.append({
                "fetchedAt": NOW.isoformat(),
                "sourceType": result.source_type,
                "sourceUrl": url,
                "fetchStatus": "success" if page_candidates else "official_parse_failed",
                "httpStatus": http_status,
                "rawCount": "1",
                "parsedCount": str(parsed_count),
                "error": f"followLinks={len(follow_links)}" if follow_links else "",
            })
            debug_lines.append(debug_line(result, parsed_count, f"{reason}; followLinks={len(follow_links)}"))
            for child_url, label in follow_links:
                debug_lines.append(
                    f"[{result.source_type}] entrance={url} nextLayer={child_url} label={label[:80]}"
                )
                inspect_url(result.source_type, f"{source} / {label[:40]}", child_url, depth + 1)
        except Exception as exc:  # noqa: BLE001
            fetch_logs.append({
                "fetchedAt": NOW.isoformat(),
                "sourceType": source_type,
                "sourceUrl": url,
                "fetchStatus": "official_fetch_failed",
                "httpStatus": http_status,
                "rawCount": "0",
                "parsedCount": "0",
                "error": str(exc)[:260],
            })
            debug_lines.append(
                f"[{source_type}] failed parsed=0 keywords=- title=- url={url} reason={str(exc)[:160]}"
            )

    twsa_candidates, twsa_log, _twsa_classify_logs = fetch_twsa_underwriting_announcements(NOW.year, args.timeout)
    fetch_logs.append(twsa_log)
    candidates.extend(twsa_candidates)
    seen_urls.add(twsa_log["sourceUrl"])
    official_raw_count += int(twsa_log.get("rawCount") or 0)
    if twsa_log.get("fetchStatus") != "official_fetch_failed":
        official_pages_success += 1
    debug_lines.append(
        f"[official_underwriting] {twsa_log.get('httpStatus') or '-'} "
        f"raw={twsa_log.get('rawCount')} parsed={twsa_log.get('parsedCount')} "
        f"title=115年－承銷公告 url={twsa_log.get('sourceUrl')} reason=twsa_underwriting_table"
    )

    for kind, keyword, status in [
        ("mops_board_approved", "董事會決議發行國內可轉換公司債", "board_approved"),
        ("mops_filing", "申報生效 國內可轉換公司債", "filing"),
    ]:
        mops_candidates, mops_log = fetch_mops_material_information(kind, keyword, status, args.timeout)
        fetch_logs.append(mops_log)
        candidates.extend(mops_candidates)
        official_raw_count += int(mops_log.get("rawCount") or 0)
        if mops_log.get("fetchStatus") != "official_fetch_failed":
            official_pages_success += 1
        debug_lines.append(
            f"[official_mops] {mops_log.get('httpStatus') or '-'} "
            f"raw={mops_log.get('rawCount')} parsed={mops_log.get('parsedCount')} "
            f"kind={kind} status={mops_log.get('fetchStatus')} reason={mops_log.get('error')}"
        )

    for item in DIRECT_OFFICIAL_URLS + load_optional_direct_sources():
        inspect_url(item["sourceType"], item["source"], item["url"])

    for source_group in OFFICIAL_SEARCHES:
        for query in source_group["queries"][: args.queries_per_source]:
            search_url = f"search:{query}"
            try:
                urls, http_status = duckduckgo_urls(query, args.search_limit, args.timeout)
                official_urls = [
                    u for u in urls if official_source_type_for_url(u, source_group["sourceType"])
                ]
                fetch_logs.append({
                    "fetchedAt": NOW.isoformat(),
                    "sourceType": source_group["sourceType"],
                    "sourceUrl": search_url,
                    "fetchStatus": "search_success",
                    "httpStatus": http_status,
                    "rawCount": str(len(official_urls)),
                    "parsedCount": "0",
                    "error": "",
                })
                debug_lines.append(
                    f"[{source_group['sourceType']}] search {http_status} officialUrls={len(official_urls)} query={query}"
                )
            except Exception as exc:  # noqa: BLE001
                fetch_logs.append({
                    "fetchedAt": NOW.isoformat(),
                    "sourceType": source_group["sourceType"],
                    "sourceUrl": search_url,
                    "fetchStatus": "official_fetch_failed",
                    "httpStatus": "",
                    "rawCount": "0",
                    "parsedCount": "0",
                    "error": str(exc)[:260],
                })
                debug_lines.append(
                    f"[{source_group['sourceType']}] search failed officialUrls=0 query={query} reason={str(exc)[:160]}"
                )
                continue
            for url in official_urls:
                inspect_url(source_group["sourceType"], source_group["source"], url)

    target_domains = [
        ("official_underwriting", "證券商公會承銷公告", "site:web.twsa.org.tw OR site:twsa.org.tw"),
        ("official_twse", "證交所 / MOPS IPO SPO 公告", "site:mopsov.twse.com.tw OR site:twse.com.tw"),
        ("official_tpex", "櫃買中心承銷與掛牌公告", "site:tpex.org.tw"),
        ("official_mops", "公開資訊觀測站公告", "site:mops.twse.com.tw OR site:mopsov.twse.com.tw"),
    ]
    for keyword in TARGET_DISCOVERY_KEYWORDS:
        for source_type, source, domain_query in target_domains:
            query = f"{domain_query} {keyword} 轉換公司債 可轉換公司債"
            search_url = f"target-search:{query}"
            try:
                urls, http_status = duckduckgo_urls(query, args.search_limit, args.timeout)
                official_urls = [
                    u for u in urls if official_source_type_for_url(u, source_type)
                ]
                fetch_logs.append({
                    "fetchedAt": NOW.isoformat(),
                    "sourceType": source_type,
                    "sourceUrl": search_url,
                    "fetchStatus": "search_success",
                    "httpStatus": http_status,
                    "rawCount": str(len(official_urls)),
                    "parsedCount": "0",
                    "error": f"keyword={keyword}",
                })
                debug_lines.append(
                    f"[{source_type}] target-search {http_status} officialUrls={len(official_urls)} keyword={keyword}"
                )
            except Exception as exc:  # noqa: BLE001
                fetch_logs.append({
                    "fetchedAt": NOW.isoformat(),
                    "sourceType": source_type,
                    "sourceUrl": search_url,
                    "fetchStatus": "official_fetch_failed",
                    "httpStatus": "",
                    "rawCount": "0",
                    "parsedCount": "0",
                    "error": f"keyword={keyword}:{str(exc)[:220]}",
                })
                continue
            for url in official_urls:
                inspect_url(source_type, f"{source} / {keyword}", url)

    news_candidates, news_logs, news_debug = discover_target_news_clues(args, inspect_url)
    candidates.extend(news_candidates)
    target_search_logs.extend(news_logs)
    debug_lines.extend(news_debug)

    return candidates, fetch_logs, target_search_logs, debug_lines, official_pages_success, official_raw_count + len(seen_urls)


def load_recent_cb_codes() -> set:
    if not RECENT_CB_DATA_PATH.exists():
        return set()
    text = RECENT_CB_DATA_PATH.read_text(encoding="utf-8", errors="ignore")
    return set(re.findall(r'"(?:bondCode|code)"\s*:\s*"([^"]+)"', text))


def parse_date(value: str) -> Optional[date]:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def normalize_key(row: Dict[str, str]) -> str:
    bond_code = row.get(OUTPUT_HEADERS[0], "") or row.get("bondCode", "")
    company_code = row.get(OUTPUT_HEADERS[2], "") or row.get("companyCode", "")
    bond_name = row.get(OUTPUT_HEADERS[4], "") or row.get("bondName", "")
    company_name = row.get(OUTPUT_HEADERS[3], "") or row.get("companyName", "") or row.get(OUTPUT_HEADERS[1], "")
    issue_amount = row.get(OUTPUT_HEADERS[6], "") or row.get("issueAmount", "")
    announcement_date = (
        row.get("boardDate", "")
        or row.get("filingDate", "")
        or row.get("announcementDate", "")
        or row.get(OUTPUT_HEADERS[7], "")
    )
    issue_type = row.get("issueType", "") or row.get(OUTPUT_HEADERS[19], "")
    if bond_code:
        return f"bond:{bond_code}"
    if company_code and bond_name:
        return f"company_bond:{company_code}:{bond_name}"
    if company_name and issue_amount and announcement_date:
        return f"company_amount_date:{company_name}:{issue_amount}:{announcement_date}"
    return f"company_type_date:{company_name}:{issue_type}:{announcement_date}"


def candidate_key(cand: Candidate) -> str:
    row = cand.row
    if row.get("CB代碼"):
        return f"bond:{row['CB代碼']}"
    if row.get("公司代號"):
        return f"company:{row['公司代號']}:{row.get('公告日期') or row.get('董事會日期')}"
    if row.get("公司名稱") or row.get("標的名稱"):
        return f"name:{row.get('公司名稱') or row.get('標的名稱')}:{row.get('公告日期') or row.get('董事會日期')}"
    return f"url:{cand.source_url}"


def merge_candidates(candidates: List[Candidate]) -> List[Candidate]:
    merged: Dict[str, Candidate] = {}
    for cand in candidates:
        key = candidate_key(cand)
        old = merged.get(key)
        if not old or STATUS_PRIORITY[cand.status] > STATUS_PRIORITY[old.status]:
            merged[key] = cand
        elif old and STATUS_PRIORITY[cand.status] == STATUS_PRIORITY[old.status]:
            for k, v in cand.row.items():
                if v and not old.row.get(k):
                    old.row[k] = v
    return list(merged.values())


def validate_and_filter(candidates: List[Candidate], recent_codes: set) -> Tuple[List[Dict[str, str]], List[Dict[str, str]], List[Dict[str, str]]]:
    rows: List[Dict[str, str]] = []
    classify_logs: List[Dict[str, str]] = []
    removed_logs: List[Dict[str, str]] = []

    for cand in candidates:
        row = cand.row
        exists_in_recent = bool(row.get("CB代碼") and row["CB代碼"] in recent_codes)
        listing_date = parse_date(row.get("掛牌日", ""))

        case_status = row.get("caseStatus", "") or row.get("案件狀態", "")
        canceled = any(word in case_status for word in ("撤銷", "終止", "取消"))

        if exists_in_recent:
            cand.remove_reason = "已出現在存續CB清單"
        elif listing_date and listing_date <= TODAY:
            cand.remove_reason = "掛牌日已到或已過"
        elif canceled:
            cand.remove_reason = "案件狀態已撤銷/終止/取消"
        else:
            if listing_date and listing_date > TODAY:
                cand.status = "upcoming_listing"
                row["status"] = "upcoming_listing"
                cand.validation_status = cand.validation_status or "needs_review"
                cand.stale_reason = cand.stale_reason or "已有未來掛牌日，尚未進入存續CB清單"
            if not row.get("CB代碼"):
                cand.validation_status = "needs_review"
                cand.stale_reason = "官方公告未提供完整CB代碼"
            if not row.get("officialSourceUrl") or not row.get("officialEvidenceText"):
                cand.validation_status = "needs_review"
                cand.stale_reason = cand.stale_reason or "缺少官方來源網址或佐證文字"

            if cand.status == "bookbuilding_auction":
                end_date = (
                    parse_date(row.get("詢圈迄日", ""))
                    or parse_date(row.get("競拍迄日", ""))
                    or parse_date(row.get("開標日", ""))
                )
                if end_date and end_date < TODAY and not listing_date:
                    cand.validation_status = "needs_review"
                    cand.stale_reason = "詢圈或競拍已結束但尚未找到掛牌資料"
                elif not end_date:
                    announcement = parse_date(row.get("announcementDate", "")) or parse_date(row.get(OUTPUT_HEADERS[7], ""))
                    if announcement and (TODAY - announcement).days > 180:
                        cand.validation_status = "needs_review"
                        cand.stale_reason = cand.stale_reason or "公告日期超過180天且未找到後續承銷/掛牌/存續資料"
                    elif announcement:
                        cand.validation_status = "needs_review"
                        cand.stale_reason = cand.stale_reason or "官方承銷公告未提供詢圈/競拍期間，保留待確認"
            elif cand.status == "board_approved":
                base = parse_date(row.get("董事會日期", "")) or parse_date(row.get("公告日期", ""))
                if base and (TODAY - base).days > 180:
                    cand.validation_status = "needs_review"
                    cand.stale_reason = "董事會通過超過180天且未找到後續送件資料"
            elif cand.status == "filing":
                base = parse_date(row.get("送件日", "")) or parse_date(row.get("生效日", "")) or parse_date(row.get("公告日期", ""))
                if base and (TODAY - base).days > 90:
                    cand.validation_status = "needs_review"
                    cand.stale_reason = "送件中超過90天且未找到後續詢圈或競拍資料"

        final_status = "" if cand.remove_reason else cand.status
        validation = "removed" if cand.remove_reason else cand.validation_status
        reason = cand.remove_reason or cand.stale_reason or "official_valid"
        classify_logs.append({
            "fetchedAt": NOW.isoformat(),
            "normalizeKey": normalize_key(row),
            "companyCode": row.get("companyCode", "") or row.get(OUTPUT_HEADERS[2], ""),
            "companyName": row.get("companyName", "") or row.get(OUTPUT_HEADERS[3], "") or row.get(OUTPUT_HEADERS[1], ""),
            "bondCode": row.get("bondCode", "") or row.get(OUTPUT_HEADERS[0], ""),
            "bondName": row.get("bondName", "") or row.get(OUTPUT_HEADERS[4], ""),
            "detectedStatus": cand.status,
            "finalStatus": final_status,
            "sourceType": row.get("sourceType", ""),
            "sourceUrl": row.get("sourceUrl", ""),
            "evidenceText": row.get("officialEvidenceText", ""),
            "validationStatus": validation,
            "reason": reason,
        })

        if cand.remove_reason:
            removed_logs.append({
                "fetchedAt": NOW.isoformat(),
                "normalizeKey": normalize_key(row),
                "companyCode": row.get("companyCode", "") or row.get(OUTPUT_HEADERS[2], ""),
                "companyName": row.get("companyName", "") or row.get(OUTPUT_HEADERS[3], "") or row.get(OUTPUT_HEADERS[1], ""),
                "bondCode": row.get("bondCode", "") or row.get(OUTPUT_HEADERS[0], ""),
                "bondName": row.get("bondName", "") or row.get(OUTPUT_HEADERS[4], ""),
                "originalStatus": cand.status,
                "removeReason": cand.remove_reason,
                "listingDate": row.get("listingDate", "") or row.get(OUTPUT_HEADERS[16], ""),
                "existsInRecentCb": "yes" if exists_in_recent else "no",
                "sourceUrl": row.get("sourceUrl", ""),
            })
            continue

        row["validationStatus"] = cand.validation_status
        row["staleReason"] = cand.stale_reason
        if row.get("sourceType") not in ALLOWED_SOURCE_TYPES:
            row["sourceType"] = "needs_review"
            row["validationStatus"] = "needs_review"
            row["staleReason"] = row.get("staleReason") or "來源類型不在允許清單"
        rows.append({h: row.get(h, "") for h in OUTPUT_HEADERS})

    return rows, classify_logs, removed_logs


def rows_to_sections(rows: List[Dict[str, str]]) -> List[Dict[str, object]]:
    grouped = {status: [] for status in SECTION_META}
    for row in rows:
        status = row.get("status", "")
        if status in grouped:
            grouped[status].append({h: row.get(h, "") for h in OUTPUT_HEADERS})

    sections = []
    for status, (section_id, title) in SECTION_META.items():
        sections.append({
            "id": section_id,
            "title": title,
            "status": status,
            "headers": OUTPUT_HEADERS,
            "rows": grouped[status],
        })
    return sections


def write_payload(rows: List[Dict[str, str]], status: str, reason: str) -> None:
    payload = {
        "sheetTitle": "CB初級市場官方公開資訊",
        "updatedAt": NOW.strftime("%Y/%m/%d"),
        "fetchedAt": NOW.isoformat(),
        "source": "官方公開資訊",
        "sourceType": status,
        "sourceUrl": "",
        "reason": reason,
        "sections": rows_to_sections(rows),
    }
    DATA_PATH.write_text(
        "window.CB_PRIMARY_MARKET_DATA = "
        + json.dumps(payload, ensure_ascii=False, indent=2)
        + ";\n",
        encoding="utf-8",
    )


def write_csv(path: Path, fieldnames: List[str], rows: List[Dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name, "") for name in fieldnames})


def summarize_status(
    official_pages_success: int,
    official_parsed_count: int,
    kept_rows: int,
    removed_count: int,
) -> Tuple[str, str]:
    if official_pages_success == 0:
        return "official_fetch_failed", "官方來源頁面無法連線或未找到可抓取網址"
    if official_parsed_count == 0:
        return "official_parse_failed", "官方頁面可連線，但未解析到符合CB三階段關鍵字的資料"
    if official_parsed_count > 0 and kept_rows == 0 and removed_count > 0:
        return "official_all_filtered", "官方資料已解析，但全部因已掛牌或已在存續CB清單而移除"
    return "success", "official_data_updated"


def console_print(text: str) -> None:
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    print(str(text).encode(encoding, errors="replace").decode(encoding, errors="replace"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Update CB primary-market data from official sources only.")
    parser.add_argument("--timeout", type=int, default=12)
    parser.add_argument("--search-limit", type=int, default=6)
    parser.add_argument("--queries-per-source", type=int, default=3)
    parser.add_argument("--follow-link-limit", type=int, default=25)
    parser.add_argument("--debug-official-sources", action="store_true")
    parser.add_argument(
        "--allow-fubon-reference",
        action="store_true",
        help="Diagnostic flag only. Fubon Excel is never written to website data.",
    )
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    candidates, fetch_logs, target_search_logs, debug_lines, official_pages_success, official_url_count = fetch_official_sources(args)
    merged = merge_candidates(candidates)
    rows, classify_logs, removed_logs = validate_and_filter(merged, load_recent_cb_codes())

    official_parsed_count = len(merged)
    official_valid_count = sum(1 for row in rows if row.get("validationStatus") == "valid")
    status, reason = summarize_status(
        official_pages_success=official_pages_success,
        official_parsed_count=official_parsed_count,
        kept_rows=len(rows),
        removed_count=len(removed_logs),
    )

    write_payload(rows, status, reason)

    write_csv(
        FETCH_LOG_PATH,
        ["fetchedAt", "sourceType", "sourceUrl", "fetchStatus", "httpStatus", "rawCount", "parsedCount", "error"],
        fetch_logs,
    )
    write_csv(
        TARGET_SEARCH_LOG_PATH,
        [
            "checkedAt",
            "targetKeyword",
            "sourceName",
            "sourceUrl",
            "matchedTitle",
            "matchedText",
            "matchedBondCode",
            "matchedBondName",
            "matchedIssuerName",
            "candidateAction",
            "reason",
        ],
        target_search_logs,
    )
    write_csv(
        CLASSIFY_LOG_PATH,
        [
            "fetchedAt",
            "normalizeKey",
            "companyCode",
            "companyName",
            "bondCode",
            "bondName",
            "detectedStatus",
            "finalStatus",
            "sourceType",
            "sourceUrl",
            "evidenceText",
            "validationStatus",
            "reason",
        ],
        classify_logs,
    )
    write_csv(
        REMOVED_LOG_PATH,
        [
            "fetchedAt",
            "normalizeKey",
            "companyCode",
            "companyName",
            "bondCode",
            "bondName",
            "originalStatus",
            "removeReason",
            "listingDate",
            "existsInRecentCb",
            "sourceUrl",
        ],
        removed_logs,
    )

    counts = {"bookbuilding_auction": 0, "filing": 0, "board_approved": 0, "upcoming_listing": 0}
    needs_review = 0
    for row in rows:
        if row.get("status") in counts:
            counts[row["status"]] += 1
        if row.get("validationStatus") == "needs_review":
            needs_review += 1

    write_csv(
        UPDATE_LOG_PATH,
        [
            "fetchedAt",
            "officialRawCount",
            "officialParsedCount",
            "officialValidCount",
            "auctionCount",
            "filingCount",
            "boardApprovedCount",
            "upcomingListingCount",
            "needsReviewCount",
            "removedCount",
            "status",
            "reason",
        ],
        [{
            "fetchedAt": NOW.isoformat(),
            "officialRawCount": str(official_url_count),
            "officialParsedCount": str(official_parsed_count),
            "officialValidCount": str(official_valid_count),
            "auctionCount": str(counts["bookbuilding_auction"]),
            "filingCount": str(counts["filing"]),
            "boardApprovedCount": str(counts["board_approved"]),
            "upcomingListingCount": str(counts["upcoming_listing"]),
            "needsReviewCount": str(needs_review),
            "removedCount": str(len(removed_logs)),
            "status": status,
            "reason": reason,
        }],
    )

    if args.debug_official_sources:
        console_print("Official source debug:")
        for line in debug_lines:
            console_print(line)
        console_print(f"finalReason={reason}")

    console_print(
        f"status={status} rawUrls={official_url_count} parsed={official_parsed_count} "
        f"valid={official_valid_count} auction={counts['bookbuilding_auction']} "
        f"filing={counts['filing']} board={counts['board_approved']} "
        f"upcoming={counts['upcoming_listing']} needsReview={needs_review} removed={len(removed_logs)}"
    )
    if args.allow_fubon_reference:
        console_print("notice: --allow-fubon-reference is diagnostic only; Fubon Excel was not written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
