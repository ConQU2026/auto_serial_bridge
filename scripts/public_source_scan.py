#!/usr/bin/env python3

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
FORBIDDEN_HEX = (
    "576561706f6e446f636b",
    "4d65726c696e5069636b",
    "47726964506c616365",
    "4772696441747461636b",
    "47656e65726963537461747573",
    "2f7461736b2f",
    "2f6465762f73746d3332",
)
IGNORED_PARTS = {".git", ".pytest_cache", "__pycache__"}


def main() -> None:
    forbidden = tuple(bytes.fromhex(value) for value in FORBIDDEN_HEX)
    violations = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or any(part in IGNORED_PARTS for part in path.parts):
            continue
        data = path.read_bytes()
        if any(token in data for token in forbidden):
            violations.append(path.relative_to(ROOT))

    if violations:
        joined = "\n".join(f"  - {path}" for path in violations)
        raise SystemExit(f"Forbidden private terms found:\n{joined}")


if __name__ == "__main__":
    main()
