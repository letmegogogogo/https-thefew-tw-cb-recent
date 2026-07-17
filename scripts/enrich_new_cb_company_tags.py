from __future__ import annotations

import argparse
import csv
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RECENT_DATA_PATH = ROOT / "outputs" / "recent-cb-data.js"
STOCK_TAGS_PATH = ROOT / "data" / "tw-stock-tags.json"
LOG_PATH = ROOT / "outputs" / "new-cb-company-tags-log.csv"
PREFIX = "window.RECENT_CB_DATA = "
TZ = timezone(timedelta(hours=8))

GENERIC_TAGS = {"", "其他", "電子零組件", "其他電子", "半導體"}
INCOMPLETE_SOURCES = {"", "fallback", "unknown", "officialIndustryOnly"}

# Verified company-specific seeds. These are intentionally small and sourced.
# Existing manual/high-confidence records are never overwritten.
COMPANY_RULES = {
    "8054": {
        "fineIndustries": ["IC設計", "ASIC設計"],
        "productTags": ["ASIC", "SoC", "IC設計服務"],
        "themeTags": ["AI晶片", "資料中心", "邊緣運算"],
        "groupTags": ["IC設計"],
        "confidence": 90,
        "accuracy": "high",
        "source": "official_web",
        "sourceNote": "依安國官方網站 ASIC、SoC 設計服務與 AI 晶片解決方案資訊整理。",
        "sourceUrls": [
            "https://www.alcormicro.com/zh-tw/",
            "https://ic.tpex.org.tw/company_chain.php?stk_code=8054",
        ],
    },
    "3028": {
        "fineIndustries": ["電子通路", "半導體通路"],
        "productTags": ["半導體元件", "記憶體", "MCU", "通訊元件"],
        "themeTags": ["物聯網", "資料中心", "電子供應鏈"],
        "groupTags": ["電子通路"],
        "confidence": 90,
        "accuracy": "high",
        "source": "official_web",
        "sourceNote": "依增你強官方公司介紹與產品線資訊整理。",
        "sourceUrls": [
            "https://www.zenitron.com.tw/tw/about/overview",
            "https://www.zenitron.com.tw/tw/products",
        ],
    },
    "6134": {
        "fineIndustries": ["連接線組", "天線"],
        "productTags": ["連接線組", "線材", "天線", "連接器"],
        "themeTags": ["網通", "自動化生產", "車用電子"],
        "groupTags": ["連接器線材"],
        "confidence": 90,
        "accuracy": "high",
        "source": "official_web",
        "sourceNote": "依萬旭電業官方公司簡介、產品型錄與法說資料整理。",
        "sourceUrls": [
            "https://www.wanshih.com.tw/list/company-profile.htm",
            "https://www.wanshih.com.tw/uploadfiles/973/catalog/2023-wanshih-e-catalogue_zh_views.pdf",
        ],
    },
    "1623": {
        "fineIndustries": ["電線電纜", "高壓電纜"],
        "productTags": ["電力電纜", "高壓電纜", "特高壓電纜", "橡膠電纜"],
        "themeTags": ["電網韌性", "AI資料中心", "電力基礎建設"],
        "groupTags": ["電線電纜"],
        "confidence": 90,
        "accuracy": "high",
        "source": "official_web",
        "sourceNote": "依 TWSE 公司資料、TWSE 新上市公司介紹及公司官網產品資訊整理。",
        "sourceUrls": [
            "https://www.twse.com.tw/pdf/ch/1623_ch.pdf",
            "https://www.twse.com.tw/market_insights/zh/detail/8a8216d69a3d6cf9019bd41f63d0076b",
            "https://tewc.com.tw/",
        ],
    },
    "6693": {
        "fineIndustries": ["IC設計", "功率半導體", "馬達驅動IC"],
        "productTags": ["Power MOSFET", "BLDC馬達驅動IC", "SoC散熱風扇驅動IC", "SiC二極體"],
        "themeTags": ["AI伺服器散熱", "節能", "電源管理", "馬達控制"],
        "groupTags": ["IC設計", "功率元件"],
        "confidence": 95,
        "accuracy": "high",
        "source": "official_web",
        "sourceNote": "依廣閎科技官網公司介紹與產品頁整理；核心產品包含功率MOSFET、BLDC馬達驅動與SoC散熱風扇驅動IC。",
        "sourceUrls": [
            "https://www.inergy.com.tw/about/",
            "https://www.inergy.com.tw/",
        ],
    },
    "6903": {
        "fineIndustries": ["無塵室工程", "機電工程", "廠務工程"],
        "productTags": ["中央空調工程", "潔淨室工程", "監控自動化", "高低壓配電", "消防工程"],
        "themeTags": ["半導體廠務", "AI資料中心", "智慧建築", "BIM"],
        "groupTags": ["廠務工程", "機電工程"],
        "confidence": 95,
        "accuracy": "high",
        "source": "official_web",
        "sourceNote": "依巨漢系統官網公司介紹與服務項目整理；提供中央空調、潔淨室、監控自動化、配電與消防整合工程。",
        "sourceUrls": [
            "https://www.jiuhan.com.tw/",
            "https://www.jiuhan.com.tw/service",
        ],
    },
    "6869": {
        "fineIndustries": ["再生能源", "能源整合服務"],
        "productTags": ["太陽光電", "儲能系統", "綠電交易", "風力發電", "水資源"],
        "themeTags": ["綠能", "儲能", "能源轉型", "淨零碳排"],
        "groupTags": ["綠能", "能源服務"],
        "confidence": 95,
        "accuracy": "high",
        "source": "official_web",
        "sourceNote": "依雲豹能源官網公司介紹與服務內容整理；業務涵蓋再生能源開發、儲能、綠電交易及水資源。",
        "sourceUrls": [
            "https://www.jv-holding.com/about.aspx",
            "https://www.jv-holding.com/",
        ],
    },
    "8442": {
        "fineIndustries": ["運動用品製造", "精品包袋"],
        "productTags": ["運動護具", "戶外運動裝備", "精品包袋", "機能性包袋"],
        "themeTags": ["運動休閒", "品牌代工", "消費升級"],
        "groupTags": ["運動用品", "包袋製造"],
        "confidence": 95,
        "accuracy": "high",
        "source": "official_web",
        "sourceNote": "依威宏控股官網公司資訊與營收公告整理；主要從事專業運動裝備及精品、機能包袋製造。",
        "sourceUrls": [
            "https://www.ww-holding.com.tw/",
            "https://www.ww-holding.com.tw/blog/",
        ],
    },
    "3294": {
        "fineIndustries": ["精密塑膠零組件", "機光電整合"],
        "productTags": ["精密塑膠射出", "模具", "機光電模組", "生醫器材"],
        "themeTags": ["AI應用", "智慧醫療", "雷射光電"],
        "groupTags": ["精密零組件", "機光電整合"],
        "confidence": 95,
        "accuracy": "high",
        "source": "official_web",
        "sourceNote": "依英濟官網公司介紹與事業內容整理；提供高精密塑膠零組件製造、模具及機光電整合服務。",
        "sourceUrls": [
            "https://www.megaforce.com.tw/zh-tw/Home/index",
            "https://www.megaforce.com.tw/zh-tw/Product/Product",
        ],
    },
    "1717": {
        "fineIndustries": ["合成樹脂", "電子材料", "特用材料"],
        "productTags": ["合成樹脂", "UV材料", "PCB光阻材料", "光電材料", "鋰電池材料"],
        "themeTags": ["電子材料", "半導體材料", "綠色化學", "材料國產化"],
        "groupTags": ["化工材料", "電子材料"],
        "confidence": 95,
        "accuracy": "high",
        "source": "official_web",
        "sourceNote": "依長興材料官網產品資訊整理；產品涵蓋合成樹脂、特用材料、電子材料與光阻材料。",
        "sourceUrls": [
            "https://www.eternal-group.com/Product",
            "https://www.eternal.com.tw/",
        ],
    },
}

