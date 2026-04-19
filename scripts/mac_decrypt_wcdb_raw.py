#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path

from Cryptodome.Cipher import AES

SQLITE_HEADER = b"SQLite format 3\x00"
PAGE_SIZE = 4096
RESERVE_SIZE = 80


def decrypt_page(raw_key: bytes, page: bytes, page_no: int) -> bytes:
    if page_no == 1:
        encrypted = page[16:PAGE_SIZE - RESERVE_SIZE]
        reserve = page[PAGE_SIZE - RESERVE_SIZE:PAGE_SIZE]
        iv = reserve[:16]
        decrypted = AES.new(raw_key, AES.MODE_CBC, iv).decrypt(encrypted)
        return SQLITE_HEADER + decrypted + reserve

    encrypted = page[:PAGE_SIZE - RESERVE_SIZE]
    reserve = page[PAGE_SIZE - RESERVE_SIZE:PAGE_SIZE]
    iv = reserve[:16]
    decrypted = AES.new(raw_key, AES.MODE_CBC, iv).decrypt(encrypted)
    return decrypted + reserve


def decrypt_db(key_hex: str, input_path: Path, output_path: Path) -> None:
    raw_key = bytes.fromhex(key_hex)
    if len(raw_key) != 32:
        raise ValueError("key must be 64 hex chars / 32 bytes")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with input_path.open("rb") as src, output_path.open("wb") as dst:
        page_no = 1
        while True:
            page = src.read(PAGE_SIZE)
            if not page:
                break
            if len(page) != PAGE_SIZE:
                dst.write(page)
                break
            dst.write(decrypt_page(raw_key, page, page_no))
            page_no += 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Decrypt WeChat macOS 4.x WCDB raw-key database.")
    parser.add_argument("--key", required=True, help="64 hex raw database key.")
    parser.add_argument("--input", required=True, help="Encrypted database path.")
    parser.add_argument("--output", required=True, help="Decrypted SQLite output path.")
    args = parser.parse_args()

    decrypt_db(args.key, Path(args.input), Path(args.output))
    print(Path(args.output).resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
