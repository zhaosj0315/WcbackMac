import json
import os
from pathlib import Path
from typing import Optional

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
MAX_PASSWORD_LEN = 128
MAX_SALT_LEN = 64
_RESULTS: dict[tuple[str, str], dict] = {}
_SALT_TO_PATHS: dict[str, list[str]] = {}
_BREAKPOINT = None
_INITIALIZED = False


def _db_salts() -> dict[str, str]:
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


def _read_process_bytes(process, addr: int, size: int) -> bytes:
    if addr <= 0 or size <= 0:
        return b""
    err = lldb.SBError()
    data = process.ReadMemory(addr, size, err)
    if not err.Success() or not data:
        return b""
    return bytes(data)


def _normalize_password(raw: bytes) -> Optional[str]:
    if len(raw) == 32:
        return raw.hex()
    text = raw.decode("ascii", errors="ignore").strip().lower()
    if len(text) == 64 and all(c in "0123456789abcdef" for c in text):
        return text
    return None


def _record_candidate(password_hex: str, salt_hex: str, frame) -> None:
    key = (password_hex, salt_hex)
    if key in _RESULTS:
        return
    paths = _SALT_TO_PATHS.get(salt_hex, [])
    item = {
        "key": password_hex,
        "salt": salt_hex,
        "paths": paths,
        "addr": hex(frame.GetPC()),
        "length": len(password_hex),
        "source": "CCKeyDerivationPBKDF",
    }
    _RESULTS[key] = item


def kdf_breakpoint(frame, bp_loc, internal_dict):  # noqa: ARG001
    process = frame.GetThread().GetProcess()
    password_ptr = frame.FindRegister("x1").GetValueAsUnsigned()
    password_len = frame.FindRegister("x2").GetValueAsUnsigned()
    salt_ptr = frame.FindRegister("x3").GetValueAsUnsigned()
    salt_len = frame.FindRegister("x4").GetValueAsUnsigned()

    if password_len <= 0 or password_len > MAX_PASSWORD_LEN or salt_len <= 0 or salt_len > MAX_SALT_LEN:
        return False

    password = _read_process_bytes(process, password_ptr, int(password_len))
    salt = _read_process_bytes(process, salt_ptr, int(salt_len))
    password_hex = _normalize_password(password)
    salt_hex = salt.hex().lower()
    if not password_hex or len(salt_hex) != 32:
        return False
    if salt_hex not in _SALT_TO_PATHS:
        return False
    _record_candidate(password_hex, salt_hex, frame)
    return False


def flush_results() -> None:
    payload = {
        "db_root": str(DB_ROOT),
        "salt_count": len(_SALT_TO_PATHS),
        "candidate_count": len(_RESULTS),
        "candidates": list(_RESULTS.values()),
    }
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT_PATH} candidates={len(_RESULTS)} salts={len(_SALT_TO_PATHS)} hook=CCKeyDerivationPBKDF")


def install(auto_continue: bool = True) -> None:
    global _BREAKPOINT, _INITIALIZED
    if not _INITIALIZED:
        salts = _db_salts()
        for rel, salt in salts.items():
            _SALT_TO_PATHS.setdefault(salt, []).append(rel)
        _INITIALIZED = True
    debugger = lldb.debugger
    debugger.SetAsync(True)
    target = debugger.GetSelectedTarget()
    if _BREAKPOINT is None:
        _BREAKPOINT = target.BreakpointCreateByName("CCKeyDerivationPBKDF")
        _BREAKPOINT.SetScriptCallbackFunction(__name__ + ".kdf_breakpoint")
        _BREAKPOINT.SetAutoContinue(True)
    process = target.GetProcess()
    if auto_continue and process.IsValid():
        state = process.GetState()
        if state in {lldb.eStateStopped, lldb.eStateSuspended}:
            process.Continue()
