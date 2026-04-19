#!/usr/bin/env python3
import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import psutil


DEFAULT_APP = Path("/Applications/WeChat.app")
DEFAULT_COPY = Path("/tmp/WeChat-resign-test.app")
DEFAULT_KEYS = Path("/tmp/wechat_lldb_key_candidates.json")


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


def find_wechat_copy_pid(copy_app: Path, timeout: int = 120) -> int:
    needles = {
        str(copy_app / "Contents" / "MacOS" / "WeChat"),
        str(Path("/private") / copy_app.relative_to("/")) if copy_app.is_absolute() else str(copy_app),
    }
    deadline = time.time() + timeout
    while time.time() < deadline:
        for proc in psutil.process_iter(["pid", "exe", "cmdline"]):
            try:
                exe = proc.info.get("exe") or ""
                cmdline = " ".join(proc.info.get("cmdline") or [])
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            haystack = f"{exe} {cmdline}"
            if any(needle in haystack for needle in needles) or str(copy_app) in haystack:
                return int(proc.info["pid"])
        time.sleep(1)
    raise RuntimeError(f"WeChat copy process not found after {timeout}s")


def main() -> int:
    parser = argparse.ArgumentParser(description="Experimental macOS WeChat decrypt + export pipeline.")
    parser.add_argument("--wechat-app", default=str(DEFAULT_APP), help="Original /Applications/WeChat.app path.")
    parser.add_argument("--copy-app", default=str(DEFAULT_COPY), help="Temporary app copy to re-sign and run.")
    parser.add_argument("--db-root", help="Encrypted db_storage root. Auto-discovered by default.")
    parser.add_argument("--keys", default=str(DEFAULT_KEYS), help="LLDB candidate key JSON path.")
    parser.add_argument("--decrypt-output", default="app/Database/MacMsg", help="Decrypted database output directory.")
    parser.add_argument("--export-output", default="data/mac_messages.csv", help="Exported messages output path.")
    parser.add_argument("--latest", type=int, default=0, help="Only export latest N rows. 0 exports all rows.")
    parser.add_argument("--skip-copy", action="store_true", help="Use existing copy-app without copying original app again.")
    parser.add_argument("--skip-open", action="store_true", help="Do not open copy-app; attach to an already running copy.")
    parser.add_argument("--quit-original", action="store_true", help="Quit running WeChat before launching the re-signed copy.")
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
        run(["open", str(copy_app)])

    pid = find_wechat_copy_pid(copy_app)
    print(f"WeChat copy pid={pid}")

    env = os.environ.copy()
    env["WECHAT_DB_ROOT"] = str(db_root)
    env["WECHAT_KEY_OUT"] = str(keys)
    run(
        [
            "lldb",
            "-b",
            "-p",
            str(pid),
            "-o",
            f"command script import {project_root / 'scripts' / 'lldb_scan_wechat_keys.py'}",
            "-o",
            "detach",
            "-o",
            "quit",
        ],
        env=env,
    )

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
