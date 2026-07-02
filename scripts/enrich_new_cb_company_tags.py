from __future__ import annotations

import argparse
import csv
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RECENT_DATA_PATH = ROOT / "outputs" / "recent-cb-data.js"
STOCK_TAGS_PATH = ROOT / "data" / "tw-stock-tags.json"
LOG_PATH = ROOT / "outputs" / "new-cb-company-tags-log.csv"
PREFIX = "window.RECENT_CB_DATA = "
TZ = timezone(timedelta(hours=8))


NAME_RULES = [
    {
        "patterns": ["萬潤"],
        "fineIndustries": ["半導體設備", "自動化設備"],
        "productTags": ["自動化設備", "半導體設備", "檢測設備", "封裝設備"],
        "themeTags": ["先進封裝", "半導體設備", "自動化"],
        "groupTags": ["半導體設備"],
        "confidence": 80,
        "accuracy": "medium",
        "source": "keyword",
        "sourceNote": "依 CB 公司名稱與半導體設備常見公開產品分類規則初步補標，需定期人工複核。",
    },
    {
        "patterns": ["擎亞"],
        "fineIndustries": ["電子通路", "IC通路"],
        "productTags": ["IC通路", "電子零組件代理", "半導體通路"],
        "themeTags": ["電子通路", "半導體供應鏈"],
        "groupTags": ["電子通路"],
        "confidence": 75,
        "accuracy": "medium",
        "source": "keyword",
        "sourceNote": "依 CB 公司名稱與電子通路產業分類規則初步補標，需定期人工複核。",
    },
    {
        "patterns": ["宜鼎"],
        "fineIndustries": ["工業電腦", "記憶體模組"],
        "productTags": ["工業用記憶體", "SSD", "記憶體模組", "嵌入式儲存"],
        "themeTags": ["工業電腦", "邊緣運算", "AIoT"],
        "groupTags": ["工業電腦", "記憶體模組"],
        "confidence": 85,
        "accuracy": "high",
        "source": "keyword",
        "sourceNote": "依 CB 公司名稱與工業記憶體/嵌入式儲存公開產品分類規則補標。",
    },
]


KEYWORD_RULES = [
    (["PCB", "印刷電路板", "電路板", "多層板", "HDI"], ["PCB"], ["PCB", "多層板", "HDI"], ["AI伺服器", "高速傳輸"], ["PCB"], 75),
    (["銅箔基板", "CCL", "Low DK", "Low DF", "高頻高速材料"], ["CCL", "高速材料"], ["銅箔基板", "高速材料"], ["AI伺服器", "高速傳輸"], ["高速材料"], 80),
    (["連接器", "線束", "Type-C"], ["連接器"], ["連接器", "Type-C"], ["高速傳輸", "車用"], ["連接器"], 75),
    (["MOSFET", "二極體", "整流器", "功率元件", "IGBT", "SiC"], ["功率元件"], ["功率元件", "MOSFET", "二極體"], ["電源管理", "車用"], ["功率元件"], 75),
    (["電感", "MLCC", "被動元件", "磁性元件"], ["被動元件"], ["被動元件", "電感", "MLCC"], ["AI電源", "車用"], ["被動元件"], 75),
    (["晶片", "控制晶片", "IC設計", "USB", "Hub", "網通晶片"], ["IC設計"], ["IC設計", "控制晶片"], ["高速傳輸", "PC周邊"], ["IC設計"], 75),
    (["散熱", "風扇", "均熱片", "水冷", "熱管"], ["散熱"], ["散熱模組", "風扇", "熱管"], ["AI伺服器", "水冷散熱"], ["散熱"], 80),
    (["機殼", "滑軌", "機構件", "機櫃"], ["機構件"], ["機構件", "機殼", "滑軌"], ["AI伺服器", "資料中心"], ["機構件"], 75),
]


