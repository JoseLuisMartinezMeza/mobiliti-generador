from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mobiliti_saas.quote_engine.sunon_image_provider import (  # noqa: E402
    _fetch_text,
    parse_sunon_product_no_catalog_entries,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build an exact-code Sunon image catalog from product pages.")
    parser.add_argument("urls", nargs="+", help="Sunon product page URLs to scan")
    parser.add_argument(
        "--output",
        default=str(ROOT / "mobiliti_saas" / "quote_engine" / "data" / "sunon_catalog.json"),
        help="Output JSON path",
    )
    parser.add_argument("--title", default="", help="Optional product title stored in entries")
    args = parser.parse_args()

    today = date.today().isoformat()
    entries = []
    seen = set()
    for url in args.urls:
        html = _fetch_text(url, timeout_seconds=30)
        for entry in parse_sunon_product_no_catalog_entries(
            html,
            product_url=url,
            product_title=args.title,
            last_seen=today,
        ):
            normalized = entry["normalized_code"]
            if normalized in seen:
                continue
            seen.add(normalized)
            entries.append(entry)

    payload = {
        "generated_at": today,
        "source": "https://www.sunonglobal.com/",
        "notes": "Generated from product pages. Only exact normalized code matches should consume these entries.",
        "entries": entries,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {len(entries)} entries to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
