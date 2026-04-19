#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.decrypt.macos_provider import build_probe, print_probe


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Probe whether Mac WeChat automatic decryption is feasible on this machine."
    )
    parser.add_argument(
        "--keychain",
        action="store_true",
        help="Probe Keychain item metadata by service name. This does not print passwords.",
    )
    parser.add_argument(
        "--no-memory",
        action="store_true",
        help="Skip task_for_pid memory permission check.",
    )
    args = parser.parse_args()

    probe = build_probe(include_keychain=args.keychain, check_memory=not args.no_memory)
    print_probe(probe)

    level = probe["feasibility"]["level"]
    return 0 if level in {"possible", "semi_auto", "import_ready"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
