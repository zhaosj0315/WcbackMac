#!/usr/bin/env python3
"""Mac 4.x 聊天统计分析 - 直接读取 message_*.db 分片，不依赖合并库"""
import argparse
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

try:
    import zstd
except ImportError:
    zstd = None


MSG_TYPE_NAMES = {
    1: "文本", 3: "图片", 34: "语音", 43: "视频", 47: "表情包",
    49: "分享/文件", 50: "语音通话", 10000: "系统消息",
}


def _decode_text(value) -> str:
    if not isinstance(value, bytes):
        return str(value or "")
    if zstd and value[:4] == b"\x28\xb5\x2f\xfd":
        try:
            text = zstd.decompress(value).decode("utf-8", errors="replace")
            if "\n" in text:
                first, rest = text.split("\n", 1)
                if ":" in first and len(first) < 80:
                    return rest
            return text
        except Exception:
            pass
    return value.decode("utf-8", errors="replace")


def _iter_messages(db_dir: Path):
    """遍历所有 message_*.db 分片中的消息"""
    for db_file in sorted(db_dir.glob("message_*.db")):
        if "fts" in db_file.name:
            continue
        try:
            conn = sqlite3.connect(db_file)
            cur = conn.cursor()
            cur.execute("select name from sqlite_master where type='table' and name like 'Msg_%'")
            tables = [r[0] for r in cur.fetchall()]
            for table in tables:
                try:
                    cur.execute(
                        f'select local_id, local_type, create_time, real_sender_id, message_content '
                        f'from "{table}" where create_time > 0'
                    )
                    for row in cur.fetchall():
                        yield table, row
                except sqlite3.Error:
                    continue
            conn.close()
        except sqlite3.Error:
            continue


def analyze(db_dir: str, contact_mapping_path: str = None) -> dict:
    db_dir = Path(db_dir)
    if not db_dir.exists():
        raise FileNotFoundError(f"数据库目录不存在: {db_dir}")

    # 加载联系人映射
    contact_map = {}
    if contact_mapping_path and Path(contact_mapping_path).exists():
        import hashlib
        raw = json.loads(Path(contact_mapping_path).read_text(encoding="utf-8"))
        # 支持 {"contacts":{...}, "chatrooms":{...}} 和扁平 {wxid: name} 两种格式
        flat: dict = {}
        if "contacts" in raw or "chatrooms" in raw:
            flat.update(raw.get("contacts", {}))
            flat.update(raw.get("chatrooms", {}))
        else:
            flat = raw
        for wxid, name in flat.items():
            h = hashlib.md5(wxid.encode()).hexdigest()
            contact_map[f"Msg_{h}"] = name

    total = 0
    type_counter: Counter = Counter()
    daily_counter: Counter = Counter()
    hourly_counter: Counter = Counter()
    table_counter: Counter = Counter()
    sent = 0

    for table, (local_id, msg_type, create_time, sender_id, content) in _iter_messages(db_dir):
        total += 1
        base_type = msg_type & 0xFFFFFFFF if msg_type > 0xFFFFFFFF else msg_type
        type_counter[MSG_TYPE_NAMES.get(base_type, f"其他({base_type})")] += 1
        if create_time:
            dt = datetime.fromtimestamp(create_time)
            daily_counter[dt.strftime("%Y-%m-%d")] += 1
            hourly_counter[dt.hour] += 1
        table_counter[table] += 1
        if sender_id == 0:
            sent += 1

    # Top 20 会话
    top_contacts = []
    for table, count in table_counter.most_common(20):
        name = contact_map.get(table, table.replace("Msg_", "")[:12])
        top_contacts.append({"name": name, "table": table, "count": count})

    # 最近 365 天
    daily_sorted = sorted(daily_counter.items(), reverse=True)[:365]

    result = {
        "total_messages": total,
        "sent_messages": sent,
        "received_messages": total - sent,
        "type_distribution": dict(type_counter.most_common()),
        "top_contacts": top_contacts,
        "daily_stats": [{"date": d, "count": c} for d, c in daily_sorted],
        "hourly_distribution": {str(h): hourly_counter.get(h, 0) for h in range(24)},
        "generated_at": datetime.now().isoformat(),
    }
    return result


def main():
    parser = argparse.ArgumentParser(description="Mac 4.x 聊天统计分析")
    parser.add_argument("--db-dir", default="app/DataBase/MacMsg/message", help="message_*.db 所在目录")
    parser.add_argument("--output", default="data/mac_analysis.json")
    parser.add_argument("--mapping", default="data/mac_contact_mapping.json", help="联系人映射 JSON")
    args = parser.parse_args()

    print("📊 开始分析...")
    result = analyze(args.db_dir, args.mapping)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"总消息数: {result['total_messages']:,}")
    print(f"发送: {result['sent_messages']:,}  接收: {result['received_messages']:,}")
    print("消息类型分布:")
    for t, c in list(result["type_distribution"].items())[:8]:
        print(f"  {t}: {c:,}")
    print(f"\nTop 5 会话:")
    for item in result["top_contacts"][:5]:
        print(f"  {item['name']}: {item['count']:,}")
    print(f"\n✅ 分析结果已写入: {args.output}")


if __name__ == "__main__":
    main()
