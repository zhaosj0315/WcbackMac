#!/usr/bin/env python3
"""
批量导出 Mac 微信媒体文件：
- attach/*.dat  → 图片（jpg/png，Mac 版本未加密，直接复制）
- video/*.mp4   → 视频
- file/*        → 文件附件（pdf/docx/xlsx 等）
"""
import argparse
import shutil
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]

MAGIC = {
    b'\xff\xd8\xff': '.jpg',
    b'\x89PNG':      '.png',
    b'GIF8':         '.gif',
    b'RIFF':         '.webp',
    b'\x00\x00\x00\x18': '.mp4',
    b'\x00\x00\x00\x20': '.mp4',
}

def detect_ext(data: bytes) -> str:
    for magic, ext in MAGIC.items():
        if data[:len(magic)] == magic:
            return ext
    return ''


def export_attach(src_dir: Path, out_dir: Path) -> tuple[int, int]:
    """导出 attach/ 下的图片 dat 文件"""
    out_dir.mkdir(parents=True, exist_ok=True)
    ok = skip = 0
    for dat in src_dir.rglob("*.dat"):
        data = dat.read_bytes()
        ext = detect_ext(data)
        if not ext:
            skip += 1
            continue
        # 保留原文件名（去掉 .dat），加上正确扩展名
        stem = dat.stem  # e.g. 0a09b3c1..._M
        dest = out_dir / (stem + ext)
        if not dest.exists():
            dest.write_bytes(data)
        ok += 1
    return ok, skip


def export_video(src_dir: Path, out_dir: Path) -> int:
    """导出 video/ 下的 mp4 文件"""
    out_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for f in src_dir.rglob("*.mp4"):
        dest = out_dir / f.name
        if not dest.exists():
            shutil.copy2(f, dest)
        count += 1
    return count


def export_files(src_dir: Path, out_dir: Path) -> int:
    """导出 file/ 下的文件附件"""
    out_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for f in src_dir.rglob("*"):
        if f.is_file() and f.suffix.lower() in (
            '.pdf', '.docx', '.doc', '.xlsx', '.xls', '.pptx', '.ppt',
            '.zip', '.rar', '.7z', '.txt', '.mp3', '.wav',
        ):
            dest = out_dir / f.name
            if not dest.exists():
                shutil.copy2(f, dest)
            count += 1
    return count


def main():
    parser = argparse.ArgumentParser(description="批量导出 Mac 微信媒体文件")
    parser.add_argument("--wxid-dir", help="微信用户目录，默认自动发现")
    parser.add_argument("--output", default="data/media_export", help="输出目录")
    parser.add_argument("--images", action="store_true", help="导出图片")
    parser.add_argument("--videos", action="store_true", help="导出视频")
    parser.add_argument("--files", action="store_true", help="导出文件附件")
    parser.add_argument("--all", action="store_true", help="导出全部")
    args = parser.parse_args()

    if args.all:
        args.images = args.videos = args.files = True
    if not any([args.images, args.videos, args.files]):
        args.images = args.videos = args.files = True

    # 自动发现微信用户目录
    if args.wxid_dir:
        wxid_dir = Path(args.wxid_dir)
    else:
        xwechat = Path.home() / "Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files"
        candidates = sorted(xwechat.glob("wxid_*_*/msg"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not candidates:
            print("❌ 未找到微信用户目录")
            sys.exit(1)
        wxid_dir = candidates[0].parent
        print(f"📂 微信用户目录: {wxid_dir.name}")

    msg_dir = wxid_dir / "msg"
    out = ROOT_DIR / args.output

    if args.images:
        attach_dir = msg_dir / "attach"
        if attach_dir.exists():
            print("🖼  导出图片中（可能需要几分钟）...")
            ok, skip = export_attach(attach_dir, out / "images")
            print(f"   ✅ 图片: {ok:,} 张  跳过(非图片): {skip:,}")
        else:
            print("⚠️  attach/ 目录不存在")

    if args.videos:
        video_dir = msg_dir / "video"
        if video_dir.exists():
            print("🎬 导出视频中...")
            cnt = export_video(video_dir, out / "videos")
            print(f"   ✅ 视频: {cnt:,} 个")
        else:
            print("⚠️  video/ 目录不存在")

    if args.files:
        file_dir = msg_dir / "file"
        if file_dir.exists():
            print("📎 导出文件附件中...")
            cnt = export_files(file_dir, out / "files")
            print(f"   ✅ 文件: {cnt:,} 个")
        else:
            print("⚠️  file/ 目录不存在")

    # 统计输出大小
    total = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    gb = total / 1024**3
    print(f"\n✅ 导出完成 → {out}")
    print(f"   总大小: {gb:.2f} GB")


if __name__ == "__main__":
    main()
