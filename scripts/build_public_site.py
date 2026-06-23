from __future__ import annotations

import shutil
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
    shutil.copy2(source, PUBLIC / target_name)


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
