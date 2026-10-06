"""Small baseline for opt-in Crux Flow delivery measurements."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def load_items(path: Path) -> list[dict[str, str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
        raise ValueError("board must be a list of items")
    return data


def summarize(items: list[dict[str, str]]) -> dict[str, int]:
    return {
        "completed": sum(str(item.get("status", "")).startswith("done") for item in items),
        "active": sum(item.get("status") == "active" for item in items),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(summarize(load_items(args.input)), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