KEYWORD_RULES = [
    (["PCB", "印刷電路板", "電路板", "多層板", "HDI"], ["PCB"], ["PCB", "多層板", "HDI"], ["AI伺服器", "高速傳輸"], ["PCB"], 75),
    (["銅箔基板", "CCL", "Low DK", "Low DF", "高頻高速材料"], ["CCL", "高速材料"], ["銅箔基板", "高速材料"], ["AI伺服器", "高速傳輸"], ["高速材料"], 80),
    (["連接器", "線束", "Type-C"], ["連接器"], ["連接器", "線束", "Type-C"], ["高速傳輸", "車用"], ["連接器"], 75),
    (["MOSFET", "二極體", "整流器", "功率元件", "IGBT", "SiC"], ["功率元件"], ["功率元件", "MOSFET", "二極體"], ["AI電源", "車用"], ["功率元件"], 75),
    (["電感", "MLCC", "被動元件", "磁性元件"], ["被動元件"], ["被動元件", "電感", "MLCC"], ["AI電源", "車用"], ["被動元件"], 75),
    (["散熱", "風扇", "均熱片", "水冷", "熱管"], ["散熱"], ["散熱模組", "風扇", "熱管"], ["AI伺服器", "液冷散熱"], ["散熱"], 80),
    (["電線", "電纜", "高壓電纜", "特高壓電纜"], ["電線電纜"], ["電線", "電纜", "高壓電纜"], ["電網韌性", "電力基礎建設"], ["電線電纜"], 75),
]

