from __future__ import annotations

import csv
import json
import re
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "outputs" / "recent-cb-data.js"
INSIGHTS_PATH = ROOT / "outputs" / "company-insights-data.js"
TAGS_PATH = ROOT / "data" / "tw-stock-tags.json"
REVIEW_PATH = ROOT / "outputs" / "tw-stock-tags-review.csv"
PREFIX = "window.RECENT_CB_DATA = "

INDUSTRY_FALLBACKS = {
    "電子零組件業": ("電子零組件", 45),
    "半導體業": ("半導體", 50),
    "電機機械": ("電機機械", 50),
    "光電業": ("光電", 50),
    "建材營造": ("營建", 50),
    "建材營造業": ("營建", 50),
    "生技醫療業": ("生技醫療", 50),
    "綠能環保": ("綠能", 50),
    "電腦及週邊設備業": ("電腦週邊", 50),
    "其他電子業": ("其他電子", 45),
    "通信網路業": ("網通設備", 50),
    "汽車工業": ("汽車零組件", 45),
    "運動休閒": ("運動休閒", 50),
    "觀光餐旅": ("觀光餐旅", 50),
    "觀光事業": ("觀光餐旅", 50),
    "化學工業": ("化工", 50),
    "塑膠工業": ("塑化", 50),
    "紡織纖維": ("紡織", 50),
    "電子通路業": ("電子通路", 50),
    "資訊服務業": ("資訊服務", 50),
    "居家生活": ("居家生活", 45),
    "數位雲端": ("軟體服務", 50),
    "文化創意業": ("文化創意", 50),
    "航運業": ("航運", 50),
    "鋼鐵工業": ("鋼鐵", 50),
    "食品工業": ("食品", 50),
    "橡膠工業": ("橡膠", 50),
    "水泥工業": ("水泥", 50),
    "油電燃氣業": ("油電燃氣", 45),
    "貿易百貨": ("貿易百貨", 50),
    "造紙工業": ("造紙", 50),
    "金融業": ("金融", 50),
    "金融保險業": ("金融", 50),
    "電器電纜": ("電機機械", 45),
    "其他": ("其他", 30),
    "-": ("其他", 30),
}

