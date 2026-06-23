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
NS = {
    "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}


def split_tags(value: str) -> list[str]:
    return [item.strip() for item in re.split(r"[、,，/；;]+", str(value or "")) if item.strip()]


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


def first_value(row: dict[str, str], *keys: str) -> str:
    for key in keys:
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def read_sheet(path: Path, preferred_names: list[str]) -> list[dict[str, str]]:
    with zipfile.ZipFile(path) as archive:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            shared = ["".join(node.text or "" for node in item.findall(".//m:t", NS)) for item in root.findall("m:si", NS)]

        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {item.attrib["Id"]: item.attrib["Target"] for item in relationships}
        sheets = workbook.findall("m:sheets/m:sheet", NS)
        sheet = next((item for name in preferred_names for item in sheets if item.attrib["name"] == name), sheets[0])
        target = targets[sheet.attrib[f"{{{NS['r']}}}id"]].lstrip("/")
        if not target.startswith("xl/"):
            target = "xl/" + target

        document = ET.fromstring(archive.read(target))
        records: list[dict[str, str]] = []
        for row in document.findall(".//m:sheetData/m:row", NS):
            record: dict[str, str] = {}
            for cell in row.findall("m:c", NS):
                match = re.match(r"[A-Z]+", cell.attrib.get("r", ""))
                if not match:
                    continue
                column = match[0]
                value_node = cell.find("m:v", NS)
                inline = cell.find("m:is", NS)
                if cell.attrib.get("t") == "s" and value_node is not None:
                    value = shared[int(value_node.text)]
                elif cell.attrib.get("t") == "inlineStr" and inline is not None:
                    value = "".join(node.text or "" for node in inline.findall(".//m:t", NS))
                else:
                    value = value_node.text if value_node is not None else ""
                record[column] = value or ""
            records.append(record)

        if not records:
            return []
        headers = records[0]
        return [{headers.get(column, column): value for column, value in record.items()} for record in records[1:]]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("xlsx", type=Path)
    args = parser.parse_args()

    recent_text = RECENT_PATH.read_text(encoding="utf-8").strip()
    recent = json.loads(recent_text[len(RECENT_PREFIX) :].rstrip(";"))
    current_codes = {str(row.get("issuerCode") or "").strip() for row in recent.get("rows", []) if row.get("issuerCode")}
    tags = json.loads(TAGS_PATH.read_text(encoding="utf-8"))

    matched = 0
    ignored = 0
    rows = read_sheet(args.xlsx, ["ABC精修完成清單", "CB股票集中表", "CB精修分類"])
    for row in rows:
        code = first_value(row, "股票代碼")
        if not code or code not in current_codes:
            ignored += 1
            continue

        grade = confidence(first_value(row, "精準度"))
        tags[code] = {
            "stockName": first_value(row, "公司簡稱") or tags.get(code, {}).get("stockName") or code,
            "officialIndustry": first_value(row, "官方產業分類") or tags.get(code, {}).get("officialIndustry") or "-",
            "fineIndustries": split_tags(first_value(row, "新細產業", "細產業")) or ["其他"],
            "productTags": split_tags(first_value(row, "產品標籤")),
            "themeTags": split_tags(first_value(row, "題材標籤")),
            "groupTags": split_tags(first_value(row, "族群標籤")) or split_tags(first_value(row, "新細產業", "細產業"))[:1],
            "confidence": grade,
            "source": "manual" if grade >= 90 else "officialIndustryOnly",
            "updatedAt": date.today().isoformat(),
        }
        matched += 1

    for code in current_codes:
        item = tags.get(code)
        if item is not None:
            item["fineIndustries"] = item.get("fineIndustries") or ["其他"]
            item["groupTags"] = item.get("groupTags") or list(item.get("fineIndustries") or [])[:1]

    tags = {
        code: tags[code]
        for code in sorted(current_codes, key=lambda value: (not value.isdigit(), int(value) if value.isdigit() else value))
    }
    TAGS_PATH.write_text(json.dumps(tags, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Current issuers: {len(current_codes)}; imported: {matched}; ignored: {ignored}; tags: {len(tags)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
