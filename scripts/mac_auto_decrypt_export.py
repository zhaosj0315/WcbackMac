#!/usr/bin/env python3
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import psutil


DEFAULT_APP = Path("/Applications/WeChat.app")
DEFAULT_COPY = Path("/tmp/WeChat-resign-test.app")
DEFAULT_KEYS = Path("/tmp/wechat_lldb_key_candidates.json")
DEFAULT_DECRYPT_OUTPUT = "app/DataBase/MacMsg"


def run(cmd: list[str], env: dict[str, str] | None = None) -> None:
    print("+", " ".join(cmd))
    subprocess.run(cmd, check=True, env=env)


def discover_db_root() -> Path:
    xwechat = (
        Path.home()
        / "Library"
        / "Containers"
        / "com.tencent.xinWeChat"
        / "Data"
        / "Documents"
        / "xwechat_files"
    )
    candidates = sorted(
        xwechat.glob("wxid_*/db_storage"),
        key=lambda p: p.stat().st_mtime if p.exists() else 0,
        reverse=True,
    )
    if not candidates:
        raise RuntimeError(f"WeChat db_storage not found under {xwechat}")
    return candidates[0]


def _bundle_roots(copy_app: Path) -> tuple[str, ...]:
    roots = {str(copy_app)}
    if copy_app.is_absolute():
        try:
            roots.add(str(Path("/private") / copy_app.relative_to("/")))
        except ValueError:
            pass
    return tuple(sorted(roots))


def _path_matches_bundle(path_str: str, bundle_roots: tuple[str, ...]) -> bool:
    if not path_str:
        return False
    normalized = str(Path(path_str).expanduser())
    return any(normalized == root or normalized.startswith(root + "/") for root in bundle_roots)


def _iter_wechat_main_processes():
    for proc in psutil.process_iter(["pid", "exe", "cmdline", "create_time"]):
        try:
            exe = proc.info.get("exe") or ""
            exe_name = Path(exe).name
            if exe_name != "WeChat":
                continue
            yield proc
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue


def _iter_bundle_processes(copy_app: Path):
    bundle_roots = _bundle_roots(copy_app)
    for proc in psutil.process_iter(["pid", "exe", "cmdline", "create_time"]):
        try:
            exe = proc.info.get("exe") or ""
            cmdline = " ".join(proc.info.get("cmdline") or [])
            exe_name = Path(exe).name
            haystack = " ".join([exe, cmdline])
            if not exe_name.startswith("WeChat") and "WeChat" not in cmdline:
                continue
            if _path_matches_bundle(exe, bundle_roots) or any(root in haystack for root in bundle_roots):
                yield proc
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue


def _attach_priority(exe_name: str, cmdline: str) -> int | None:
    lowered = cmdline.lower()
    if "--type=renderer" in lowered or "--type=gpu-process" in lowered:
        return None
    if exe_name == "WeChatAppEx":
        return 0
    if exe_name == "WeChat":
        return 1
    if "Helper" in exe_name and "--type=utility" in cmdline:
        return 2
    if "Helper" in exe_name:
        return 3
    return 4


def find_wechat_copy_pid(copy_app: Path, timeout: int = 120) -> int:
    bundle_roots = _bundle_roots(copy_app)
    deadline = time.time() + timeout
    while time.time() < deadline:
        preferred_pid = None
        for proc in psutil.process_iter(["pid", "exe", "cmdline"]):
            try:
                exe = proc.info.get("exe") or ""
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            if _path_matches_bundle(exe, bundle_roots):
                exe_name = Path(exe).name
                if exe_name == "WeChat":
                    return int(proc.info["pid"])
                if preferred_pid is None and exe_name == "WeChatAppEx":
                    preferred_pid = int(proc.info["pid"])
        if preferred_pid is not None:
            return preferred_pid
        time.sleep(1)
    raise RuntimeError(f"WeChat copy process not found after {timeout}s")


