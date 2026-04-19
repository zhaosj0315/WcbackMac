#!/usr/bin/env python3
import argparse
import csv
import heapq
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


TEXT_COLUMNS = (
    "message_content",
    "compress_content",
    "real_sender_id",
    "source",
)


def looks_readable(text: str) -> bool:
    if not text:
        return False
    useful = 0
    for char in text:
        if char in "\t\r\n" or (char.isprintable() and char != "\ufffd"):
            useful += 1
    if useful / max(len(text), 1) < 0.9:
        return False
    controls = sum(1 for char in text if ord(char) < 32 and char not in "\t\r\n")
    return controls == 0


def decode_cell(value: Any) -> Any:
    if value is None or isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        return value.replace("\r", "\\r").replace("\n", "\\n")
    if isinstance(value, bytes):
        # Mac 微信用 zstd 压缩（魔数 0x28B52FFD）
        if value[:4] == b'\x28\xb5\x2f\xfd':
            try:
                import zstd
                text = zstd.decompress(value).decode('utf-8', errors='replace').strip('\x00')
                if '\n' in text:
                    first, rest = text.split('\n', 1)
                    if ':' in first and len(first) < 80:
                        text = rest
                return text.replace("\r", "\\r").replace("\n", "\\n")
            except Exception:
                pass
        for encoding in ("utf-8", "gb18030", "utf-16le"):
            try:
                text = value.decode(encoding)
            except UnicodeDecodeError:
                continue
            text = text.strip("\x00")
            if looks_readable(text):
                return text.replace("\r", "\\r").replace("\n", "\\n")
        return "0x" + value[:64].hex()
    return str(value)


def iso_time(timestamp: Any) -> str:
    try:
        value = int(timestamp)
    except (TypeError, ValueError):
        return ""
    if value <= 0:
        return ""
    if value > 10_000_000_000:
        value = value // 1000
    return datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M:%S")


def find_message_dbs(root: Path) -> list[Path]:
    if root.is_file():
        return [root]
    dbs = []
    for path in root.rglob("*.db"):
        if path.name.startswith(("message_", "biz_message_")) or path.name.endswith(".dec.db"):
            dbs.append(path)
    return sorted(dbs)


def message_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "select name from sqlite_master where type='table' and name like 'Msg_%' order by name"
    ).fetchall()
    return [row[0] for row in rows]


def table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in conn.execute(f'pragma table_info("{table}")').fetchall()]


def query_messages(conn: sqlite3.Connection, table: str, latest: int = 0) -> Iterable[dict[str, Any]]:
    columns = table_columns(conn, table)
    wanted = [
        column
        for column in (
            "local_id",
            "server_id",
            "local_type",
            "sort_seq",
            "real_sender_id",
            "create_time",
            "status",
            "source",
            "message_content",
            "compress_content",
        )
        if column in columns
    ]
    if not wanted:
        return
    select_list = ", ".join(f'"{column}"' for column in wanted)
    order_clause = ""
    limit_clause = ""
    if "create_time" in wanted:
        order_clause = ' order by "create_time" desc' if latest else ' order by "create_time"'
    if latest:
        limit_clause = f" limit {int(latest)}"
    cursor = conn.execute(f'select {select_list} from "{table}"{order_clause}{limit_clause}')
    for row in cursor:
        item = dict(zip(wanted, row))
        for column in TEXT_COLUMNS:
            if column in item:
                item[column] = decode_cell(item[column])
        item["datetime"] = iso_time(item.get("create_time"))
        yield item


