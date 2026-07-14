#!/usr/bin/env python3
"""Regenerate and seal the Qingpu frozen expected-result artifact.

This is a maintainer-only command. Review the generated JSON diff before accepting
it; normal evaluation only reads the sealed artifact and never executes gold SQL.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

try:
    from .expected_results import (
        DEFAULT_ARTIFACT,
        DEFAULT_DATASET,
        DEFAULT_LOCK,
        seal_expected_results,
    )
except ImportError:  # direct script execution
    from expected_results import (
        DEFAULT_ARTIFACT,
        DEFAULT_DATASET,
        DEFAULT_LOCK,
        seal_expected_results,
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--artifact", type=Path, default=DEFAULT_ARTIFACT)
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    artifact, lock = seal_expected_results(args.dataset, args.artifact, args.lock)
    print(f"sealed expected results: artifact={artifact}; lock={lock}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
