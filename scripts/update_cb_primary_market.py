from __future__ import annotations

import argparse
import csv
import html
import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote_plus, unquote, urlparse
from urllib.request import Request, urlopen
from zipfile import BadZipFile, ZipFile
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = ROOT / "outputs" / "cb-primary-market-data.js"
LOG_PATH = ROOT / "outputs" / "cb-primary-market-update-log.csv"
PREFIX = "window.CB_PRIMARY_MARKET_DATA = "
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

SEARCH_QUERIES = [
    "富邦證券 CB初級市場資訊 xlsx",
    "CB初級市場資訊 xlsx 富邦證券",
    "CB初級市場資訊 轉換公司債 xlsx",
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


def extract_search_urls(page: str) -> list[str]:
    urls: list[str] = []
    for match in re.finditer(r'href=["\']([^"\']+)["\']', page):
        href = html.unescape(match.group(1))
        if "uddg=" in href:
            href = unquote(re.search(r"uddg=([^&]+)", href).group(1)) if re.search(r"uddg=([^&]+)", href) else href
        if href.startswith("//"):
            href = "https:" + href
        if not href.startswith("http"):
            continue
        if any(skip in href for skip in ("duckduckgo.com", "google.com/search", "bing.com/search")):
            continue
        if href not in urls:
            urls.append(href)
    return urls


def candidate_urls(timeout: int) -> list[str]:
    urls: list[str] = []
    env_urls = os.environ.get("CB_PRIMARY_MARKET_URLS") or os.environ.get("CB_PRIMARY_MARKET_URL") or ""
    for item in re.split(r"[\s,]+", env_urls):
        if item.strip():
            urls.append(item.strip())
    for query in SEARCH_QUERIES:
        try:
            page = fetch_text(f"https://duckduckgo.com/html/?q={quote_plus(query)}", timeout=timeout)
        except Exception:
            continue
        for url in extract_search_urls(page):
            if url not in urls:
                urls.append(url)
    return urls


def download_candidate_workbook(timeout: int) -> tuple[Path | None, str, str]:
    temp_dir = Path(tempfile.mkdtemp(prefix="cb-primary-market-"))
    for url in candidate_urls(timeout):
        try:
            raw = fetch_bytes(url, timeout=timeout)
        except Exception:
            continue
        if not raw.startswith(b"PK"):
            continue
        out = temp_dir / "cb-primary-market.xlsx"
        out.write_bytes(raw)
        try:
            with ZipFile(out):
                pass
        except BadZipFile:
            continue
        return out, "公開來源/搜尋", url
    return None, "", ""


def latest_local_workbook(download_dir: Path) -> Path | None:
    patterns = ["*CB初級市場資訊*.xlsx", "*CB*市場*.xlsx"]
    candidates: list[Path] = []
    for pattern in patterns:
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
    value = cell.find("a:v", NS)
    if cell.attrib.get("t") == "inlineStr":
        return "".join(t.text or "" for t in cell.iterfind(".//a:t", NS))
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


def find_updated_at(rows: list[list[str]]) -> str | None:
    for row in rows[:5]:
        for cell in row:
            if is_probable_excel_date(cell):
                return excel_serial_to_date(cell)
            if re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", str(cell or "").strip()):
                return str(cell).strip()
    return None


def normalize_row(item: dict, status: str, source: str, source_url: str, updated_at: str) -> dict:
    def get(*names: str) -> str:
        for name in names:
            for key, value in item.items():
                if name in key:
                    return value
        return ""

    normalized = dict(item)
    normalized.update(
        {
            "CB代碼": get("CB代碼"),
            "標的名稱": get("標的名稱"),
            "發行期間年": get("發行期間"),
            "發行金額億": get("發行金額"),
            "公告日期": get("公告日期", "掛牌日", "送件日"),
            "主辦承銷商": get("主辦承銷商"),
            "信用等級 / 擔保行": get("信用等級", "擔保行"),
            "詢圈 / 競拍": get("詢圈", "競拍"),
            "產業別": get("產業別"),
            "資本額億": get("資本額"),
            "status": status,
            "source": source,
            "sourceUrl": source_url,
            "updatedAt": updated_at,
        }
    )
    return normalized


def parse_sections(rows: list[list[str]], source: str, source_url: str) -> dict:
    sheet_title = compact(rows[0])[0] if rows and compact(rows[0]) else "富邦證券CB初級市場資訊"
    updated_at = find_updated_at(rows) or datetime.now(TZ).date().isoformat()
    sections: list[dict] = []
    i = 0
    while i < len(rows):
        current = compact(rows[i])
        meta = detect_section(current[0]) if len(current) == 1 else None
        if not meta:
            i += 1
            continue
        header_row = rows[i + 1] if i + 1 < len(rows) else []
        headers = [h.strip() for h in header_row if h and h.strip()]
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
                body.append(normalize_row(item, meta["status"], source, source_url, updated_at))
            j += 1
        sections.append({"id": meta["id"], "title": meta["title"], "headers": headers, "rows": body})
        i = j
    return {
        "sheetTitle": sheet_title,
        "updatedAt": updated_at,
        "sections": sections,
    }


def section_counts(payload: dict) -> dict[str, int]:
    counts = {"auction": 0, "filing": 0, "board": 0}
    for section in payload.get("sections", []):
        if section.get("id") in counts:
            counts[section["id"]] = len(section.get("rows", []))
    return counts


def load_previous_payload() -> dict | None:
    if not OUTPUT_PATH.exists():
        return None
    text = OUTPUT_PATH.read_text(encoding="utf-8").strip()
    if not text.startswith(PREFIX):
        return None
    return json.loads(text[len(PREFIX) :].rstrip(";"))


def write_payload(payload: dict) -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(PREFIX + json.dumps(payload, ensure_ascii=False, indent=2) + ";\n", encoding="utf-8")


def write_log(source_url: str, counts: dict[str, int], status: str, reason: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    exists = LOG_PATH.exists()
    with LOG_PATH.open("a", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "fetchedAt",
                "sourceUrl",
                "auctionCount",
                "filingCount",
                "boardApprovedCount",
                "status",
                "reason",
            ],
        )
        if not exists:
            writer.writeheader()
        writer.writerow(
            {
                "fetchedAt": now_iso(),
                "sourceUrl": source_url,
                "auctionCount": counts.get("auction", 0),
                "filingCount": counts.get("filing", 0),
                "boardApprovedCount": counts.get("board", 0),
                "status": status,
                "reason": reason,
            }
        )


def resolve_workbook(args) -> tuple[Path, str, str]:
    if args.input:
        path = Path(args.input)
        if not path.exists():
            raise FileNotFoundError(str(path))
        return path, "指定檔案", str(path)

    downloaded, source, source_url = download_candidate_workbook(timeout=args.timeout)
    if downloaded:
        return downloaded, source, source_url

    local = latest_local_workbook(Path(args.download_dir))
    if local:
        return local, "本機最新富邦CB初級市場資訊", ""

    raise FileNotFoundError("找不到可用的 CB 初級市場資訊 Excel")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--download-dir", default=str(Path.home() / "Downloads"))
    parser.add_argument("--input", default="")
    parser.add_argument("--timeout", type=int, default=12)
    args = parser.parse_args()

    previous = load_previous_payload()
    try:
        workbook, source, source_url = resolve_workbook(args)
        rows = parse_sheet_rows(workbook)
        payload = parse_sections(rows, source=source, source_url=source_url)
        counts = section_counts(payload)
        if not payload.get("sections") or not any(counts.values()):
            raise ValueError("解析結果沒有三個頁面資料")
        payload["sourceFile"] = workbook.name if source == "本機最新富邦CB初級市場資訊" else urlparse(source_url).path.rsplit("/", 1)[-1]
        payload["sourceUrl"] = source_url
        payload["fetchedAt"] = now_iso()
        write_payload(payload)
        write_log(source_url, counts, "success", "")
        print(f"updated primary market: auction={counts['auction']} filing={counts['filing']} board={counts['board']}")
        return 0
    except Exception as error:
        if previous:
            counts = section_counts(previous)
            write_log(previous.get("sourceUrl") or previous.get("sourceFile") or "", counts, "fallback_previous", f"{type(error).__name__}: {error}")
            print(f"primary market fallback_previous: {type(error).__name__}: {error}")
            return 0
        write_log("", {"auction": 0, "filing": 0, "board": 0}, "failed", f"{type(error).__name__}: {error}")
        raise


if __name__ == "__main__":
    raise SystemExit(main())
