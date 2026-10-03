"""Build the checked-in pet catalog from the public reference application."""

from __future__ import annotations

import argparse
import json
import re
import urllib.request
from pathlib import Path
from typing import Final, cast

SOURCE_URL: Final = "https://petbodyw101.vercel.app/"
BUNDLE_PATTERN: Final = re.compile(
    r'<script[^>]+src="(?P<src>/assets/index-[^"]+\.js)"'
)
CATALOG_PATTERN: Final = re.compile(
    r"[A-Za-z_$][\w$]*=JSON\.parse\(`(?P<catalog>\[\{.*?\}\])`\)",
    re.DOTALL,
)
REQUIRED_KEYS: Final = frozenset(
    {
        "name",
        "school",
        "wowFactor",
        "eggName",
        "exclusive",
        "unhatchable",
        "retired",
        "specialBody",
    }
)


def fetch_text(url: str) -> str:
    """Fetch UTF-8 text from the fixed HTTPS reference origin."""
    if not url.startswith("https://petbodyw101.vercel.app/"):
        raise ValueError("Refusing to fetch from an untrusted origin")
    request = urllib.request.Request(  # noqa: S310 - origin is allow-listed above
        url,
        headers={"User-Agent": "wiz-hatch-data-builder/0.2.0"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310
        return response.read().decode("utf-8")


def extract_catalog(page: str, bundle: str) -> list[dict[str, object]]:
    """Extract and validate the JSON catalog embedded in the JS bundle."""
    bundle_match = BUNDLE_PATTERN.search(page)
    if bundle_match is None:
        raise RuntimeError("Unable to locate the application bundle")
    catalog_match = CATALOG_PATTERN.search(bundle)
    if catalog_match is None:
        raise RuntimeError("Unable to locate the embedded pet catalog")

    raw: object = json.loads(catalog_match.group("catalog"))
    if not isinstance(raw, list) or not raw:
        raise RuntimeError("The extracted catalog is not a non-empty array")

    validated: list[dict[str, object]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise RuntimeError(f"Catalog entry {index} is not an object")
        record = cast("dict[str, object]", item)
        if set(record) != REQUIRED_KEYS:
            raise RuntimeError(f"Catalog entry {index} has an unexpected schema")
        validated.append(record)
    return sorted(validated, key=lambda item: str(item["name"]).casefold())


def build_catalog(output: Path) -> int:
    """Fetch the reference bundle and atomically write normalized JSON."""
    page = fetch_text(SOURCE_URL)
    bundle_match = BUNDLE_PATTERN.search(page)
    if bundle_match is None:
        raise RuntimeError("Unable to locate the application bundle")
    bundle_url = f"https://petbodyw101.vercel.app{bundle_match.group('src')}"
    catalog = extract_catalog(page, fetch_text(bundle_url))

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(f"{output.suffix}.tmp")
    temporary.write_text(
        f"{json.dumps(catalog, indent=2, ensure_ascii=False)}\n",
        encoding="utf-8",
        newline="\n",
    )
    temporary.replace(output)
    return len(catalog)


def parse_args() -> argparse.Namespace:
    """Parse command-line options."""
    default_output = Path(__file__).resolve().parents[1] / "data" / "pets.json"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=default_output,
        help="Path to the generated JSON catalog",
    )
    return parser.parse_args()


def main() -> None:
    """Generate the local pet catalog."""
    args = parse_args()
    output = cast("Path", args.output).expanduser().resolve()
    count = build_catalog(output)
    print(f"Wrote {count} pets to {output}")


if __name__ == "__main__":
    main()
