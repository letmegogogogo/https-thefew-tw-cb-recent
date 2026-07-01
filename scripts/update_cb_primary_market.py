from __future__ import annotations

import argparse
import csv
import html
import json
import os
import re
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote_plus, unquote, urlparse
from urllib.request import Request, urlopen
from zipfile import BadZipFile, ZipFile
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = ROOT / "outputs" / "cb-primary-market-data.js"
RECENT_CB_DATA_PATH = ROOT / "outputs" / "recent-cb-data.js"
UPDATE_LOG_PATH = ROOT / "outputs" / "cb-primary-market-update-log.csv"
REMOVED_LOG_PATH = ROOT / "outputs" / "cb-primary-market-removed-log.csv"
PRIMARY_PREFIX = "window.CB_PRIMARY_MARKET_DATA = "
RECENT_PREFIX = "window.RECENT_CB_DATA = "
TZ = timezone(timedelta(hours=8))
NS = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.7",
}

SECTION_META = {
    "詢圈/競拍": {"id": "auction", "status": "bookbuilding_auction", "title": "詢圈/競拍 標的"},
    "送件": {"id": "filing", "status": "filing", "title": "送件標的"},
    "董事會通過": {"id": "board", "status": "board_approved", "title": "董事會通過發行標的"},
}
OFFICIAL_DOMAINS = ("mops.twse.com.tw", "twse.com.tw", "www.twse.com.tw", "tpex.org.tw", "www.tpex.org.tw")
OFFICIAL_SEARCH_QUERIES = [
    "site:mops.twse.com.tw 可轉換公司債 申報生效 xlsx",
    "site:tpex.org.tw 可轉換公司債 詢圈 競拍 xlsx",
    "site:twse.com.tw 可轉換公司債 承銷公告 xlsx",
]


def now_iso() -> str:
    return datetime.now(TZ).isoformat()


def excel_serial_to_date(value: str) -> str | None:
    try:
        serial = int(float(str(value).strip()))
    except Exception:
        return None
    if serial < 1:
        return None
    return (datetime(1899, 12, 30, tzinfo=TZ) + timedelta(days=serial)).strftime("%Y-%m-%d")


def is_probable_excel_date(value: str) -> bool:
    text = str(value or "").strip()
    if not re.fullmatch(r"\d{4,5}(\.0+)?", text):
        return False
    try:
        serial = int(float(text))
    except ValueError:
        return False
    return 35000 <= serial <= 60000


def normalize_cell(value: str) -> str:
    text = (value or "").strip()
    if is_probable_excel_date(text):
        return excel_serial_to_date(text) or text
    return text


def parse_date(value: str) -> date | None:
    text = str(value or "").strip()
    for pattern in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            pass
    return None


def fetch_bytes(url: str, timeout: int) -> bytes:
    request = Request(url, headers=HEADERS)
    with urlopen(request, timeout=timeout) as response:
        return response.read()