def list_attach_pids(copy_app: Path, fallback_pid: int | None = None, timeout: int = 15) -> list[int]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        bundle_candidates: list[tuple[int, int, str, str, int]] = []
        for proc in _iter_bundle_processes(copy_app):
            try:
                create_time = float(proc.info.get("create_time") or 0)
                pid = int(proc.info["pid"])
                exe_name = Path(proc.info.get("exe") or "").name
                cmdline = " ".join(proc.info.get("cmdline") or [])
            except (KeyError, TypeError, ValueError):
                continue
            priority = _attach_priority(exe_name, cmdline)
            if priority is None:
                continue
            bundle_candidates.append((priority, pid, exe_name, cmdline, int(create_time)))
        if bundle_candidates:
            bundle_candidates.sort(key=lambda item: (item[0], -item[4], item[1]))
            pids = [pid for _, pid, _, _, _ in bundle_candidates[:5]]
            if fallback_pid and fallback_pid not in pids and psutil.pid_exists(fallback_pid):
                pids.insert(0, fallback_pid)
            return pids
        time.sleep(1)

    if fallback_pid and psutil.pid_exists(fallback_pid):
        return [fallback_pid]
    return []


def resolve_attach_pid(copy_app: Path, preferred_pid: int | None = None, timeout: int = 15) -> int:
    if preferred_pid is not None and psutil.pid_exists(preferred_pid):
        return preferred_pid

    try:
        return find_wechat_copy_pid(copy_app, timeout=timeout)
    except RuntimeError:
        pass

    deadline = time.time() + timeout
    while time.time() < deadline:
        candidates = []
        for proc in _iter_wechat_main_processes():
            try:
                candidates.append(
                    (
                        float(proc.info.get("create_time") or 0),
                        int(proc.info["pid"]),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        if candidates:
            candidates.sort(reverse=True)
            return candidates[0][1]
        time.sleep(1)

    raise RuntimeError(f"No attachable WeChat process found after {timeout}s")


def wait_for_wechat_ready(pid: int, interactive: bool, attach_delay: int) -> None:
    if interactive:
        print()
        print("请先在新打开的微信副本里确认：")
        print("1. 已完成登录（如果需要）")
        print("2. 聊天列表已经正常显示")
        print("3. 至少点开 3-5 个聊天窗口/联系人资料，让对应数据库真正被访问")
        print("4. 再额外等待几秒，让数据库加载完成")
        print()
        input("准备好后按回车继续 LLDB 扫描 > ")
        return

    if attach_delay > 0:
        print(f"等待 {attach_delay}s，让微信副本完成启动和数据库加载...")
        time.sleep(attach_delay)


def _load_scan_payload(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.setdefault("candidates", [])
    return payload


def _merge_scan_payloads(db_root: Path, payloads: list[dict]) -> dict:
    merged: dict[tuple[str, str], dict] = {}
    salt_count = 0
    scanned_pids: list[int] = []
    scanned_processes: list[dict] = []
    total_region_count = 0
    total_bytes_scanned = 0
    for payload in payloads:
        salt_count = max(salt_count, int(payload.get("salt_count", 0) or 0))
        pid = payload.get("pid")
        if isinstance(pid, int):
            scanned_pids.append(pid)
            scanned_processes.append(
                {
                    "pid": pid,
                    "process_name": payload.get("process_name"),
                    "region_count": int(payload.get("region_count", 0) or 0),
                    "bytes_scanned": int(payload.get("bytes_scanned", 0) or 0),
                }
            )
        total_region_count += int(payload.get("region_count", 0) or 0)
        total_bytes_scanned += int(payload.get("bytes_scanned", 0) or 0)
        for candidate in payload.get("candidates", []):
            key = str(candidate.get("key", "")).lower()
            salt = str(candidate.get("salt", "")).lower()
            if len(key) != 64 or len(salt) != 32:
                continue
            item = merged.setdefault(
                (key, salt),
                {
                    "key": key,
                    "salt": salt,
                    "paths": [],
                    "sources": [],
                },
            )
            for rel in candidate.get("paths", []):
                if rel not in item["paths"]:
                    item["paths"].append(rel)
            source = {
                "pid": payload.get("pid"),
                "process_name": payload.get("process_name"),
                "addr": candidate.get("addr"),
                "length": candidate.get("length"),
            }
            if source not in item["sources"]:
                item["sources"].append(source)
            if "addr" not in item and candidate.get("addr"):
                item["addr"] = candidate.get("addr")
            if "length" not in item and candidate.get("length"):
                item["length"] = candidate.get("length")
    return {
        "db_root": str(db_root),
        "salt_count": salt_count,
        "candidate_count": len(merged),
        "scanned_pids": scanned_pids,
        "scanned_processes": scanned_processes,
        "total_region_count": total_region_count,
        "total_bytes_scanned": total_bytes_scanned,
        "candidates": list(merged.values()),
    }


def _run_lldb_import(project_root: Path, script_name: str, pid: int, env: dict[str, str], extra_commands: list[str] | None = None) -> dict | None:
    with tempfile.NamedTemporaryFile(prefix=f"wechat_keys_{pid}_", suffix=".json", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    local_env = env.copy()
    local_env["WECHAT_KEY_OUT"] = str(tmp_path)
    commands = [
        "lldb",
        "-b",
        "-p",
        str(pid),
        "-o",
        f"command script import {project_root / 'scripts' / script_name}",
    ]
    if script_name == "lldb_hook_cckdf_keys.py":
        commands.extend(["-o", "script import lldb_hook_cckdf_keys; lldb_hook_cckdf_keys.install()"])
    for cmd in extra_commands or []:
        commands.extend(["-o", cmd])
    commands.extend(["-o", "detach", "-o", "quit"])
    try:
        run(commands, env=local_env)
        if tmp_path.exists():
            return _load_scan_payload(tmp_path)
        return None
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass


def run_lldb_scan(project_root: Path, pids: list[int], db_root: Path, keys: Path) -> None:
    env = os.environ.copy()
    env["WECHAT_DB_ROOT"] = str(db_root)
    errors: list[str] = []
    process_names: dict[int, str] = {}

    for pid in pids:
        try:
            proc = psutil.Process(pid)
            process_names[pid] = Path(proc.exe()).name
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            process_names[pid] = "unknown"

    hook_payloads: list[dict] = []
    for pid in pids:
        try:
            payload = _run_lldb_import(
                project_root,
                "lldb_hook_cckdf_keys.py",
                pid,
                env,
                extra_commands=[
                    "script import time; time.sleep(30)",
                    "script import lldb_hook_cckdf_keys; lldb_hook_cckdf_keys.flush_results()",
                ],
            )
            if payload:
                payload["pid"] = pid
                payload["process_name"] = process_names.get(pid, "unknown")
                hook_payloads.append(payload)
        except subprocess.CalledProcessError as exc:
            errors.append(f"hook pid={pid} ({process_names.get(pid, 'unknown')}): {exc}")

    merged = _merge_scan_payloads(db_root, hook_payloads)
    merged["fallback"] = "CCKeyDerivationPBKDF"
    if merged["candidate_count"] > 0:
        keys.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
        return

    payloads: list[dict] = []
    for pid in pids:
        try:
            payload = _run_lldb_import(project_root, "lldb_scan_wechat_keys.py", pid, env)
            if payload:
                payload["pid"] = pid
                payload["process_name"] = process_names.get(pid, "unknown")
                payloads.append(payload)
        except subprocess.CalledProcessError as exc:
            errors.append(f"pid={pid} ({process_names.get(pid, 'unknown')}): {exc}")

    merged = _merge_scan_payloads(db_root, payloads)
    merged["fallback"] = "literal-scan-after-hook"
    keys.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")

    if merged["candidate_count"] > 0:
        return

    if errors:
        raise RuntimeError(
            "LLDB 已尝试扫描多个微信进程，但没有拿到可用 key。\n"
            + "\n".join(errors)
        )

    raise RuntimeError(
        "LLDB 扫描完成，但没有发现可用 key。\n"
        f"已扫描进程数: {len(merged.get('scanned_processes', []))}, "
        f"内存区域数: {merged.get('total_region_count', 0)}, "
        f"字节数: {merged.get('total_bytes_scanned', 0)}。\n"
        "如果这些统计明显偏小，说明扫到的进程还不对；如果统计已经很大但仍为 0，下一步需要把扫描方式升级到 hook PBKDF / 原始 key 提取。"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Experimental macOS WeChat decrypt + export pipeline.")
    parser.add_argument("--wechat-app", default=str(DEFAULT_APP), help="Original /Applications/WeChat.app path.")
    parser.add_argument("--copy-app", default=str(DEFAULT_COPY), help="Temporary app copy to re-sign and run.")
    parser.add_argument("--db-root", help="Encrypted db_storage root. Auto-discovered by default.")
    parser.add_argument("--keys", default=str(DEFAULT_KEYS), help="LLDB candidate key JSON path.")
    parser.add_argument("--decrypt-output", default=DEFAULT_DECRYPT_OUTPUT, help="Decrypted database output directory.")
    parser.add_argument("--export-output", default="data/mac_messages.csv", help="Exported messages output path.")
    parser.add_argument("--latest", type=int, default=0, help="Only export latest N rows. 0 exports all rows.")
    parser.add_argument("--skip-copy", action="store_true", help="Use existing copy-app without copying original app again.")
    parser.add_argument("--skip-open", action="store_true", help="Do not open copy-app; attach to an already running copy.")
    parser.add_argument("--quit-original", action="store_true", help="Quit running WeChat before launching the re-signed copy.")
    parser.add_argument("--attach-delay", type=int, default=15, help="Seconds to wait before LLDB attach in non-interactive mode.")
    parser.add_argument("--non-interactive", action="store_true", help="Do not wait for Enter before LLDB attach; rely on --attach-delay instead.")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[1]
    wechat_app = Path(args.wechat_app).expanduser()
    copy_app = Path(args.copy_app).expanduser()
    db_root = Path(args.db_root).expanduser() if args.db_root else discover_db_root()
    keys = Path(args.keys).expanduser()

    if args.quit_original:
        subprocess.run(["osascript", "-e", 'quit app "WeChat"'], check=False)
        time.sleep(3)

    if not args.skip_copy:
        if copy_app.exists():
            shutil.rmtree(copy_app)
        run(["ditto", str(wechat_app), str(copy_app)])
        run(["/usr/bin/codesign", "--force", "--deep", "--sign", "-", str(copy_app)])

    if not args.skip_open:
        run(["open", "-na", str(copy_app)])

    pid = find_wechat_copy_pid(copy_app)
    print(f"WeChat copy pid={pid}")
    wait_for_wechat_ready(pid, interactive=not args.non_interactive, attach_delay=max(0, args.attach_delay))
    pid = resolve_attach_pid(copy_app, preferred_pid=pid, timeout=15)
    attach_pids = list_attach_pids(copy_app, fallback_pid=pid, timeout=15)
    if not attach_pids:
        attach_pids = [pid]
    print(f"WeChat attach pids={attach_pids}")

    run_lldb_scan(project_root, attach_pids, db_root, keys)

    run(
        [
            sys.executable,
            str(project_root / "scripts" / "mac_decrypt_from_keys.py"),
            "--keys",
            str(keys),
            "--db-root",
            str(db_root),
            "--output",
            args.decrypt_output,
            "--verify",
        ]
    )

    export_cmd = [
        sys.executable,
        str(project_root / "scripts" / "mac_export_messages.py"),
        "--db-dir",
        args.decrypt_output,
        "--output",
        args.export_output,
    ]
    if args.latest:
        export_cmd.extend(["--latest", str(args.latest)])
    run(export_cmd)

    print(f"done db_root={db_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
