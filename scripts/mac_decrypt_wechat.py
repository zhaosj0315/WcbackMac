#!/usr/bin/env python3
import argparse
import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.decrypt.decrypt import decrypt, verify_db_key
from app.decrypt.macos_provider import candidate_roots, find_databases


def pick_verify_db(databases):
    preferred = ["message_0.db", "contact.db", "message_1.db", "general.db"]
    for name in preferred:
        for db in databases:
            if db.name == name and db.encrypted:
                return db
    for db in databases:
        if db.encrypted:
            return db
    return None


def decrypt_databases(key: str, databases, out_dir: Path, source_root: Path | None = None) -> tuple[int, int]:
    ok = 0
    failed = 0
    out_dir.mkdir(parents=True, exist_ok=True)
    for db in databases:
        if not db.encrypted:
            continue
        src = Path(db.path)
        if source_root:
            try:
                rel = src.relative_to(source_root)
            except ValueError:
                rel = Path(src.name)
        else:
            rel = Path(src.name)
        dest = out_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        success, result = decrypt(key, str(src), str(dest))
        if success:
            ok += 1
            print(f"[OK] {src} -> {dest}")
        else:
            failed += 1
            print(f"[FAIL] {src}: {result}")
    return ok, failed


def main() -> int:
    parser = argparse.ArgumentParser(description="Decrypt Mac WeChat databases with a known 64-hex database key.")
    parser.add_argument("--key", required=True, help="64 hex chars WeChat database key.")
    parser.add_argument("--source", help="Mac WeChat xwechat_files/db root. Defaults to auto discovery.")
    parser.add_argument("--out", default="./app/Database/MacMsg", help="Output directory for decrypted Mac databases.")
    parser.add_argument("--limit", type=int, default=500, help="Maximum database files to scan.")
    parser.add_argument("--no-verify", action="store_true", help="Skip key verification before decrypting.")
    args = parser.parse_args()

    roots = [Path(args.source).expanduser().resolve()] if args.source else candidate_roots()
    databases = find_databases(roots, limit=args.limit)
    encrypted = [db for db in databases if db.encrypted]
    if not encrypted:
        print("No encrypted Mac WeChat databases found.")
        return 2

    verify_db = pick_verify_db(databases)
    if verify_db and not args.no_verify:
        if not verify_db_key(args.key, verify_db.path):
            print(f"Key verification failed against {verify_db.path}")
            return 3
        print(f"Key verified against {verify_db.path}")

    source_root = roots[0] if len(roots) == 1 else None
    ok, failed = decrypt_databases(args.key, encrypted, Path(args.out), source_root)
    print(f"Finished. success={ok}, failed={failed}, output={Path(args.out).resolve()}")
    return 0 if ok and not failed else 4


if __name__ == "__main__":
    raise SystemExit(main())
