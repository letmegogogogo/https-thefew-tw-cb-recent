from __future__ import annotations

import hashlib
import shutil
import json
import re
from datetime import date, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUTS = ROOT / "outputs"
PUBLIC = ROOT / "public"

FILES = {
    "recent-cb.html": "index.html",
    "recent-cb-data.js": "recent-cb-data.js",
    "cb-primary-market-data.js": "cb-primary-market-data.js",
    "eps-forecast-data.js": "eps-forecast-data.js",
    "company-insights-data.js": "company-insights-data.js",
    "exchange-rate-data.js": "exchange-rate-data.js",
}

DATA_VERSION_FILES = tuple(name for name in FILES if name != "recent-cb.html")


def copy_file(source_name: str, target_name: str) -> None:
    source = OUTPUTS / source_name
    if not source.exists():
        raise FileNotFoundError(f"Missing required deployment file: {source}")
    if source_name == "recent-cb-data.js":
        validate_recent_cb_data(source)
    shutil.copy2(source, PUBLIC / target_name)


def build_data_version() -> str:
    digest = hashlib.sha256()
    for source_name in DATA_VERSION_FILES:
        source = OUTPUTS / source_name
        if not source.exists():
            raise FileNotFoundError(f"Missing required deployment file: {source}")
        digest.update(source_name.encode("utf-8"))
        digest.update(source.read_bytes())
    return digest.hexdigest()[:12]


def build_index(data_version: str) -> None:
    source = OUTPUTS / "recent-cb.html"
    if not source.exists():
        raise FileNotFoundError(f"Missing required deployment file: {source}")
    text = source.read_text(encoding="utf-8")
    marker = "const dataVersion = Date.now();"
    if marker not in text:
        raise ValueError(f"{source} does not contain the data version marker")
    text = text.replace(marker, f'const dataVersion = "{data_version}";', 1)
    (PUBLIC / "index.html").write_text(text, encoding="utf-8")


def copy_history_json_files() -> int:
    history_source = OUTPUTS / "cb-history"
    history_target = PUBLIC / "cb-history"
    if not history_source.exists():
        raise FileNotFoundError(f"Missing required deployment directory: {history_source}")
    json_files = sorted(history_source.rglob("*.json"))
    if not json_files:
        raise ValueError(f"{history_source} does not contain history JSON files")
    for source in json_files:
        target = history_target / source.relative_to(history_source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    return len(json_files)


def validate_recent_cb_data(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if "window.RECENT_CB_DATA" not in text:
        raise ValueError(f"{path} does not contain window.RECENT_CB_DATA")
    match = re.search(r"window\.RECENT_CB_DATA\s*=\s*(\{.*?\});\s*$", text, re.S)
    if not match:
        raise ValueError(f"{path} has invalid RECENT_CB_DATA format")
    payload = json.loads(match.group(1))
    rows = payload.get("rows")
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"{path} has empty RECENT_CB_DATA rows")
    validate_new_cb_company_details(path, rows)


def validate_new_cb_company_details(path: Path, rows: list[dict]) -> None:
    """Do not deploy newly issued CB rows with generic placeholder company tags."""
    cutoff = date.today() - timedelta(days=45)
    generic_tags = {"", "-", "其他", "半導體", "其他電子", "電子設備", "電子零組件"}
    incomplete_sources = {"", "fallback", "unknown", "officialIndustryOnly"}
    incomplete: list[str] = []
    for row in rows:
        raw_date = str(row.get("issueDate") or row.get("listingDate") or "")[:10]
        try:
            issue_date = date.fromisoformat(raw_date)
        except ValueError:
            continue
        if issue_date < cutoff:
            continue
        products = [str(value).strip() for value in (row.get("productTags") or []) if str(value).strip()]
        fine = [str(value).strip() for value in (row.get("fineIndustryTags") or []) if str(value).strip()]
        themes = [str(value).strip() for value in (row.get("themeTags") or []) if str(value).strip()]
        source = str(row.get("tagSource") or "").strip()
        confidence = int(row.get("tagConfidence") or 0)
        useful = any(value not in generic_tags for value in products + fine)
        if source in incomplete_sources or confidence < 60 or not useful or not products or not themes:
            incomplete.append(
                f"{row.get('bondCode') or '?'} {row.get('bondName') or row.get('bondShortName') or '?'}"
            )
    if incomplete:
        sample = ", ".join(incomplete[:10])
        raise ValueError(
            f"{path} contains {len(incomplete)} newly issued CB rows with incomplete company details: {sample}. "
            "Run enrich_new_cb_company_tags.py and refresh recent CB data before deployment."
        )


def main() -> int:
    # Validate the source before removing the last usable local public build.
    validate_recent_cb_data(OUTPUTS / "recent-cb-data.js")
    if PUBLIC.exists():
        shutil.rmtree(PUBLIC)
    PUBLIC.mkdir(parents=True, exist_ok=True)

    data_version = build_data_version()
    build_index(data_version)
    for source_name, target_name in FILES.items():
        if source_name != "recent-cb.html":
            copy_file(source_name, target_name)

    history_count = copy_history_json_files()

    print(f"Built static site at {PUBLIC} (data version {data_version}, {history_count} history files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
