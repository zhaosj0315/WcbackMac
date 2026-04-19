#!/usr/bin/env python3
"""Mac 4.x 朋友圈导出 - 适配 SnsTimeLine 表结构"""
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


def _parse_sns_xml(content: str) -> dict:
    """解析朋友圈 XML，提取关键字段"""
    result = {"raw": content[:500] if len(content) > 500 else content}
    try:
        root = ET.fromstring(content)
        obj = root.find("TimelineObject") or root
        result["id"] = (obj.findtext("id") or "").strip()
        result["username"] = (obj.findtext("username") or "").strip()
        result["createTime"] = (obj.findtext("createTime") or "").strip()
        result["contentDesc"] = (obj.findtext("contentDesc") or "").strip()
        # 媒体列表
        media_list = []
        for media in obj.findall(".//media"):
            url = media.findtext("url") or media.findtext("thumbUrl") or ""
            if url:
                media_list.append(url.strip())
        result["media"] = media_list
    except ET.ParseError:
        pass
    return result


def export_sns(db_path: str, output_path: str, limit: int = 0) -> int:
    db = Path(db_path)
    if not db.exists():
        print(f"❌ 数据库不存在: {db}")
        return 0

    conn = sqlite3.connect(db)
    cur = conn.cursor()

    # Mac 4.x 表名
    cur.execute("select name from sqlite_master where type='table'")
    tables = {r[0] for r in cur.fetchall()}

    if "SnsTimeLine" not in tables:
        print(f"❌ 未找到 SnsTimeLine 表，当前表: {tables}")
        conn.close()
        return 0

    sql = "select tid, user_name, content from SnsTimeLine order by tid desc"
    if limit:
        sql += f" limit {limit}"
    cur.execute(sql)
    rows = cur.fetchall()
    conn.close()

    results = []
    for tid, user_name, content in rows:
        text = _decode(content)
        parsed = _parse_sns_xml(text) if text.strip().startswith("<") else {"raw": text}
        results.append({
            "tid": tid,
            "user_name": user_name,
            "create_time": parsed.get("createTime", ""),
            "content_desc": parsed.get("contentDesc", ""),
            "media": parsed.get("media", []),
            "xml": parsed.get("raw", ""),
        })

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"✅ 朋友圈导出完成: {len(results)} 条 -> {output_path}")
    return len(results)


def main():
    parser = argparse.ArgumentParser(description="Mac 4.x 朋友圈导出")
    parser.add_argument("--db", default="app/DataBase/MacMsg/sns/sns.db")
    parser.add_argument("--output", default="data/sns.json")
    parser.add_argument("--limit", type=int, default=0, help="最多导出条数，0=全部")
    args = parser.parse_args()
    export_sns(args.db, args.output, args.limit)


if __name__ == "__main__":
    main()
