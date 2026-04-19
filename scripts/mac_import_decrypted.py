#!/usr/bin/env python3
import argparse
import os
import shutil
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.config import DB_DIR, INFO_FILE_PATH
from app.DataBase.merge import merge_databases, merge_MediaMSG_databases


IMPORTANT_DBS = {
    "MicroMsg.db",
    "MSG.db",
    "MediaMSG.db",
    "Misc.db",
    "HardLinkImage.db",
    "HardLinkVideo.db",
    "OpenIMContact.db",
}


def copy_databases(source: Path, target: Path) -> list[Path]:
    copied = []
    for path in source.rglob("*.db"):
        if path.name in IMPORTANT_DBS or path.name.startswith(("MSG", "MediaMSG")):
            output = target / path.name
            shutil.copy2(path, output)
            copied.append(output)
    return copied


def merge_shards(target: Path) -> None:
    msg0 = target / "MSG0.db"
    msg = target / "MSG.db"
    if msg0.exists():
        shutil.copy2(msg0, msg)
        merge_databases([str(target / f"MSG{i}.db") for i in range(1, 100)], str(msg))

    media0 = target / "MediaMSG0.db"
    media = target / "MediaMSG.db"
    if media0.exists():
        shutil.copy2(media0, media)
        merge_MediaMSG_databases([str(target / f"MediaMSG{i}.db") for i in range(1, 100)], str(media))


def write_info(wxid: str, wx_dir: str, name: str, mobile: str) -> None:
    os.makedirs(os.path.dirname(INFO_FILE_PATH), exist_ok=True)
    payload = {
        "wxid": wxid,
        "wx_dir": wx_dir,
        "name": name,
        "mobile": mobile,
        "token": "",
    }
    import json

    with open(INFO_FILE_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=4)


def main() -> int:
    parser = argparse.ArgumentParser(description="Import decrypted WeChat databases for the Mac desktop workflow.")
    parser.add_argument("source", help="Directory containing decrypted WeChat .db files.")
    parser.add_argument("--wxid", default="wxid_mac_import", help="Your wxid. Used by the UI profile file.")
    parser.add_argument("--name", default="Mac User", help="Display name used by the UI profile file.")
    parser.add_argument("--mobile", default="", help="Mobile number used by the UI profile file.")
    parser.add_argument("--media-root", default="", help="Original WeChat file root for images/video/audio.")
    parser.add_argument("--no-merge", action="store_true", help="Copy shard databases without merging MSG/MediaMSG.")
    args = parser.parse_args()

    source = Path(args.source).expanduser().resolve()
    if not source.exists() or not source.is_dir():
        raise SystemExit(f"source directory not found: {source}")

    target = Path(DB_DIR).resolve()
    target.mkdir(parents=True, exist_ok=True)

    copied = copy_databases(source, target)
    if not args.no_merge:
        merge_shards(target)

    write_info(args.wxid, args.media_root or str(source), args.name, args.mobile)

    print(f"Imported {len(copied)} database files into {target}")
    print(f"Profile written to {Path(INFO_FILE_PATH).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
