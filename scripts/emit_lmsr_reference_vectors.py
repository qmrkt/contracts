from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.generate_lmsr_reference_vectors import generate_fixture

FIXTURE_PATH = ROOT / "tests" / "fixtures" / "lmsr_reference_vectors.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="Emit deterministic LMSR reference vectors")
    parser.add_argument("--check", action="store_true", help="verify the checked-in fixture matches freshly generated output")
    args = parser.parse_args()

    generated = json.dumps(generate_fixture(), indent=2, sort_keys=True) + "\n"

    if args.check:
        if not FIXTURE_PATH.exists():
            print(f"missing fixture: {FIXTURE_PATH}", file=sys.stderr)
            return 1
        existing = FIXTURE_PATH.read_text(encoding="utf-8")
        if existing != generated:
            print(f"fixture out of date: {FIXTURE_PATH}", file=sys.stderr)
            return 1
        print(f"fixture up to date: {FIXTURE_PATH}")
        return 0

    FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE_PATH.write_text(generated, encoding="utf-8")
    print(f"wrote {FIXTURE_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

