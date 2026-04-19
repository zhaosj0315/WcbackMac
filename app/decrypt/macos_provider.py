import ctypes
import json
import os
import platform
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

import psutil

from app.util.os_support import IS_MACOS, mac_wechat_roots


WECHAT_PROCESS_KEYWORDS = (
    "wechat",
    "xinwechat",
    "com.tencent.xinwechat",
    "com.tencent.wechat",
)

DB_NAMES = (
    "MicroMsg.db",
    "MSG.db",
    "MediaMSG.db",
    "Misc.db",
    "HardLinkImage.db",
    "HardLinkVideo.db",
    "contact.db",
    "general.db",
    "hardlink.db",
    "head_image.db",
    "key_info.db",
    "media_0.db",
    "message_resource.db",
    "session.db",
)


@dataclass
class ProcessInfo:
    pid: int
    name: str
    exe: str
    cmdline: list[str]


@dataclass
class DatabaseInfo:
    path: str
    name: str
    size: int
    encrypted: bool


@dataclass
class MemoryPermission:
    checked: bool
    allowed: bool
    pid: int | None
    code: int | None
    message: str


def find_wechat_processes() -> list[ProcessInfo]:
    processes = []
    for proc in psutil.process_iter(["pid", "name", "exe", "cmdline"]):
        try:
            name = proc.info.get("name") or ""
            exe = proc.info.get("exe") or ""
            cmdline = proc.info.get("cmdline") or []
            haystack = " ".join([name, exe, *cmdline]).lower()
            if any(keyword in haystack for keyword in WECHAT_PROCESS_KEYWORDS):
                processes.append(
                    ProcessInfo(
                        pid=proc.info["pid"],
                        name=name,
                        exe=exe,
                        cmdline=cmdline,
                    )
                )
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return processes


def candidate_roots() -> list[Path]:
    roots = [Path(path) for path in mac_wechat_roots()]
    home = Path.home()
    extra = [
        home / "Library" / "Containers" / "com.tencent.xinWeChat",
        home / "Library" / "Containers" / "com.tencent.xinWeChat" / "Data" / "Documents",
        home / "Library" / "Containers" / "com.tencent.xinWeChat" / "Data" / "Documents" / "xwechat_files",
        home / "Library" / "Containers" / "com.tencent.xinWeChat" / "Data" / "Documents" / "app_data",
        home / "Library" / "Containers" / "com.tencent.WeChat",
        home / "Library" / "Containers" / "com.tencent.WeChat" / "Data" / "Documents",
        home / "Library" / "Containers" / "com.tencent.WeChat" / "Data" / "Documents" / "xwechat_files",
        home / "Library" / "Application Support" / "com.tencent.xinWeChat",
        home / "Library" / "Application Support" / "WeChat",
    ]
    for process in find_wechat_processes():
        for arg in process.cmdline:
            if arg.startswith("--wechat-files-path="):
                extra.append(Path(arg.split("=", 1)[1]))
            elif arg.startswith("--wmpf_root_dir="):
                extra.append(Path(arg.split("=", 1)[1]).parent.parent)
    for path in extra:
        if path.exists() and path not in roots:
            roots.append(path)
    return roots


