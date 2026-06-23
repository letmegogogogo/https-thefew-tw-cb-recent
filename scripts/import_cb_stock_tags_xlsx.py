from __future__ import annotations

import argparse
import json
import re
import xml.etree.ElementTree as ET
import zipfile
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RECENT_PATH = ROOT / "outputs" / "recent-cb-data.js"
TAGS_PATH = ROOT / "data" / "tw-stock-tags.json"
RECENT_PREFIX = "window.RECENT_CB_DATA = "
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main", "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}


def split_tags(value: str) -> list[str]:
    return [item.strip() for item in re.split(r"[、,，/；;]+", value or "") if item.strip()]


def read_sheet(path: Path, sheet_name: str) -> list[dict]:
    with zipfile.ZipFile(path) as archive:
        shared = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            shared = ["".join(node.text or "" for node in item.findall(".//m:t", NS)) for item in root.findall("m:si", NS)]
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {item.attrib["Id"]: item.attrib["Target"] for item in relationships}
        sheet = next(item for item in workbook.findall("m:sheets/m:sheet", NS) if item.attrib["name"] == sheet_name)
        target = targets[sheet.attrib[f"{{{NS['r']}}}id"]].lstrip("/")
        if not target.startswith("xl/"):
            target = "xl/" + target
        document = ET.fromstring(archive.read(target))
        records = []
        for row in document.findall(".//m:sheetData/m:row", NS):
            record = {}
            for cell in row.findall("m:c", NS):
                column = re.match(r"[A-Z]+", cell.attrib.get("r", ""))[0]
                cell_type = cell.attrib.get("t")
                value_node = cell.find("m:v", NS)
                inline = cell.find("m:is", NS)
                if cell_type == "s" and value_node is not None:
                    value = shared[int(value_node.text)]
                elif cell_type == "inlineStr" and inline is not None:
                    value = "".join(node.text or "" for node in inline.findall(".//m:t", NS))
                else:
                    value = value_node.text if value_node is not None else ""
                record[column] = value or ""
            records.append(record)
        headers = records[0]
        return [{headers.get(column, column): value for column, value in record.items()} for record in records[1:]]


def confidence(value: str) -> int:
    text = str(value or "").strip().upper()
    if text.startswith("A"):
        return 90
    if text.startswith("B"):
        return 60
    if text.startswith("C"):
        return 40
    match = re.search(r"\d+", text)
    return int(match.group()) if match else 40


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("xlsx", type=Path)
    args = parser.parse_args()

    recent_text = RECENT_PATH.read_text(encoding="utf-8").strip()
    recent = json.loads(recent_text[len(RECENT_PREFIX) :].rstrip(";"))
    current_codes = {str(row.get("issuerCode") or "").strip() for row in recent.get("rows", []) if row.get("issuerCode")}
    tags = json.loads(TAGS_PATH.read_text(encoding="utf-8"))
    for code in current_codes:
        item = tags.get(code)
        if item is not None and not item.get("groupTags"):
            item["groupTags"] = list(item.get("fineIndustries") or [])[:1]

    matched = 0
    ignored = 0
    for row in read_sheet(args.xlsx, "CB精修分類"):
        code = str(row.get("股票代碼") or "").strip()
        if not code or code not in current_codes:
            ignored += 1
            continue
        grade = confidence(row.get("精準度", ""))
        tags[code] = {
            "stockName": str(row.get("公司簡稱") or tags.get(code, {}).get("stockName") or code).strip(),
            "officialIndustry": str(row.get("官方產業分類") or tags.get(code, {}).get("officialIndustry") or "-").strip(),
            "fineIndustries": split_tags(row.get("細產業", "")) or ["其他"],
            "productTags": split_tags(row.get("產品標籤", "")),
            "themeTags": split_tags(row.get("題材標籤", "")),
            "groupTags": split_tags(row.get("族群標籤", "")),
            "confidence": grade,
            "source": "manual" if grade >= 90 else "officialIndustryOnly",
            "updatedAt": date.today().isoformat(),
        }
        matched += 1

    tags = {code: tags[code] for code in sorted(current_codes, key=lambda value: (not value.isdigit(), int(value) if value.isdigit() else value))}
    TAGS_PATH.write_text(json.dumps(tags, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Current issuers: {len(current_codes)}; imported: {matched}; ignored: {ignored}; tags: {len(tags)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
