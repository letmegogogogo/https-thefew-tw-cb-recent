from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zipfile import ZipFile
import xml.etree.ElementTree as ET

NS = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
TZ = timezone(timedelta(hours=8))


def excel_serial_to_date(value: str) -> str | None:
    try:
        serial = int(float(str(value).strip()))
    except Exception:
        return None
    if serial < 1:
        return None
    # Excel 1900 date system (with the leap year bug behavior).
    base = datetime(1899, 12, 30, tzinfo=TZ)
    dt = base + timedelta(days=serial)
    return dt.strftime("%Y-%m-%d")


def is_date_header(text: str) -> bool:
    keys = (
        "更新日期",
        "公告日期",
        "送件日",
        "生效日",
        "掛牌日",
        "發行日",
        "到期日",
        "日期",
    )
    return any(k in text for k in keys)


def pick_latest_workbook(download_dir: Path) -> Path:
    candidates = sorted(
        download_dir.glob("CB初級市場資訊*.xlsx"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise FileNotFoundError("找不到 CB初級市場資訊*.xlsx")
    return candidates[0]


def read_shared_strings(z: ZipFile) -> list[str]:
    root = ET.fromstring(z.read("xl/sharedStrings.xml"))
    out: list[str] = []
    for si in root.findall("a:si", NS):
        out.append("".join(t.text or "" for t in si.iterfind(".//a:t", NS)))
    return out


def cell_text(cell: ET.Element, shared: list[str]) -> str:
    value = cell.find("a:v", NS)
    if value is None:
        inline = cell.find("a:is", NS)
        if inline is not None:
          return "".join(t.text or "" for t in inline.iterfind(".//a:t", NS))
        return ""
    if cell.attrib.get("t") == "s":
        return shared[int(value.text)]
    return value.text or ""


def parse_sheet_rows(xlsx_path: Path) -> tuple[str | None, list[list[str]]]:
    with ZipFile(xlsx_path) as z:
        shared = read_shared_strings(z)
        sheet = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
        rows: list[list[str]] = []
        for row in sheet.findall(".//a:sheetData/a:row", NS):
            values: list[str] = []
            for cell in row.findall("a:c", NS):
                values.append(cell_text(cell, shared))
            rows.append(values)
    return None, rows


def normalize_value(header: str, value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    if is_date_header(header):
        converted = excel_serial_to_date(text)
        if converted:
            return converted
    return text


def parse_sections(rows: list[list[str]]) -> dict:
    sheet_title = rows[0][0].strip() if rows and rows[0] and rows[0][0].strip() else "CB初級市場資訊"
    update_date = None
    if len(rows) > 1:
        for cell in rows[1]:
            update_date = excel_serial_to_date(cell)
            if update_date:
                break

    sections = []
    i = 0
    while i < len(rows):
        row = rows[i]
        compact = [c.strip() for c in row if c and c.strip()]
        if len(compact) == 1 and compact[0] not in {sheet_title, "更新日期:"}:
            section_title = compact[0]
            header_row = rows[i + 1] if i + 1 < len(rows) else []
            headers = [h.strip() for h in header_row if h and h.strip()]
            body = []
            j = i + 2
            while j < len(rows):
                next_compact = [c.strip() for c in rows[j] if c and c.strip()]
                if len(next_compact) == 1 and next_compact[0] not in {sheet_title, "更新日期:"}:
                    break
                if len(next_compact) >= 2:
                    values = rows[j][: len(headers)]
                    item = {}
                    for idx, header in enumerate(headers):
                        item[header] = normalize_value(header, values[idx] if idx < len(values) else "")
                    body.append(item)
                j += 1
            section_id = "section-" + str(len(sections) + 1)
            if "詢圈/競拍" in section_title:
                section_id = "auction"
            elif "送件" in section_title:
                section_id = "filing"
            elif "董事會" in section_title:
                section_id = "board"
            sections.append({"id": section_id, "title": section_title, "headers": headers, "rows": body})
            i = j
            continue
        i += 1

    return {
        "sheetTitle": sheet_title,
        "updatedAt": update_date,
        "sections": sections,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--download-dir", default=str(Path.home() / "Downloads"))
    parser.add_argument("--input", default="")
    parser.add_argument("--output", default=str(Path(__file__).resolve().parents[1] / "outputs" / "cb-primary-market-data.js"))
    args = parser.parse_args()

    download_dir = Path(args.download_dir)
    xlsx_path = Path(args.input) if args.input else pick_latest_workbook(download_dir)
    _, rows = parse_sheet_rows(xlsx_path)
    payload = parse_sections(rows)
    payload["sourceFile"] = xlsx_path.name
    payload["fetchedAt"] = datetime.now(TZ).isoformat()

    out_path = Path(args.output)
    out_path.write_text("window.CB_PRIMARY_MARKET_DATA = " + json.dumps(payload, ensure_ascii=False, indent=2) + ";\n", encoding="utf-8")
    print(out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
