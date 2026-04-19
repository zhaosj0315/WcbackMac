#!/usr/bin/env python3
"""Mac 4.x 收藏导出 - 适配 fav_db_item 表结构"""
import argparse
import json
import sqlite3
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

try:
    import zstd
except ImportError:
    zstd = None

TYPE_NAMES = {
    1: "文本", 2: "图片", 3: "语音", 4: "视频", 5: "链接",
    6: "位置", 7: "小程序", 8: "文件", 14: "聊天记录", 16: "群聊视频", 18: "笔记",
}


def _decode(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        if zstd and value[:4] == b"\x28\xb5\x2f\xfd":
            try:
                return zstd.decompress(value).decode("utf-8", errors="replace")
            except Exception:
                pass
        return value.decode("utf-8", errors="replace")
    return str(value)


def _parse_fav_xml(content: str) -> dict:
    result = {}
    try:
        root = ET.fromstring(content)
        result["desc"] = (root.findtext("desc") or "").strip()
        src = root.find(".//source")
        result["source_type"] = src.get("sourcetype", "") if src is not None else ""
        for tag in ("url", "dataurl", "cdnurl"):
            val = root.findtext(f".//{tag}")
            if val:
                result["url"] = val.strip()
                break
    except ET.ParseError:
        pass
    return result


def export_favorite(db_path: str, output_path: str, limit: int = 0) -> int:
    db = Path(db_path)
    if not db.exists():
        print(f"❌ 数据库不存在: {db}")
        return 0

    conn = sqlite3.connect(db)
    cur = conn.cursor()

    cur.execute("select name from sqlite_master where type='table'")
    tables = {r[0] for r in cur.fetchall()}

    if "fav_db_item" not in tables:
        print(f"❌ 未找到 fav_db_item 表，当前表: {tables}")
        conn.close()
        return 0

    sql = "select local_id, server_id, type, update_time, content from fav_db_item order by update_time desc"
    if limit:
        sql += f" limit {limit}"
    cur.execute(sql)
    rows = cur.fetchall()
    conn.close()

    results = []
    for local_id, server_id, fav_type, update_time, content in rows:
        text = _decode(content)
        parsed = _parse_fav_xml(text) if text.strip().startswith("<") else {}
        ts = datetime.fromtimestamp(update_time).strftime("%Y-%m-%d %H:%M:%S") if update_time else ""
        results.append({
            "local_id": local_id,
            "server_id": server_id,
            "type": fav_type,
            "type_name": TYPE_NAMES.get(fav_type, f"未知({fav_type})"),
            "update_time": ts,
            "desc": parsed.get("desc", ""),
            "url": parsed.get("url", ""),
            "xml": text[:500] if len(text) > 500 else text,
        })

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"✅ 收藏导出完成: {len(results)} 条 -> {output_path}")
    return len(results)


def main():
    parser = argparse.ArgumentParser(description="Mac 4.x 收藏导出")
    parser.add_argument("--db", default="app/DataBase/MacMsg/favorite/favorite.db")
    parser.add_argument("--output", default="data/favorites.json")
    parser.add_argument("--limit", type=int, default=0, help="最多导出条数，0=全部")
    args = parser.parse_args()
    export_favorite(args.db, args.output, args.limit)


if __name__ == "__main__":
    main()