def _looks_encrypted(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            head = f.read(16)
        return head != b"SQLite format 3\x00"
    except OSError:
        return False


def find_databases(roots: Iterable[Path] | None = None, limit: int = 500) -> list[DatabaseInfo]:
    roots = list(roots or candidate_roots())
    found: list[DatabaseInfo] = []
    seen = set()
    for root in roots:
        if not root.exists():
            continue
        try:
            iterator = root.rglob("*.db")
            for path in iterator:
                if len(found) >= limit:
                    return found
                try:
                    resolved = path.resolve()
                except OSError:
                    continue
                if resolved in seen:
                    continue
                seen.add(resolved)
                name = path.name
                lower_name = name.lower()
                if (
                    name in DB_NAMES
                    or name.startswith(("MSG", "MediaMSG"))
                    or lower_name.startswith(("message_", "media_", "biz_message_"))
                    or "db_storage" in str(path)
                ):
                    try:
                        size = path.stat().st_size
                    except OSError:
                        size = 0
                    found.append(
                        DatabaseInfo(
                            path=str(path),
                            name=name,
                            size=size,
                            encrypted=_looks_encrypted(path),
                        )
                    )
        except (OSError, PermissionError):
            continue
    return found


def check_task_for_pid(pid: int | None = None) -> MemoryPermission:
    if not IS_MACOS:
        return MemoryPermission(False, False, pid, None, "not macOS")
    processes = find_wechat_processes()
    if pid is None and processes:
        pid = processes[0].pid
    if pid is None:
        return MemoryPermission(True, False, None, None, "WeChat process not found")

    try:
        libsystem = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
        mach_task_self = libsystem.mach_task_self
        mach_task_self.restype = ctypes.c_uint32
        task_for_pid = libsystem.task_for_pid
        task_for_pid.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.POINTER(ctypes.c_uint32)]
        task_for_pid.restype = ctypes.c_int
        task = ctypes.c_uint32(0)
        code = task_for_pid(mach_task_self(), int(pid), ctypes.byref(task))
    except Exception as exc:
        return MemoryPermission(True, False, pid, None, f"task_for_pid call failed: {exc}")

    if code == 0 and task.value:
        return MemoryPermission(True, True, pid, code, "task_for_pid allowed")
    return MemoryPermission(
        True,
        False,
        pid,
        code,
        "task_for_pid denied; automatic memory key extraction is unlikely without debugger entitlement/root/SIP changes",
    )


def probe_keychain_metadata() -> list[str]:
    names = ["WeChat", "wechat", "xinWeChat", "com.tencent.xinWeChat", "com.tencent.WeChat"]
    hits = []
    for name in names:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", name],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=8,
        )
        if result.returncode == 0:
            hits.append(name)
    return hits


def evaluate_feasibility(databases: list[DatabaseInfo], memory: MemoryPermission) -> dict:
    names = {db.name for db in databases}
    has_core_db = bool(
        names & {"MicroMsg.db", "MSG.db", "message_0.db", "contact.db"}
        or any(name.startswith("MSG") or name.startswith("message_") for name in names)
    )
    encrypted_count = sum(1 for db in databases if db.encrypted)
    if has_core_db and memory.allowed and encrypted_count:
        level = "possible"
        reason = "found encrypted WeChat databases and current process can obtain WeChat task port"
    elif has_core_db and encrypted_count:
        level = "semi_auto"
        reason = "found encrypted WeChat databases, but memory key extraction permission is currently denied"
    elif has_core_db:
        level = "import_ready"
        reason = "found WeChat databases, but they do not look encrypted or key extraction is not required"
    else:
        level = "blocked"
        reason = "core WeChat databases were not found"
    return {
        "level": level,
        "reason": reason,
        "has_core_db": has_core_db,
        "encrypted_count": encrypted_count,
    }


def build_probe(include_keychain: bool = False, check_memory: bool = True) -> dict:
    processes = find_wechat_processes()
    roots = candidate_roots()
    databases = find_databases(roots)
    memory = check_task_for_pid(processes[0].pid if processes and check_memory else None) if check_memory else MemoryPermission(False, False, None, None, "skipped")
    keychain_hits = probe_keychain_metadata() if include_keychain else []
    feasibility = evaluate_feasibility(databases, memory)
    return {
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "processes": [asdict(process) for process in processes],
        "roots": [str(root) for root in roots],
        "databases": [asdict(db) for db in databases],
        "memory_permission": asdict(memory),
        "keychain_metadata_hits": keychain_hits,
        "feasibility": feasibility,
    }


def print_probe(probe: dict) -> None:
    print(json.dumps(probe, ensure_ascii=False, indent=2))