def write_csv(rows: Iterable[dict[str, Any]], output: Path, contact_mapper=None) -> int:
    output.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "db_file",
        "table_name",
        "conversation_with",  # 新增：会话对象（从表名推断）
        "local_id",
        "server_id",
        "local_type",
        "sort_seq",
        "real_sender_id",
        "sender_display_name",  # 显示名称
        "create_time",
        "datetime",
        "status",
        "source",
        "message_content",
        "compress_content",
    ]
    count = 0
    
    # 构建表名到 wxid 的映射（通过遍历所有联系人计算 MD5）
    table_to_wxid = {}
    if contact_mapper:
        import hashlib
        all_wxids = list(contact_mapper.wxid_to_remark.keys()) + list(contact_mapper.chatroom_to_name.keys())
        for wxid in all_wxids:
            table_hash = hashlib.md5(wxid.encode()).hexdigest()
            table_name = f"Msg_{table_hash}"
            table_to_wxid[table_name] = wxid
    
    with output.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            # 从表名推断会话对象
            table_name = row.get("table_name", "")
            if table_name in table_to_wxid:
                conversation_wxid = table_to_wxid[table_name]
                if contact_mapper:
                    if '@chatroom' in conversation_wxid:
                        row["conversation_with"] = contact_mapper.get_chatroom_name(conversation_wxid)
                    else:
                        row["conversation_with"] = contact_mapper.get_display_name(conversation_wxid)
                else:
                    row["conversation_with"] = conversation_wxid
            else:
                row["conversation_with"] = table_name
            
            # sender_display_name 暂时留空（Mac 版本的 real_sender_id 是数字 ID）
            row["sender_display_name"] = ""
            
            writer.writerow(row)
            count += 1
    return count


def write_jsonl(rows: Iterable[dict[str, Any]], output: Path) -> int:
    output.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with output.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")
            count += 1
    return count


def iter_rows(root: Path, latest: int = 0) -> Iterable[dict[str, Any]]:
    buffered: list[tuple[int, int, dict[str, Any]]] = []
    sequence = 0
    for db_path in find_message_dbs(root):
        try:
            conn = sqlite3.connect(db_path)
        except sqlite3.Error as exc:
            print(f"skip db {db_path}: {exc}")
            continue
        with conn:
            try:
                tables = message_tables(conn)
                for table in tables:
                    for row in query_messages(conn, table, latest=latest):
                        row["db_file"] = str(db_path)
                        row["table_name"] = table
                        if latest:
                            sequence += 1
                            sort_key = int(row.get("create_time") or 0)
                            entry = (sort_key, sequence, row)
                            if len(buffered) < latest:
                                heapq.heappush(buffered, entry)
                            else:
                                heapq.heappushpop(buffered, entry)
                        else:
                            yield row
            except sqlite3.Error as exc:
                print(f"skip db {db_path}: {exc}")
    if latest:
        for _, _, row in sorted(buffered, reverse=True):
            yield row


def main() -> int:
    parser = argparse.ArgumentParser(description="Export decrypted macOS WeChat 4.x message tables to CSV or JSONL.")
    parser.add_argument("--db-dir", default="app/Database/MacMsg", help="Decrypted MacMsg directory or one decrypted message db.")
    parser.add_argument("--output", default="data/mac_messages.csv", help="Output CSV/JSONL file.")
    parser.add_argument("--format", choices=("csv", "jsonl"), default="csv")
    parser.add_argument("--latest", type=int, default=0, help="Only export latest N rows, sorted by create_time desc.")
    parser.add_argument("--mapping", default="data/mac_contact_mapping.json", help="Contact mapping JSON file.")
    args = parser.parse_args()

    # 加载联系人映射
    contact_mapper = None
    mapping_path = Path(args.mapping).expanduser()
    if mapping_path.exists():
        try:
            import sys
            sys.path.insert(0, str(Path(__file__).parent))
            from mac_contact_mapper import MacContactMapper
            contact_mapper = MacContactMapper.load_mapping(str(mapping_path))
            print(f"✅ 已加载联系人映射: {len(contact_mapper.wxid_to_remark)} 个联系人")
        except Exception as e:
            print(f"⚠️  加载映射失败: {e}")

    rows = iter_rows(Path(args.db_dir).expanduser(), latest=args.latest)
    output = Path(args.output).expanduser()
    count = write_jsonl(rows, output) if args.format == "jsonl" else write_csv(rows, output, contact_mapper)
    print(f"exported={count} output={output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
