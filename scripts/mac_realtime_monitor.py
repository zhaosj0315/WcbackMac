#!/usr/bin/env python3
"""Mac 4.x 实时消息监听 - 轮询 message_*.db 变化（替代 Windows realTime.exe）"""
import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

try:
    import zstd
except ImportError:
    zstd = None


def _decode(value) -> str:
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


class MacRealTimeMonitor:
    """轮询 Mac 微信 message_*.db 分片，检测新消息"""

    def __init__(self, db_dir: str, poll_interval: float = 2.0):
        self.db_dir = Path(db_dir)
        self.poll_interval = poll_interval
        # {db_file: {table: max_local_id}}
        self._watermarks: dict[str, dict[str, int]] = {}
        self._running = False

    def _get_dbs(self) -> list[Path]:
        return sorted(p for p in self.db_dir.glob("message_*.db") if "fts" not in p.name)

    def _init_watermarks(self) -> None:
        """记录当前各表最大 local_id，作为起始水位"""
        for db_file in self._get_dbs():
            key = db_file.name
            self._watermarks.setdefault(key, {})
            try:
                conn = sqlite3.connect(db_file)
                cur = conn.cursor()
                cur.execute("select name from sqlite_master where type='table' and name like 'Msg_%'")
                for (table,) in cur.fetchall():
                    cur.execute(f'select max(local_id) from "{table}"')
                    row = cur.fetchone()
                    self._watermarks[key][table] = row[0] or 0
                conn.close()
            except sqlite3.Error:
                continue

    def _poll_once(self) -> list[dict]:
        """扫描一次，返回新消息列表"""
        new_msgs = []
        for db_file in self._get_dbs():
            key = db_file.name
            self._watermarks.setdefault(key, {})
            try:
                conn = sqlite3.connect(db_file)
                cur = conn.cursor()
                cur.execute("select name from sqlite_master where type='table' and name like 'Msg_%'")
                tables = [r[0] for r in cur.fetchall()]
                for table in tables:
                    watermark = self._watermarks[key].get(table, 0)
                    cur.execute(
                        f'select local_id, local_type, create_time, real_sender_id, message_content '
                        f'from "{table}" where local_id > ? order by local_id asc',
                        (watermark,),
                    )
                    rows = cur.fetchall()
                    for local_id, msg_type, create_time, sender_id, content in rows:
                        text = _decode(content)
                        dt = datetime.fromtimestamp(create_time).strftime("%Y-%m-%d %H:%M:%S") if create_time else ""
                        new_msgs.append({
                            "db": key,
                            "table": table,
                            "local_id": local_id,
                            "type": msg_type & 0xFFFFFFFF if msg_type and msg_type > 0xFFFFFFFF else msg_type,
                            "create_time": create_time,
                            "datetime": dt,
                            "is_sender": sender_id == 0,
                            "content": text[:500],
                        })
                        self._watermarks[key][table] = max(self._watermarks[key].get(table, 0), local_id)
                conn.close()
            except sqlite3.Error:
                continue
        return new_msgs

    def start(self, callback: Callable[[list[dict]], None] | None = None, max_rounds: int = 0) -> None:
        """开始监听。callback 接收新消息列表；max_rounds=0 表示无限循环。"""
        self._init_watermarks()
        self._running = True
        rounds = 0
        print(f"[{datetime.now():%H:%M:%S}] 开始监听 {self.db_dir}，轮询间隔 {self.poll_interval}s")
        try:
            while self._running:
                msgs = self._poll_once()
                if msgs:
                    if callback:
                        callback(msgs)
                    else:
                        for m in msgs:
                            direction = "→" if m["is_sender"] else "←"
                            print(f"[{m['datetime']}] {direction} [{m['table'][-8:]}] {m['content'][:80]}")
                rounds += 1
                if max_rounds and rounds >= max_rounds:
                    break
                time.sleep(self.poll_interval)
        except KeyboardInterrupt:
            pass
        finally:
            self._running = False
            print("监听已停止")

    def stop(self) -> None:
        self._running = False


def main():
    parser = argparse.ArgumentParser(description="Mac 4.x 实时消息监听（轮询）")
    parser.add_argument("--db-dir", default="app/DataBase/MacMsg/message")
    parser.add_argument("--interval", type=float, default=2.0, help="轮询间隔秒数")
    parser.add_argument("--output", help="将新消息追加写入 JSONL 文件（可选）")
    args = parser.parse_args()

    output_file = open(args.output, "a", encoding="utf-8") if args.output else None

    def on_new(msgs: list[dict]):
        for m in msgs:
            direction = "→" if m["is_sender"] else "←"
            print(f"[{m['datetime']}] {direction} [{m['table'][-8:]}] {m['content'][:80]}")
        if output_file:
            for m in msgs:
                output_file.write(json.dumps(m, ensure_ascii=False) + "\n")
            output_file.flush()

    monitor = MacRealTimeMonitor(args.db_dir, args.interval)
    monitor.start(callback=on_new)

    if output_file:
        output_file.close()


if __name__ == "__main__":
    main()
