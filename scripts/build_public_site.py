from __future__ import annotations

import shutil
import json
import re
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
}


def copy_file(source_name: str, target_name: str) -> None:
    source = OUTPUTS / source_name
    if not source.exists():
        raise FileNotFoundError(f"Missing required deployment file: {source}")
    if source_name == "recent-cb-data.js":
        validate_recent_cb_data(source)
    shutil.copy2(source, PUBLIC / target_name)


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


def main() -> int:
    if PUBLIC.exists():
        shutil.rmtree(PUBLIC)
    PUBLIC.mkdir(parents=True, exist_ok=True)

    for source_name, target_name in FILES.items():
        copy_file(source_name, target_name)

    history_source = OUTPUTS / "cb-history"
    history_target = PUBLIC / "cb-history"
    if not history_source.exists():
        raise FileNotFoundError(f"Missing required deployment directory: {history_source}")
    shutil.copytree(history_source, history_target)

    print(f"Built static site at {PUBLIC}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
