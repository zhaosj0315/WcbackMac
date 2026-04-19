import json
import os
import re
from pathlib import Path

import lldb


def _discover_db_root() -> Path:
    configured = os.environ.get("WECHAT_DB_ROOT")
    if configured:
        return Path(configured).expanduser()

    xwechat = (
        Path.home()
        / "Library"
        / "Containers"
        / "com.tencent.xinWeChat"
        / "Data"
        / "Documents"
        / "xwechat_files"
    )
    candidates = sorted(xwechat.glob("wxid_*/db_storage"), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    if candidates:
        return candidates[0]
    return xwechat


DB_ROOT = _discover_db_root()
OUT_PATH = Path(os.environ.get("WECHAT_KEY_OUT", "/tmp/wechat_lldb_key_candidates.json")).expanduser()
MAX_REGION = 256 * 1024 * 1024
CHUNK = 4 * 1024 * 1024
OVERLAP = 256


def db_salts():
    salts = {}
    for path in DB_ROOT.rglob("*.db"):
        try:
            head = path.read_bytes()[:16]
        except OSError:
            continue
        if head != b"SQLite format 3\x00":
            rel = str(path.relative_to(DB_ROOT))
            salts[rel] = head.hex()
    return salts


def scan_process(process, salts):
    salt_values = set(salts.values())
    salt_to_paths = {}
    for rel, salt in salts.items():
        salt_to_paths.setdefault(salt, []).append(rel)

    raw_key_re = re.compile(rb"x'([0-9a-fA-F]{64,128})'")
    hex_re = re.compile(rb"([0-9a-fA-F]{64,128})")
    results = []
    seen = set()

    regions = process.GetMemoryRegions()
    for idx in range(regions.GetSize()):
        region = lldb.SBMemoryRegionInfo()
        regions.GetMemoryRegionAtIndex(idx, region)
        if not region.IsReadable():
            continue
        start = region.GetRegionBase()
        end = region.GetRegionEnd()
        size = end - start
        if size <= 0 or size > MAX_REGION:
            continue

        offset = 0
        carry = b""
        while offset < size:
            read_size = min(CHUNK, size - offset)
            err = lldb.SBError()
            data = process.ReadMemory(start + offset, read_size, err)
            if not err.Success() or not data:
                offset += read_size
                carry = b""
                continue
            blob = carry + data

            for regex in (raw_key_re, hex_re):
                for match in regex.finditer(blob):
                    text = match.group(1).decode("ascii", errors="ignore").lower()
                    for salt in salt_values:
                        if salt in text:
                            key = text[:64]
                            item = (key, salt)
                            if item in seen:
                                continue
                            seen.add(item)
                            results.append(
                                {
                                    "key": key,
                                    "salt": salt,
                                    "paths": salt_to_paths.get(salt, []),
                                    "addr": hex(start + offset + max(0, match.start(1) - len(carry))),
                                    "length": len(text),
                                }
                            )

            carry = blob[-OVERLAP:]
            offset += read_size
    return results


target = lldb.debugger.GetSelectedTarget()
process = target.GetProcess()
salts = db_salts()
results = scan_process(process, salts)
payload = {"db_root": str(DB_ROOT), "salt_count": len(salts), "candidate_count": len(results), "candidates": results}
OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"wrote {OUT_PATH} candidates={len(results)} salts={len(salts)}")