KEYWORD_RULES = (
    (r"銅箔基板|\bCCL\b|Low\s*D[FK]|高頻高速材料", ["CCL", "高速材料"], ["銅箔基板", "高速材料"]),
    (r"PCB|印刷電路板|電路板|軟性電路|多層板|\bHDI\b", ["PCB"], ["PCB"]),
    (r"連接器|線束|連接線|傳輸線|線組|線纜|Type-?C", ["連接器"], ["連接器", "線材"]),
    (r"MOSFET|二極體|整流器|功率元件|IGBT|\bSiC\b", ["功率元件"], ["功率元件"]),
    (r"電感|MLCC|被動元件|磁性元件|變壓器|線圈", ["被動元件"], ["被動元件", "磁性元件"]),
    (r"散熱|風扇|均熱片|水冷|熱管", ["散熱"], ["散熱"]),
    (r"機殼|滑軌|機構件|機櫃", ["機構件"], ["機構件"]),
    (r"精密金屬|沖壓|樞紐|鉸鏈|轉軸|金屬零件", ["機構件"], ["精密金屬件"]),
    (r"電源供應器|Power\s*Supply|\bPSU\b", ["電源供應器"], ["電源供應器"]),
    (r"光通訊|光收發|光模組", ["光通訊"], ["光通訊"]),
    (r"網通設備|交換器|路由器", ["網通設備"], ["網通設備"]),
    (r"半導體設備|晶圓設備|封裝設備|測試設備", ["半導體設備"], ["半導體設備"]),
    (r"半導體材料|矽晶圓|光阻|特用氣體", ["半導體材料"], ["半導體材料"]),
    (r"封裝|測試|封測", ["半導體封測"], ["封測"]),
    (r"晶圓代工|晶圓製造|半導體製造", ["半導體製造"], ["晶圓製造"]),
    (r"IC設計|控制晶片|USB|\bHub\b|網通晶片", ["IC設計"], ["IC", "控制晶片"]),
    (r"電腦週邊|鍵盤|滑鼠|消費性電子", ["電腦週邊"], ["電腦週邊"]),
    (r"汽車電子|車用電子|汽車零組件|車用零組件", ["汽車零組件"], ["汽車零組件"]),
    (r"控制系統|系統整合|軟體|軟件|資訊系統", ["軟體服務", "資訊服務"], ["系統整合", "軟體服務"]),
    (r"數位學習|線上教育|教育.*軟", ["文化創意", "軟體服務"], ["數位學習"]),
    (r"遊戲|線上遊戲|手機遊戲", ["遊戲"], ["遊戲"]),
    (r"鑄鐵|鑄件|鑄造", ["鋼鐵"], ["鑄件"]),
    (r"工具機|手工具|扳手|刀具", ["工具機"], ["手工具"]),
    (r"自動化.*設備|乾燥設備|雷射.*機|光學檢查|專用機", ["半導體設備"], ["自動化設備"]),
    (r"能源工程|太陽能|風力發電|風電|再生能源", ["綠能"], ["能源工程"]),
    (r"儲能|電池模組|電池系統", ["儲能"], ["儲能系統"]),
    (r"環保|廢棄物|資源回收|水處理", ["環保"], ["環保服務"]),
    (r"TPU|高分子|塑膠|塑料|PET瓶|瓶胚|塑膠地板", ["塑化"], ["高分子材料"]),
    (r"化學材料|化學品|特用化學|樹脂|塗料", ["化工"], ["化學材料"]),
    (r"紡織|成衣|織帶|布料|鞋類|網布", ["紡織"], ["紡織材料"]),
    (r"馬口鐵|鋁罐|金屬包裝|包裝材料|瓶蓋|鋁蓋", ["其他"], ["包裝材料"]),
    (r"運動用品|運動.*手套|護具|健身器材", ["運動休閒"], ["運動用品"]),
    (r"有線電視|電信服務|寬頻|網路服務", ["網通設備"], ["電信服務"]),
    (r"工程規劃|工程設計|工程服務|專案管理|統包工程", ["電機機械"], ["工程服務"]),
    (r"租賃|分期付款|融資", ["金融"], ["租賃服務"]),
    (r"建設|營造|建築工程|不動產開發", ["營建"], ["營建"]),
    (r"水泥|預拌混凝土", ["水泥"], ["水泥"]),
    (r"鋼鐵|鋼材|不鏽鋼", ["鋼鐵"], ["鋼材"]),
    (r"醫療器材|醫材|醫療設備", ["醫材"], ["醫療器材"]),
    (r"製藥|藥品|原料藥|新藥", ["製藥"], ["藥品"]),
    (r"生技|生物科技|細胞治療", ["生技醫療"], ["生技醫療"]),
    (r"食品|飲料|餐飲|農產品", ["食品"], ["食品飲料"]),
    (r"飯店|旅館|餐廳|觀光", ["觀光餐旅"], ["觀光餐旅"]),
    (r"海運|航運|船舶運輸", ["航運"], ["航運"]),
    (r"物流|倉儲|配送", ["物流"], ["物流服務"]),
    (r"百貨|零售|量販|購物中心", ["百貨通路"], ["零售通路"]),
    (r"紙漿|紙張|紙板|紙器", ["造紙"], ["紙製品"]),
    (r"橡膠|輪胎", ["橡膠"], ["橡膠製品"]),
    (r"葬儀|殯葬", ["其他"], ["殯葬服務"]),
)


def load_recent_rows() -> list[dict]:
    text = DATA_PATH.read_text(encoding="utf-8").strip()
    return json.loads(text[len(PREFIX) :].rstrip(";")).get("rows", [])


def load_existing_tags() -> dict:
    try:
        payload = json.loads(TAGS_PATH.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError):
        return {}


def load_company_insights() -> dict:
    try:
        text = INSIGHTS_PATH.read_text(encoding="utf-8").strip()
        prefix = "window.CB_COMPANY_INSIGHTS = "
        return json.loads(text[len(prefix) :].rstrip(";"))
    except (OSError, ValueError):
        return {}


def company_name(row: dict) -> str:
    name = str(row.get("issuerName") or row.get("issuerCode") or "").strip()
    for suffix in ("股份有限公司", "有限公司", "公司"):
        if name.endswith(suffix):
            return name[: -len(suffix)].strip()
    return name


def descriptive_text(row: dict, insight: dict) -> str:
    values = [
        row.get("mainProducts"),
        row.get("primaryProductsServices"),
        row.get("companyDescription"),
        row.get("businessDescription"),
        insight.get("primaryProductsServices"),
        insight.get("bestMarginProduct"),
    ]
    values.extend(row.get("productTags") or [])
    return " ".join(str(value) for value in values if value)