OFFICIAL_INDUSTRY_FALLBACKS = {
    "水泥": ("水泥", ["水泥", "建材"], ["水泥", "建材"], ["低碳建材"], ["水泥"]),
    "食品": ("食品", ["食品"], ["食品", "飲料"], ["內需消費"], ["食品"]),
    "塑膠": ("塑化", ["塑化"], ["塑膠", "化工材料"], ["原物料"], ["塑化"]),
    "化學": ("化工", ["化工"], ["化學品", "化工材料"], ["特用化學"], ["化工"]),
    "生技": ("生技醫療", ["生技醫療"], ["醫療產品", "保健"], ["生技醫療"], ["生技醫療"]),
    "醫療": ("生技醫療", ["生技醫療"], ["醫療產品", "保健"], ["生技醫療"], ["生技醫療"]),
    "建材營造": ("營建", ["營建"], ["建設", "營造"], ["房市"], ["營建"]),
    "鋼鐵": ("鋼鐵", ["鋼鐵"], ["鋼材"], ["原物料"], ["鋼鐵"]),
    "橡膠": ("橡膠", ["橡膠"], ["輪胎", "橡膠製品"], ["車用"], ["橡膠"]),
    "汽車": ("汽車零組件", ["汽車零組件"], ["汽車零組件", "車用零件"], ["車用"], ["汽車"]),
    "半導體": ("半導體", ["半導體"], ["半導體產品"], ["半導體供應鏈"], ["半導體"]),
    "電腦": ("電腦週邊", ["電腦週邊"], ["電腦週邊", "系統產品"], ["AI PC", "工業電腦"], ["電腦週邊"]),
    "光電": ("光電", ["光電"], ["光電產品", "面板零組件"], ["光電"], ["光電"]),
    "通信網路": ("網通設備", ["網通設備"], ["網通設備", "無線通訊"], ["AIoT", "高速傳輸"], ["網通"]),
    "電子零組件": ("電子零組件", ["電子零組件"], ["電子零組件"], ["電子供應鏈"], ["電子零組件"]),
    "電子通路": ("電子通路", ["電子通路"], ["IC通路", "電子零組件代理"], ["電子通路"], ["電子通路"]),
    "資訊服務": ("資訊服務", ["資訊服務"], ["資訊服務", "軟體服務"], ["雲端服務", "資安"], ["資訊服務"]),
    "其他電子": ("其他電子", ["其他電子"], ["電子產品", "設備服務"], ["電子供應鏈"], ["其他電子"]),
    "觀光": ("觀光餐旅", ["觀光餐旅"], ["餐飲", "旅宿"], ["內需消費"], ["觀光餐旅"]),
    "金融": ("金融", ["金融"], ["金融服務"], ["金融"], ["金融"]),
    "貿易百貨": ("貿易百貨", ["貿易百貨"], ["百貨通路", "貿易"], ["內需消費"], ["貿易百貨"]),
    "文化創意": ("文化創意", ["文化創意"], ["文創內容"], ["娛樂消費"], ["文化創意"]),
}


def today_text() -> str:
    return datetime.now(TZ).date().isoformat()


def load_recent_rows() -> list[dict]:
    text = RECENT_DATA_PATH.read_text(encoding="utf-8").strip()
    if not text.startswith(PREFIX):
        raise ValueError("recent-cb-data.js format is invalid")
    payload = json.loads(text[len(PREFIX) :].rstrip(";"))
    rows = payload.get("rows", [])
    return rows if isinstance(rows, list) else []


def load_tags() -> dict:
    try:
        payload = json.loads(STOCK_TAGS_PATH.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError):
        return {}