INDUSTRY_RULES = {
    "電器電纜": (["電線電纜"], ["電線", "電纜"], ["電網韌性"], ["電線電纜"]),
    "半導體業": (["半導體"], ["半導體"], [], ["半導體"]),
    "電子零組件業": (["電子零組件"], ["電子零組件"], [], ["電子零組件"]),
    "其他電子業": (["其他電子"], ["電子設備"], [], ["其他電子"]),
    "通信網路業": (["網通設備"], ["網通設備"], ["網通"], ["網通設備"]),
    "資訊服務業": (["資訊服務"], ["資訊服務"], ["數位轉型"], ["資訊服務"]),
    "電腦及週邊設備業": (["電腦週邊"], ["電腦週邊"], ["AI伺服器"], ["電腦週邊"]),
    "生技醫療業": (["生技醫療"], ["生技醫療"], [], ["生技醫療"]),
    "建材營造業": (["營建"], ["營建工程"], [], ["營建"]),
    "觀光事業": (["觀光餐旅"], ["觀光餐旅"], [], ["觀光餐旅"]),
    "食品工業": (["食品"], ["食品"], [], ["食品"]),
    "化學工業": (["化工"], ["化學品"], [], ["化工"]),
    "塑膠工業": (["塑化"], ["塑膠製品"], [], ["塑化"]),
    "紡織纖維": (["紡織"], ["紡織品"], [], ["紡織"]),
    "鋼鐵工業": (["鋼鐵"], ["鋼鐵製品"], [], ["鋼鐵"]),
    "航運業": (["航運"], ["航運服務"], [], ["航運"]),
    "電機機械": (["電機機械"], ["機械設備"], [], ["電機機械"]),
    "金融保險業": (["金融"], ["金融服務"], [], ["金融"]),
}


def today_text() -> str:
    return datetime.now(TZ).date().isoformat()


def parse_js(path: Path, prefix: str) -> dict:
    text = path.read_text(encoding="utf-8-sig").strip()
    if not text.startswith(prefix):
        raise ValueError(f"invalid JS data: {path}")
    return json.loads(text[len(prefix):].rstrip(";"))


