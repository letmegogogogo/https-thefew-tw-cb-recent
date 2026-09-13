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
    "8462": {
        "fineIndustries": ["連鎖健身中心", "運動健康服務"],
        "productTags": ["健身房會員", "私人教練課程", "運動保健服務", "兒童體適能"],
        "themeTags": ["內需消費", "健康管理", "運動休閒"],
        "groupTags": ["健身中心", "運動服務"],
        "confidence": 90,
        "accuracy": "high",
        "source": "official_web",
        "sourceNote": "依柏文公開法說與公司資料整理；核心服務為健身工廠會員制健身中心、私人教練與運動健康服務。",
        "sourceUrls": [
            "https://www.fitnessfactory.com.tw/",
            "https://tw.stock.yahoo.com/quote/8462.TW/profile",
        ],
    },
    "1598": {
        "fineIndustries": ["健身器材", "運動休閒設備"],
        "productTags": ["跑步機", "室內健身車", "橢圓機", "按摩椅", "商用健身器材"],
        "themeTags": ["運動休閒", "健康管理", "居家健身"],
        "groupTags": ["健身器材", "運動用品"],
        "confidence": 90,
        "accuracy": "high",
        "source": "official_web",
        "sourceNote": "依岱宇國際公司產品與公開資料整理；主要產品為家用及商用健身器材與健康休閒設備。",
        "sourceUrls": [
            "https://www.dyaco.com/",
            "https://tw.stock.yahoo.com/quote/1598.TW/profile",
        ],
    },
    "9802": {
        "fineIndustries": ["製鞋代工", "戶外功能鞋"],
        "productTags": ["登山鞋", "戶外鞋", "運動鞋", "雪靴", "鞋類ODM"],
        "themeTags": ["戶外休閒", "製鞋供應鏈", "品牌代工"],
        "groupTags": ["製鞋", "運動用品"],
        "confidence": 90,
        "accuracy": "high",
        "source": "official_web",
        "sourceNote": "依鈺齊-KY公開法說與公司資料整理；主要從事戶外功能鞋、運動鞋及多功能鞋款開發代工。",
        "sourceUrls": [
            "https://www.yueyuen.com/",
            "https://tw.stock.yahoo.com/quote/9802.TW/profile",
        ],
    },
    "8478": {
        "fineIndustries": ["豪華遊艇", "高端休閒船舶"],
        "productTags": ["Ocean Alexander", "大型遊艇", "豪華遊艇", "遊艇售後服務"],
        "themeTags": ["高端消費", "美元營收", "北美市場"],
        "groupTags": ["遊艇", "高端休閒"],
        "confidence": 90,
        "accuracy": "high",
        "source": "official_web",
        "sourceNote": "依東哥遊艇公開資料整理；主要製造與銷售 Ocean Alexander 豪華遊艇並提供售後服務。",
        "sourceUrls": [
            "https://oceanalexander.com/zh-hans/",
            "https://tw.stock.yahoo.com/quote/8478.TWO/profile",
        ],
    },
    "8467": {
        "fineIndustries": ["碳纖維運動用品", "球拍製造"],
        "productTags": ["羽球拍", "網球拍", "曲棍球桿", "碳纖維球拍", "運動用品ODM"],
        "themeTags": ["運動休閒", "碳纖維材料", "品牌代工"],
        "groupTags": ["運動用品", "碳纖維"],
        "confidence": 90,
        "accuracy": "high",
        "source": "official_web",
        "sourceNote": "依波力官方公司介紹與產品分類整理；主要產品包含碳纖維球拍、曲棍球桿及相關運動用品。",
        "sourceUrls": [
            "https://www.bonnygo.com.tw/tw/about/index.aspx",
            "https://bonnyworldwide.com/tw/product/index.aspx",
        ],
    },
    "8433": {
        "fineIndustries": ["流行飾品", "美容美髮用品", "個人護理電器"],
        "productTags": ["髮飾", "梳鏡", "珠寶配飾", "手袋", "美容美髮電器", "跨境電商"],
        "themeTags": ["美妝個護", "生活消費", "跨境電商"],
        "groupTags": ["流行飾品", "美妝個護"],
        "confidence": 90,
        "accuracy": "high",
        "source": "official_web",
        "sourceNote": "依弘帆公開法說與公司資料整理；主要從事流行髮飾、梳鏡、珠寶、手袋、美容美髮及個護電器等產品。",
        "sourceUrls": [
            "http://www.bonfame.com/",
            "https://tw.stock.yahoo.com/quote/8433.TWO/profile",
        ],
    },
    "8927": {
        "fineIndustries": ["加油站通路", "油品零售"],
        "productTags": ["汽柴油零售", "加油站", "潤滑油", "洗車服務", "液化石油氣"],
        "themeTags": ["能源通路", "油價", "內需消費"],
        "groupTags": ["油品通路", "能源服務"],
        "confidence": 90,
        "accuracy": "high",
        "source": "official_web",
        "sourceNote": "依北基公開公司資料整理；主要經營加油站汽柴油零售、汽機車潤滑保養、洗車與液化石油氣等服務。",
        "sourceUrls": [
            "http://www.nspco.com.tw",
            "https://tw.stock.yahoo.com/quote/8927.TWO/profile",
        ],
    },
    "1909": {
        "fineIndustries": ["工業用紙", "包裝紙"],
        "productTags": ["工業用紙", "瓦楞紙箱", "紙器包裝", "回收紙", "紙漿"],
        "themeTags": ["循環經濟", "包裝材料", "原物料成本"],
        "groupTags": ["造紙", "包裝材料"],
        "confidence": 90,
        "accuracy": "high",
        "source": "official_web",
        "sourceNote": "依榮成紙業公開公司資料整理；主要產品為工業用紙、紙器包裝及回收紙相關業務。",
        "sourceUrls": [
            "https://www.longchenpaper.com/",
            "https://tw.stock.yahoo.com/quote/1909.TW/profile",
        ],
    },
    "3016": {
        "fineIndustries": ["矽晶圓", "磊晶晶圓"],
        "productTags": ["矽磊晶圓", "再生晶圓", "晶圓材料", "半導體材料"],
        "themeTags": ["半導體材料", "先進製程", "晶圓供應鏈"],
        "groupTags": ["半導體材料", "矽晶圓"],
        "confidence": 90,
        "accuracy": "high",
        "source": "official_web",
        "sourceNote": "依嘉晶電子公開公司資料整理；主要從事矽磊晶圓與半導體晶圓材料相關業務。",
        "sourceUrls": [
            "https://www.epi.episil.com/",
            "https://tw.stock.yahoo.com/quote/3016.TW/profile",
        ],
    },
    "3583": {
        "fineIndustries": ["半導體設備", "濕製程設備", "晶圓再生"],
        "productTags": ["濕製程設備", "晶圓再生服務", "半導體製程設備", "自動化設備", "再生晶圓"],
        "themeTags": ["CoWoS擴產", "先進封裝", "半導體設備國產化", "先進製程"],
        "groupTags": ["半導體設備", "晶圓再生", "先進封裝"],
        "confidence": 95,
        "accuracy": "high",
        "source": "official_web",
        "sourceNote": "依辛耘公開公司資料整理；業務涵蓋半導體濕製程設備、製程設備代理與晶圓再生服務。",
        "sourceUrls": [
            "https://www.scientech.com.tw/",
            "https://tw.stock.yahoo.com/quote/3583.TW/profile",
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
    "2303": {
        "fineIndustries": ["晶圓代工", "特殊製程"],
        "productTags": ["邏輯製程", "混合訊號製程", "嵌入式高壓製程", "BCD製程", "RFSOI"],
        "themeTags": ["成熟製程", "車用半導體", "物聯網", "晶圓代工"],
        "groupTags": ["晶圓代工", "半導體製造"],
        "confidence": 95,
        "accuracy": "high",
        "source": "official_web",
        "sourceNote": "依聯華電子官網與公開公司資料整理；公司為全球晶圓代工廠，提供邏輯、混合訊號、高壓、嵌入式非揮發性記憶體、RFSOI與BCD等製程。",
        "sourceUrls": [
            "https://www.umc.com/zh-TW/home/Index",
            "https://www.umc.com/zh-TW/News/press_release/Content/corporate/20240430",
        ],
    },
    "3219": {
        "fineIndustries": ["自動化測試設備", "5G毫米波天線"],
        "productTags": ["功能測試設備", "射頻測試", "組裝測試", "可靠度測試", "毫米波天線模組"],
        "themeTags": ["AI伺服器測試", "車用電子", "5G毫米波", "自動化檢測"],
        "groupTags": ["測試設備", "通訊元件"],
        "confidence": 90,
        "accuracy": "high",
        "source": "official_web",
        "sourceNote": "依倚強科技官網與公開法說資料整理；公司提供消費電子、車用電子、AI伺服器等自動化測試解決方案，並布局 5G 毫米波天線模組。",
        "sourceUrls": [
            "https://www.aether-tek.com/zh_cn/aethertek-story/",
            "https://www.aether-tek.com/zh/%E5%80%9A%E5%BC%B7%E7%A7%91%E6%8A%80%E8%82%A1%E4%BB%BD%E6%9C%89%E9%99%90%E5%85%AC%E5%8F%B8-%E8%82%A1%E7%A5%A8%E4%BB%A3%E8%99%9F%EF%BC%9A3219-%E3%80%8C2024%E7%AC%AC%E4%BA%8C%E5%AD%A3%E7%87%9F/",
        ],
    },
    "3512": {
        "fineIndustries": ["住宅建設", "土地開發"],
        "productTags": ["住宅大樓", "土地開發", "不動產銷售", "營建工程"],
        "themeTags": ["房市", "台南建案", "土地資產", "都更"],
        "groupTags": ["建設開發", "營建"],
        "confidence": 90,
        "accuracy": "high",
        "source": "official_web",
        "sourceNote": "依皇龍開發官網與公開公司資料整理；主要業務為土地開發、住宅及大樓興建與銷售。",
        "sourceUrls": [
            "https://www.hldc.com.tw/about/",
            "https://www.hldc.com.tw/about/about_us__1/",
        ],
    },
    "4541": {
        "fineIndustries": ["航太精密零件", "五軸加工"],
        "productTags": ["航太發動機零件", "起落架零件", "渦輪機匣", "齒輪箱零件", "CNC精密加工"],
        "themeTags": ["航太供應鏈", "航太發動機", "精密加工", "國防航太"],
        "groupTags": ["航太零組件", "精密加工"],
        "confidence": 95,
        "accuracy": "high",
        "source": "official_web",
        "sourceNote": "依晟田科技官網與公開法說資料整理；公司聚焦航太發動機、起落架、飛控致動器等精密零件加工。",
        "sourceUrls": [
            "https://www.maicl.com/index.php?inter=about",
            "https://www.maicl.com/index.php?inter=application&nc_id=3",
        ],
    },
    "4973": {
        "fineIndustries": ["記憶體品牌", "儲存裝置"],
        "productTags": ["SSD", "記憶卡", "隨身碟", "DRAM模組", "外接式硬碟", "工業用記憶體"],
        "themeTags": ["記憶體模組", "邊緣儲存", "消費電子", "工業儲存"],
        "groupTags": ["記憶體", "儲存裝置"],
        "confidence": 90,
        "accuracy": "high",
        "source": "official_web",
        "sourceNote": "依廣穎電通官網與公司簡介整理；公司經營自有品牌 Silicon Power，產品涵蓋記憶卡、隨身碟、SSD、記憶體模組、外接硬碟與工業用記憶體。",
        "sourceUrls": [
            "https://www.silicon-power.com/tw/",
            "https://www.silicon-power.com/web/cn/company_profile",
        ],
    },
    "2221": {
        "fineIndustries": ["航太扣件", "金屬扣件"],
        "productTags": ["螺帽", "航太扣件", "汽車扣件", "工業扣件", "精密金屬零件"],
        "themeTags": ["航太供應鏈", "汽車零組件", "扣件", "精密加工"],
        "groupTags": ["扣件", "航太零組件"],
        "confidence": 85,
        "accuracy": "medium",
        "source": "public_disclosure",
        "sourceNote": "依公開公司資料與產業分類整理；公司主要從事螺帽、扣件與金屬零件製造，應用於汽車、航太及工業市場。",
        "sourceUrls": [
            "https://mops.twse.com.tw/mops/web/index",
            "https://tw.stock.yahoo.com/quote/2221.TWO/profile",
        ],
    },
    "6683": {
        "fineIndustries": ["IC測試載板", "半導體測試介面"],
        "productTags": ["Load Board", "Burn-in Board", "Probe Card", "IC Socket", "測試載板"],
        "themeTags": ["半導體測試", "AI晶片測試", "先進封測", "晶圓測試"],
        "groupTags": ["半導體測試", "測試載板"],
        "confidence": 90,
        "accuracy": "high",
        "source": "official_web",
        "sourceNote": "依雍智科技公開資料整理；主要產品為 IC 測試載板、老化測試板、探針卡及相關半導體測試介面產品。",
        "sourceUrls": [
            "https://www.ksmt.com.tw",
            "https://tw.finance.yahoo.com/quote/6683/profile",
        ],
    },
    "7631": {
        "fineIndustries": ["半導體廠務工程", "特殊氣體二次配"],
        "productTags": ["特殊氣體管路", "廠務供應系統工程", "製程設備零組件", "管路自動雷射焊接機"],
        "themeTags": ["半導體廠務", "先進製程", "海外擴廠", "智慧製造"],
        "groupTags": ["廠務工程", "半導體設備"],
        "confidence": 95,
        "accuracy": "high",
        "source": "official_web",
        "sourceNote": "依聚賢研發官網與 TWSE 新上市公司介紹整理；公司聚焦高科技廠房廠務供應系統工程、特殊氣體二次配及設備零組件開發。",
        "sourceUrls": [
            "https://www.geniideas.com.tw/tw/service",
            "https://wwwc.twse.com.tw/market_insights/zh/detail/8a8216d6956a7ba201957dd77499004e",
        ],
    },
    "3149": {
        "fineIndustries": ["光電玻璃加工", "保護玻璃"],
        "productTags": ["薄化玻璃", "強化玻璃", "鍍膜玻璃", "保護玻璃", "3D成型玻璃", "節能玻璃"],
        "themeTags": ["光電玻璃", "消費電子", "綠建築", "車載顯示"],
        "groupTags": ["光電玻璃", "光學元件"],
        "confidence": 90,
        "accuracy": "high",
        "source": "official_web",
        "sourceNote": "依正達國際光電官網投資人問答整理；主要產品為手機、平板、一體成型電腦、電視等終端產品使用之光電玻璃。",
        "sourceUrls": [
            "https://www.gtoc.com.tw/webc/html/investor/index.aspx?nav=6",
        ],
    },
    "8936": {
        "fineIndustries": ["水利管材", "管線工程"],
        "productTags": ["鋼管", "延性鑄鐵管", "輸配水管線", "推進管", "海水淡化工程", "淨污水處理"],
        "themeTags": ["水資源", "公共工程", "基礎建設", "水利工程"],
        "groupTags": ["水利工程", "管材"],
        "confidence": 95,
        "accuracy": "high",
        "source": "official_web",
        "sourceNote": "依國統國際官網公司介紹整理；公司專事大口徑輸配水管線設計、製造及裝配，並擴及水處理與管線工程。",
        "sourceUrls": [
            "https://www.kti.com.tw/",
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
            fallback_theme = theme or group or fine
            return {
                "fineIndustries": fine, "productTags": product, "themeTags": fallback_theme,
                "groupTags": group, "confidence": 65, "source": "officialIndustryFallback",
                "accuracy": "low", "sourceNote": "依官方產業與現有公開欄位保守分類，後續更新仍會持續精修。",
                "sourceUrls": [],
            }, "official_industry_fallback"
    fallback_label = industry or clean_company_name(row.get("issuerName")) or "待細分"
    return {
        "fineIndustries": ["待細分"], "productTags": [fallback_label], "themeTags": [fallback_label],
        "groupTags": ["待細分"], "confidence": 60, "source": "autoFallback",
        "accuracy": "needs_review",
        "sourceNote": "現有公開欄位不足，先保留可辨識分類並於後續更新繼續精修。", "sourceUrls": [],
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
        force_company_rule = bool(requested and code in COMPANY_RULES)
        if has_refined_tags(existing) and not force_company_rule:
            logs.append(log_row(code, row, "skipped_existing", existing, "existing_verified_tags"))
            continue
        if (
            not requested
            and code not in COMPANY_RULES
            and str(existing.get("updatedAt") or "") == today_text()
            and str(existing.get("source") or "").strip() not in INCOMPLETE_SOURCES
        ):
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
