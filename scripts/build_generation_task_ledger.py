#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from novel_manga.task_ledger import build_generation_task_ledger


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--novel-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    ledger = build_generation_task_ledger(args.novel_dir, args.output)
    print(json.dumps(ledger["summary"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