def load_tags() -> dict:
    try:
        data = json.loads(STOCK_TAGS_PATH.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def meaningful(values) -> list[str]:
    return [str(value).strip() for value in (values or []) if str(value).strip()]


def has_refined_tags(tag: dict) -> bool:
    """Only verified, useful tags count as complete; fallback '其他' must retry."""
    if not isinstance(tag, dict):
        return False
    fine = meaningful(tag.get("fineIndustries"))
    products = meaningful(tag.get("productTags"))
    groups = meaningful(tag.get("groupTags"))
    source = str(tag.get("source") or "").strip()
    confidence = int(tag.get("confidence") or 0)
    useful = any(value not in GENERIC_TAGS for value in fine + products + groups)
    return source not in INCOMPLETE_SOURCES and confidence >= 60 and useful and bool(products)


def clean_company_name(value: str) -> str:
    return re.sub(r"(股份有限公司|有限公司)$", "", str(value or "").strip())


def issue_priority(row: dict) -> tuple:
    text = str(row.get("issueDate") or row.get("listingDate") or "")[:10]
    try:
        issue = date.fromisoformat(text)
    except ValueError:
        issue = date.min
    today = datetime.now(TZ).date()
    upcoming = issue >= today
    return (0 if upcoming else 1, abs((issue - today).days), str(row.get("issuerCode") or ""))


def record_for(row: dict, values: dict) -> dict:
    code = str(row.get("issuerCode") or "").strip()
    name = clean_company_name(row.get("issuerName"))
    return {
        "stockName": name,
        "officialIndustry": str(row.get("industryCategory") or "").strip(),
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
    code = str(row.get("issuerCode") or "").strip()
    if code in COMPANY_RULES:
        return COMPANY_RULES[code], "verified_company_rule"
    text = " ".join(str(row.get(key) or "") for key in (
        "issuerName", "bondShortName", "industryCategory", "mainProducts", "businessScope"
    ))
    for keywords, fine, product, theme, group, confidence in KEYWORD_RULES:
        if any(keyword.lower() in text.lower() for keyword in keywords):
            return {
                "fineIndustries": fine, "productTags": product, "themeTags": theme,
                "groupTags": group, "confidence": confidence, "source": "keyword",
                "accuracy": "medium", "sourceNote": "依現有官方產業及主要產品關鍵字初步分類。",
                "sourceUrls": [],
            }, "matched_product_keyword"
    industry = str(row.get("industryCategory") or "").strip()
    for key, (fine, product, theme, group) in INDUSTRY_RULES.items():
        if key in industry:
            return {
                "fineIndustries": fine, "productTags": product, "themeTags": theme,
                "groupTags": group, "confidence": 55, "source": "officialIndustryOnly",
                "accuracy": "needs_review", "sourceNote": "僅依官方產業保守分類，將於後續更新繼續精修。",
                "sourceUrls": [],
            }, "official_industry_fallback"
    return {
        "fineIndustries": ["其他"], "productTags": [], "themeTags": [], "groupTags": ["其他"],
        "confidence": 30, "source": "fallback", "accuracy": "needs_review",
        "sourceNote": "現有公開欄位不足，保留待查且下次更新會繼續處理。", "sourceUrls": [],
    }, "insufficient_data"


def write_log(items: list[dict]) -> None:
    fields = ["checkedAt", "stockId", "companyName", "officialIndustry", "action",
              "fineIndustryTags", "productTags", "themeTags", "groupTags", "accuracy",
              "sourceNote", "reason"]
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(items)


def log_row(code: str, row: dict, action: str, record: dict, reason: str) -> dict:
    return {
        "checkedAt": datetime.now(TZ).isoformat(), "stockId": code,
        "companyName": clean_company_name(row.get("issuerName")),
        "officialIndustry": row.get("industryCategory") or "", "action": action,
        "fineIndustryTags": "、".join(record.get("fineIndustries") or []),
        "productTags": "、".join(record.get("productTags") or []),
        "themeTags": "、".join(record.get("themeTags") or []),
        "groupTags": "、".join(record.get("groupTags") or []),
        "accuracy": record.get("accuracy") or "", "sourceNote": record.get("sourceNote") or "",
        "reason": reason,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--codes", default="", help="Comma-separated issuer stock codes")
    args = parser.parse_args()
    requested = {value.strip() for value in args.codes.split(",") if value.strip()}
    rows = parse_js(RECENT_DATA_PATH, PREFIX).get("rows", [])
    issuers: dict[str, dict] = {}
    for row in rows:
        code = str(row.get("issuerCode") or "").strip()
        if code and code not in issuers:
            issuers[code] = row
    tags = load_tags()
    logs: list[dict] = []
    processed = enriched = needs_review = 0
    candidates = sorted(issuers.items(), key=lambda item: issue_priority(item[1]))
    for code, row in candidates:
        if requested and code not in requested:
            continue
        existing = tags.get(code, {})
        if has_refined_tags(existing):
            logs.append(log_row(code, row, "skipped_existing", existing, "existing_verified_tags"))
            continue
        if not requested and code not in COMPANY_RULES and str(existing.get("updatedAt") or "") == today_text():
            logs.append(log_row(code, row, "deferred", existing, "checked_today_retry_next_day"))
            continue
        if processed >= args.limit:
            logs.append(log_row(code, row, "deferred", existing, "limit_reached_retry_next_run"))
            continue
        values, reason = classify(row)
        record = record_for(row, values)
        tags[code] = record
        processed += 1
        action = "enriched" if has_refined_tags(record) else "needs_review"
        enriched += action == "enriched"
        needs_review += action == "needs_review"
        logs.append(log_row(code, row, action, record, reason))
    if processed:
        STOCK_TAGS_PATH.write_text(json.dumps(tags, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_log(logs)
    print(f"issuers={len(issuers)} processed={processed} enriched={enriched} needs_review={needs_review}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