def classify(row: dict, insight: dict) -> dict:
    official = str(row.get("industryCategory") or "-").strip()
    text = descriptive_text(row, insight)
    fine_matches = []
    product_matches = []
    for pattern, fine, products in KEYWORD_RULES:
        if text and re.search(pattern, text, re.IGNORECASE):
            fine_matches.extend(value for value in fine if value not in fine_matches)
            product_matches.extend(value for value in products if value not in product_matches)
    if fine_matches:
        theme_patterns = {
            "AI伺服器": r"AI伺服器|AI\s*server",
            "網通": r"網通|交換器|路由器",
            "高速傳輸": r"高速傳輸|高頻高速|Low\s*D[FK]",
            "車用": r"車用|汽車",
            "資料中心": r"資料中心|data\s*center",
            "綠能": r"綠能|太陽能|風電",
            "儲能": r"儲能",
            "電動車": r"電動車|充電樁|充電線",
            "5G": r"\b5G\b|第五代行動通訊",
            "低軌衛星": r"低軌衛星|衛星通訊",
            "智慧製造": r"自動化|智慧製造|工業4\.0",
            "生技醫療": r"生技|醫療|製藥|藥品",
        }
        themes = [name for name, pattern in theme_patterns.items() if re.search(pattern, text, re.IGNORECASE)]
        return {
            "stockName": company_name(row),
            "officialIndustry": official,
            "fineIndustries": fine_matches,
            "productTags": product_matches,
            "themeTags": themes,
            "confidence": 78,
            "source": "webKeyword",
            "updatedAt": date.today().isoformat(),
        }
    fine, confidence = INDUSTRY_FALLBACKS.get(official, ("其他", 30))
    return {
        "stockName": company_name(row),
        "officialIndustry": official,
        "fineIndustries": [fine],
        "productTags": [],
        "themeTags": [],
        "confidence": confidence,
        "source": "officialIndustryOnly" if fine != "其他" else "fallback",
        "updatedAt": date.today().isoformat(),
    }


def review_note(item: dict) -> str:
    if item["fineIndustries"] == ["其他"]:
        return "官方產業不足，需人工確認"
    if item["fineIndustries"] == ["電子零組件"]:
        return "電子零組件業缺少主要產品線索"
    return "目前僅依官方產業保守分類"


def main() -> int:
    rows = load_recent_rows()
    issuers = {}
    for row in rows:
        code = str(row.get("issuerCode") or "").strip()
        if code and code not in issuers:
            issuers[code] = row

    existing = load_existing_tags()
    insights = load_company_insights()
    tags = {}
    for code, row in issuers.items():
        old = existing.get(code, {})
        if old.get("source") == "manual" and (old.get("confidence") or 0) >= 90:
            tags[code] = old
        else:
            tags[code] = classify(row, insights.get(code, {}))

    tags = dict(sorted(tags.items(), key=lambda item: (not item[0].isdigit(), int(item[0]) if item[0].isdigit() else item[0])))
    TAGS_PATH.write_text(json.dumps(tags, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    review = [(code, item) for code, item in tags.items() if item["confidence"] < 70]
    review.sort(
        key=lambda pair: (
            0 if pair[1]["fineIndustries"] == ["其他"] else 1 if pair[1]["fineIndustries"] == ["電子零組件"] else 2,
            pair[1]["confidence"],
            pair[0],
        )
    )
    with REVIEW_PATH.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["stockCode", "stockName", "officialIndustry", "fineIndustries", "productTags", "themeTags", "confidence", "source", "note"],
        )
        writer.writeheader()
        for code, item in review:
            writer.writerow(
                {
                    "stockCode": code,
                    "stockName": item["stockName"],
                    "officialIndustry": item["officialIndustry"],
                    "fineIndustries": "|".join(item["fineIndustries"]),
                    "productTags": "|".join(item["productTags"]),
                    "themeTags": "|".join(item["themeTags"]),
                    "confidence": item["confidence"],
                    "source": item["source"],
                    "note": review_note(item),
                }
            )

    missing = [row.get("bondCode") for row in rows if not tags.get(str(row.get("issuerCode") or ""), {}).get("fineIndustries")]
    print(f"CB rows: {len(rows)}; issuers: {len(issuers)}; tags: {len(tags)}; missing: {len(missing)}")
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