def save_tags(tags: dict) -> None:
    STOCK_TAGS_PATH.write_text(
        json.dumps(tags, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def has_refined_tags(tag: dict) -> bool:
    if not isinstance(tag, dict):
        return False
    return bool(
        tag.get("fineIndustries")
        or tag.get("productTags")
        or tag.get("themeTags")
        or tag.get("groupTags")
    )


def company_name(row: dict) -> str:
    return str(row.get("issuerName") or "").replace("股份有限公司", "").strip()


def unique_issuers(rows: list[dict]) -> dict[str, dict]:
    issuers: dict[str, dict] = {}
    for row in rows:
        code = str(row.get("issuerCode") or "").strip()
        if code and code not in issuers:
            issuers[code] = row
    return issuers


def make_record(row: dict, values: dict) -> dict:
    code = str(row.get("issuerCode") or "").strip()
    name = company_name(row) or str(row.get("issuerName") or "").strip()
    industry = str(row.get("industryCategory") or "").strip()
    return {
        "stockName": name,
        "officialIndustry": industry,
        "fineIndustries": values["fineIndustries"],
        "productTags": values["productTags"],
        "themeTags": values["themeTags"],
        "groupTags": values["groupTags"],
        "confidence": values["confidence"],
        "source": values["source"],
        "updatedAt": today_text(),
        "stockId": code,
        "companyName": name,
        "accuracy": values["accuracy"],
        "sourceNote": values["sourceNote"],
        "sourceUrls": values.get("sourceUrls", []),
    }


def classify(row: dict) -> tuple[dict, str]:
    name = company_name(row)
    issuer_name = str(row.get("issuerName") or "")
    industry = str(row.get("industryCategory") or "")
    text = " ".join(
        str(row.get(key) or "")
        for key in ["issuerName", "bondShortName", "industryCategory", "mainProducts", "businessScope"]
    )

    for rule in NAME_RULES:
        if any(pattern in issuer_name or pattern in name for pattern in rule["patterns"]):
            return {
                "fineIndustries": rule["fineIndustries"],
                "productTags": rule["productTags"],
                "themeTags": rule["themeTags"],
                "groupTags": rule["groupTags"],
                "confidence": rule["confidence"],
                "source": rule["source"],
                "accuracy": rule["accuracy"],
                "sourceNote": rule["sourceNote"],
                "sourceUrls": [],
            }, "matched_company_keyword"

    for keywords, fine, product, theme, group, confidence in KEYWORD_RULES:
        if any(keyword.lower() in text.lower() for keyword in keywords):
            return {
                "fineIndustries": fine,
                "productTags": product,
                "themeTags": theme,
                "groupTags": group,
                "confidence": confidence,
                "source": "keyword",
                "accuracy": "medium",
                "sourceNote": "依 recent-cb-data.js 既有公司/產業/產品文字命中關鍵字規則補標，需定期人工複核。",
                "sourceUrls": [],
            }, "matched_product_keyword"

    for key, (_label, fine, product, theme, group) in OFFICIAL_INDUSTRY_FALLBACKS.items():
        if key in industry:
            return {
                "fineIndustries": fine,
                "productTags": product,
                "themeTags": theme,
                "groupTags": group,
                "confidence": 50,
                "source": "officialIndustryOnly",
                "accuracy": "needs_review",
                "sourceNote": "僅依官方產業保守補標；尚未取得明確產品線資料，需人工複核。",
                "sourceUrls": [],
            }, "official_industry_fallback"

    return {
        "fineIndustries": ["其他"],
        "productTags": [],
        "themeTags": [],
        "groupTags": ["其他"],
        "confidence": 30,
        "source": "fallback",
        "accuracy": "needs_review",
        "sourceNote": "recent-cb-data.js 可用資料不足，暫列其他並待人工複核。",
        "sourceUrls": [],
    }, "insufficient_data"


def write_log(rows: list[dict]) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "checkedAt",
        "stockId",
        "companyName",
        "officialIndustry",
        "action",
        "fineIndustryTags",
        "productTags",
        "themeTags",
        "groupTags",
        "accuracy",
        "sourceNote",
        "reason",
    ]
    with LOG_PATH.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def log_row(code: str, row: dict, action: str, record: dict | None, reason: str) -> dict:
    record = record or {}
    return {
        "checkedAt": datetime.now(TZ).isoformat(),
        "stockId": code,
        "companyName": company_name(row),
        "officialIndustry": row.get("industryCategory") or "",
        "action": action,
        "fineIndustryTags": "、".join(record.get("fineIndustries") or []),
        "productTags": "、".join(record.get("productTags") or []),
        "themeTags": "、".join(record.get("themeTags") or []),
        "groupTags": "、".join(record.get("groupTags") or []),
        "accuracy": record.get("accuracy") or "",
        "sourceNote": record.get("sourceNote") or "",
        "reason": reason,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()

    rows = load_recent_rows()
    tags = load_tags()
    issuers = unique_issuers(rows)
    logs: list[dict] = []
    processed = 0
    enriched = 0
    needs_review = 0

    for code, row in sorted(issuers.items()):
        existing = tags.get(code, {})
        if has_refined_tags(existing):
            logs.append(log_row(code, row, "skipped_existing", existing, "existing_refined_tags"))
            continue
        if processed >= args.limit:
            logs.append(log_row(code, row, "skipped_existing", existing, "limit_reached"))
            continue

        values, reason = classify(row)
        record = make_record(row, values)
        tags[code] = record
        processed += 1
        if values.get("accuracy") in {"high", "medium"}:
            enriched += 1
            action = "enriched"
        else:
            needs_review += 1
            action = "needs_review"
        logs.append(log_row(code, row, action, record, reason))

    if processed:
        save_tags(tags)
    write_log(logs)
    print(
        f"issuers={len(issuers)} processed={processed} enriched={enriched} "
        f"needs_review={needs_review} log={LOG_PATH}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