def fetch_text(url: str, timeout: int) -> str:
    raw = fetch_bytes(url, timeout)
    for encoding in ("utf-8", "big5", "cp950"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def is_official_url(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return any(host == domain or host.endswith("." + domain) for domain in OFFICIAL_DOMAINS)


def official_source_type(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if "mops" in host:
        return "official_mops"
    if "tpex" in host:
        return "official_tpex"
    if "twse" in host:
        return "official_twse"
    return "official_underwriting"


def extract_search_urls(page: str) -> list[str]:
    urls: list[str] = []
    for match in re.finditer(r'href=["\']([^"\']+)["\']', page):
        href = html.unescape(match.group(1))
        if "uddg=" in href:
            uddg = re.search(r"uddg=([^&]+)", href)
            href = unquote(uddg.group(1)) if uddg else href
        if href.startswith("//"):
            href = "https:" + href
        if href.startswith("http") and href not in urls:
            urls.append(href)
    return urls


def official_candidate_urls(timeout: int) -> list[str]:
    urls: list[str] = []
    env_urls = os.environ.get("CB_PRIMARY_MARKET_OFFICIAL_URLS") or os.environ.get("CB_PRIMARY_MARKET_URLS") or ""
    for item in re.split(r"[\s,]+", env_urls):
        if item.strip() and is_official_url(item.strip()):
            urls.append(item.strip())
    for query in OFFICIAL_SEARCH_QUERIES:
        try:
            page = fetch_text(f"https://duckduckgo.com/html/?q={quote_plus(query)}", timeout=timeout)
        except Exception:
            continue
        for url in extract_search_urls(page):
            if is_official_url(url) and url not in urls:
                urls.append(url)
    return urls


def try_download_official_workbook(timeout: int) -> tuple[Path | None, str, str]:
    temp_dir = Path(tempfile.mkdtemp(prefix="cb-primary-official-"))
    for url in official_candidate_urls(timeout):
        try:
            raw = fetch_bytes(url, timeout)
        except Exception:
            continue
        if not raw.startswith(b"PK"):
            continue
        out = temp_dir / "official-primary-market.xlsx"
        out.write_bytes(raw)
        try:
            with ZipFile(out):
                pass
        except BadZipFile:
            continue
        return out, official_source_type(url), url
    return None, "", ""


def latest_local_workbook(download_dir: Path) -> Path | None:
    candidates: list[Path] = []
    for pattern in ("*CB初級市場資訊*.xlsx", "*CB*市場*.xlsx"):
        candidates.extend(download_dir.glob(pattern))
    candidates = [path for path in candidates if path.is_file()]
    return max(candidates, key=lambda path: path.stat().st_mtime) if candidates else None


def read_shared_strings(z: ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in z.namelist():
        return []
    root = ET.fromstring(z.read("xl/sharedStrings.xml"))
    return ["".join(t.text or "" for t in si.iterfind(".//a:t", NS)) for si in root.findall("a:si", NS)]


def cell_col_index(cell_ref: str) -> int:
    letters = re.match(r"[A-Z]+", cell_ref).group(0)
    value = 0
    for letter in letters:
        value = value * 26 + ord(letter) - 64
    return value - 1


def cell_text(cell: ET.Element, shared: list[str]) -> str:
    if cell.attrib.get("t") == "inlineStr":
        return "".join(t.text or "" for t in cell.iterfind(".//a:t", NS))
    value = cell.find("a:v", NS)
    if value is None or value.text is None:
        return ""
    if cell.attrib.get("t") == "s":
        return shared[int(value.text)]
    return value.text


def parse_sheet_rows(xlsx_path: Path) -> list[list[str]]:
    with ZipFile(xlsx_path) as z:
        shared = read_shared_strings(z)
        sheet = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
    rows: list[list[str]] = []
    for row in sheet.findall(".//a:sheetData/a:row", NS):
        values: list[str] = []
        for cell in row.findall("a:c", NS):
            idx = cell_col_index(cell.attrib["r"])
            while len(values) <= idx:
                values.append("")
            values[idx] = normalize_cell(cell_text(cell, shared))
        rows.append(values)
    return rows


def compact(row: list[str]) -> list[str]:
    return [cell.strip() for cell in row if cell and cell.strip()]


def detect_section(text: str) -> dict | None:
    for key, meta in SECTION_META.items():
        if key in text:
            return meta
    return None


def find_updated_at(rows: list[list[str]]) -> str:
    for row in rows[:5]:
        for cell in row:
            if is_probable_excel_date(cell):
                return excel_serial_to_date(cell) or datetime.now(TZ).date().isoformat()
            if re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", str(cell or "").strip()):
                return str(cell).strip()
    return datetime.now(TZ).date().isoformat()


def get_by_contains(item: dict, *names: str) -> str:
    for name in names:
        for key, value in item.items():
            if name in key:
                return value
    return ""


def normalize_row(item: dict, status: str, source_type: str, source: str, source_url: str, updated_at: str) -> dict:
    normalized = dict(item)
    normalized.update(
        {
            "CB代碼": get_by_contains(item, "CB代碼"),
            "標的名稱": get_by_contains(item, "標的名稱"),
            "發行期間年": get_by_contains(item, "發行期間"),
            "發行金額億": get_by_contains(item, "發行金額"),
            "公告日期": get_by_contains(item, "公告日期", "掛牌日", "送件日"),
            "主辦承銷商": get_by_contains(item, "主辦承銷商"),
            "信用等級 / 擔保行": get_by_contains(item, "信用等級", "擔保行"),
            "詢圈 / 競拍": get_by_contains(item, "詢圈", "競拍"),
            "產業別": get_by_contains(item, "產業別"),
            "資本額億": get_by_contains(item, "資本額"),
            "status": status,
            "sourceType": source_type,
            "source": source,
            "sourceUrl": source_url,
            "updatedAt": updated_at,
            "validationStatus": "valid",
            "staleReason": "",
            "officialSourceUrl": source_url if source_type.startswith("official_") else "",
            "officialEvidenceText": "",
        }
    )
    return normalized


def parse_sections(rows: list[list[str]], source_type: str, source: str, source_url: str) -> dict:
    sheet_title = compact(rows[0])[0] if rows and compact(rows[0]) else "CB初級市場資訊"
    updated_at = find_updated_at(rows)
    sections: list[dict] = []
    i = 0
    while i < len(rows):
        current = compact(rows[i])
        meta = detect_section(current[0]) if len(current) == 1 else None
        if not meta:
            i += 1
            continue
        headers = [h.strip() for h in (rows[i + 1] if i + 1 < len(rows) else []) if h and h.strip()]
        body: list[dict] = []
        j = i + 2
        while j < len(rows):
            next_compact = compact(rows[j])
            if len(next_compact) == 1 and detect_section(next_compact[0]):
                break
            if len(next_compact) >= 2 and headers:
                values = rows[j][: len(headers)]
                item = {
                    header: normalize_cell(values[index] if index < len(values) else "")
                    for index, header in enumerate(headers)
                }
                body.append(normalize_row(item, meta["status"], source_type, source, source_url, updated_at))
            j += 1
        sections.append({"id": meta["id"], "title": meta["title"], "headers": headers, "rows": body})
        i = j
    return {"sheetTitle": sheet_title, "updatedAt": updated_at, "sections": sections}


def load_recent_codes() -> set[str]:
    if not RECENT_CB_DATA_PATH.exists():
        return set()
    text = RECENT_CB_DATA_PATH.read_text(encoding="utf-8").strip()
    if not text.startswith(RECENT_PREFIX):
        return set()
    payload = json.loads(text[len(RECENT_PREFIX) :].rstrip(";"))
    return {str(row.get("bondCode") or "").strip() for row in payload.get("rows", []) if row.get("bondCode")}


def is_date_like_code(code: str) -> bool:
    return bool(re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", code) or re.fullmatch(r"\d{4}/\d{1,2}/\d{1,2}", code))


def validate_payload(payload: dict, recent_codes: set[str]) -> tuple[dict, list[dict], dict, dict]:
    today = datetime.now(TZ).date()
    raw_counts = {"auction": 0, "filing": 0, "board": 0}
    filtered_counts = {"auction": 0, "filing": 0, "board": 0}
    removed: list[dict] = []
    output_sections = []

    for section in payload.get("sections", []):
        section_id = section.get("id")
        rows = section.get("rows", [])
        raw_counts[section_id] = len(rows)
        kept = []
        for row in rows:
            code = str(row.get("CB代碼") or "").strip()
            name = row.get("標的名稱") or ""
            listing_date = parse_date(get_by_contains(row, "掛牌日"))
            reason = ""
            validation_status = "valid"
            if is_date_like_code(code):
                reason = "CB代碼格式像日期"
                validation_status = "parse_failed"
            elif code and code in recent_codes:
                reason = "已在存續 CB 清單"
                validation_status = "stale_removed"
            elif section_id == "auction" and listing_date and listing_date <= today:
                reason = "已到掛牌日"
                validation_status = "stale_removed"

            if reason:
                removed.append(
                    {
                        "CB代碼": code,
                        "標的名稱": name,
                        "原狀態": row.get("status") or "",
                        "移除原因": reason,
                        "掛牌日": listing_date.isoformat() if listing_date else "",
                        "是否已在 recent-cb-data.js": "yes" if code in recent_codes else "no",
                        "sourceType": row.get("sourceType") or "",
                        "sourceUrl": row.get("sourceUrl") or row.get("officialSourceUrl") or "",
                    }
                )
                continue
            if not code:
                row["validationStatus"] = "needs_review"
                row["staleReason"] = "尚無 CB 代碼"
            else:
                row["validationStatus"] = validation_status
                row["staleReason"] = ""
            kept.append(row)
        filtered_counts[section_id] = len(kept)
        next_section = dict(section)
        next_section["rows"] = kept
        output_sections.append(next_section)

    next_payload = dict(payload)
    next_payload["sections"] = output_sections
    return next_payload, removed, raw_counts, filtered_counts


def load_previous_payload() -> dict | None:
    if not OUTPUT_PATH.exists():
        return None
    text = OUTPUT_PATH.read_text(encoding="utf-8").strip()
    if not text.startswith(PRIMARY_PREFIX):
        return None
    return json.loads(text[len(PRIMARY_PREFIX) :].rstrip(";"))


def mark_fallback_previous(payload: dict) -> dict:
    next_payload = dict(payload)
    sections = []
    for section in payload.get("sections", []):
        next_section = dict(section)
        next_rows = []
        for row in section.get("rows", []):
            next_row = dict(row)
            next_row["sourceType"] = "fallback_previous"
            next_row["source"] = "上一版資料"
            next_row["sourceUrl"] = ""
            next_row["validationStatus"] = next_row.get("validationStatus") or "needs_review"
            next_rows.append(next_row)
        next_section["rows"] = next_rows
        sections.append(next_section)
    next_payload["sections"] = sections
    next_payload["fetchedAt"] = now_iso()
    return next_payload


def write_payload(payload: dict) -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(PRIMARY_PREFIX + json.dumps(payload, ensure_ascii=False, indent=2) + ";\n", encoding="utf-8")


def write_update_log(source_type: str, source_url: str, raw: dict, filtered: dict, removed: list[dict], status: str, reason: str) -> None:
    parse_failed = sum(1 for row in removed if row.get("移除原因") == "CB代碼格式像日期")
    with UPDATE_LOG_PATH.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "fetchedAt",
                "sourceType",
                "sourceUrl",
                "rawAuctionCount",
                "filteredAuctionCount",
                "rawFilingCount",
                "filteredFilingCount",
                "rawBoardApprovedCount",
                "filteredBoardApprovedCount",
                "removedCount",
                "parseFailedCount",
                "status",
                "reason",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "fetchedAt": now_iso(),
                "sourceType": source_type,
                "sourceUrl": source_url,
                "rawAuctionCount": raw.get("auction", 0),
                "filteredAuctionCount": filtered.get("auction", 0),
                "rawFilingCount": raw.get("filing", 0),
                "filteredFilingCount": filtered.get("filing", 0),
                "rawBoardApprovedCount": raw.get("board", 0),
                "filteredBoardApprovedCount": filtered.get("board", 0),
                "removedCount": len(removed),
                "parseFailedCount": parse_failed,
                "status": status,
                "reason": reason,
            }
        )


def write_removed_log(removed: list[dict]) -> None:
    with REMOVED_LOG_PATH.open("w", encoding="utf-8-sig", newline="") as handle:
        fields = ["CB代碼", "標的名稱", "原狀態", "移除原因", "掛牌日", "是否已在 recent-cb-data.js", "sourceType", "sourceUrl"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(removed)


def resolve_workbook(args) -> tuple[Path, str, str, str]:
    if args.input:
        path = Path(args.input)
        if not path.exists():
            raise FileNotFoundError(str(path))
        return path, "fallback_reference", "指定Excel備援參考", ""

    official, source_type, source_url = try_download_official_workbook(args.timeout)
    if official:
        return official, source_type, "官方公開資訊", source_url

    local = latest_local_workbook(Path(args.download_dir))
    if local:
        return local, "fallback_reference", "富邦Excel備援參考", ""

    raise FileNotFoundError("找不到官方來源或備援 Excel")


def section_counts(payload: dict) -> dict:
    counts = {"auction": 0, "filing": 0, "board": 0}
    for section in payload.get("sections", []):
        if section.get("id") in counts:
            counts[section["id"]] = len(section.get("rows", []))
    return counts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--download-dir", default=str(Path.home() / "Downloads"))
    parser.add_argument("--input", default="")
    parser.add_argument("--timeout", type=int, default=12)
    args = parser.parse_args()

    recent_codes = load_recent_codes()
    previous = load_previous_payload()
    try:
        workbook, source_type, source, source_url = resolve_workbook(args)
        rows = parse_sheet_rows(workbook)
        payload = parse_sections(rows, source_type, source, source_url)
        payload["sourceFile"] = workbook.name if source_type == "fallback_reference" else urlparse(source_url).path.rsplit("/", 1)[-1]
        payload["sourceType"] = source_type
        payload["sourceUrl"] = source_url
        payload["fetchedAt"] = now_iso()
        payload, removed, raw_counts, filtered_counts = validate_payload(payload, recent_codes)
        write_payload(payload)
        write_update_log(source_type, source_url, raw_counts, filtered_counts, removed, "success", "")
        write_removed_log(removed)
        print(
            "updated primary market: "
            f"auction={filtered_counts['auction']} filing={filtered_counts['filing']} "
            f"board={filtered_counts['board']} removed={len(removed)} sourceType={source_type}"
        )
        return 0
    except Exception as error:
        if previous:
            fallback = mark_fallback_previous(previous)
            fallback, removed, raw_counts, filtered_counts = validate_payload(fallback, recent_codes)
            write_payload(fallback)
            write_update_log("fallback_previous", "", raw_counts, filtered_counts, removed, "fallback_previous", f"{type(error).__name__}: {error}")
            write_removed_log(removed)
            print(f"primary market fallback_previous: {type(error).__name__}: {error}")
            return 0
        write_update_log("", "", {"auction": 0, "filing": 0, "board": 0}, {"auction": 0, "filing": 0, "board": 0}, [], "failed", f"{type(error).__name__}: {error}")
        write_removed_log([])
        raise


if __name__ == "__main__":
    raise SystemExit(main())
