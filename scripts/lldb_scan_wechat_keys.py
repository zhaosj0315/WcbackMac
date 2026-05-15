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
CHUNK = 4 * 1024 * 1024
OVERLAP = 128
HEX_LITERAL_RE = re.compile(rb"x'([0-9a-fA-F]{96})'")


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

    results = []
    seen = set()
    bytes_scanned = 0
    region_count = 0

    regions = process.GetMemoryRegions()
    for idx in range(regions.GetSize()):
        region = lldb.SBMemoryRegionInfo()
        regions.GetMemoryRegionAtIndex(idx, region)
        if not region.IsReadable():
            continue
        if hasattr(region, "IsWritable") and not region.IsWritable():
            continue
        start = region.GetRegionBase()
        end = region.GetRegionEnd()
        size = end - start
        if size <= 0:
            continue
        region_count += 1

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
            bytes_scanned += len(data)

            for match in HEX_LITERAL_RE.finditer(blob):
                text = match.group(1).decode("ascii", errors="ignore").lower()
                key = text[:64]
                salt = text[64:]
                if salt not in salt_values:
                    continue
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
    return results, {"region_count": region_count, "bytes_scanned": bytes_scanned}


target = lldb.debugger.GetSelectedTarget()
process = target.GetProcess()
salts = db_salts()
results, stats = scan_process(process, salts)
payload = {
    "db_root": str(DB_ROOT),
    "salt_count": len(salts),
    "candidate_count": len(results),
    "region_count": stats["region_count"],
    "bytes_scanned": stats["bytes_scanned"],
    "candidates": results,
}
OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
print(
    f"wrote {OUT_PATH} candidates={len(results)} salts={len(salts)} "
    f"regions={stats['region_count']} bytes={stats['bytes_scanned']}"
)
