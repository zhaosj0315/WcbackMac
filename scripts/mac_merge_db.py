#!/usr/bin/env python3
"""
Mac 版本数据库合并功能
将多个 message_*.db 合并成单一数据库，方便查询和导出
"""
import sqlite3
import shutil
from pathlib import Path
from datetime import datetime
from typing import List, Optional


class MacDBMerger:
    def __init__(self, db_dir: str = "app/Database/MacMsg"):
        self.db_dir = Path(db_dir)
        self.message_dir = self.db_dir / "message"
        
    def merge_message_dbs(self, output_path: str = "data/merged_messages.db",
                         include_contact: bool = True,
                         include_session: bool = True,
                         test_mode: bool = False,
                         test_limit: int = 100) -> str:
        """
        合并所有消息数据库到单一文件
        
        Args:
            output_path: 输出数据库路径
            include_contact: 是否包含联系人表
            include_session: 是否包含会话表
            test_mode: 测试模式（只合并少量数据）
            test_limit: 测试模式下每个表的最大行数
        
        Returns:
            合并后的数据库路径
        """
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        
        # 如果输出文件已存在，删除
        if output.exists():
            output.unlink()
            print(f"🗑️  删除旧文件: {output}")
        
        # 创建新数据库
        conn = sqlite3.connect(str(output))
        cursor = conn.cursor()
        
        if test_mode:
            print(f"🧪 测试模式：每个表最多 {test_limit} 条")
        
        print(f"📦 开始合并数据库...")
        print(f"  源目录: {self.db_dir}")
        print(f"  输出: {output}")
        print()
        
        # 1. 合并联系人表
        if include_contact:
            self._merge_contact_table(cursor)
        
        # 2. 合并会话表
        if include_session:
            self._merge_session_table(cursor)
        
        # 3. 合并所有消息表
        total_messages = self._merge_message_tables(cursor, test_mode, test_limit)
        
        # 4. 创建索引
        self._create_indexes(cursor)
        
        conn.commit()
        conn.close()
        
        # 输出统计
        size_mb = output.stat().st_size / 1024 / 1024
        print(f"\n✅ 合并完成!")
        print(f"  总消息数: {total_messages:,}")
        print(f"  文件大小: {size_mb:.2f} MB")
        print(f"  输出路径: {output.absolute()}")
        
        return str(output.absolute())
    
    def _merge_contact_table(self, cursor: sqlite3.Cursor):
        """合并联系人表"""
        contact_db = self.db_dir / "contact" / "contact.db"
        if not contact_db.exists():
            print("⚠️  contact.db 不存在，跳过联系人表")
            return
        
        print("📇 合并联系人表...")
        
        # 附加联系人数据库
        cursor.execute(f"ATTACH DATABASE '{contact_db}' AS contact_db")
        
        # 复制表结构和数据
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS contact AS 
            SELECT * FROM contact_db.contact
        """)
        
        count = cursor.execute("SELECT COUNT(*) FROM contact").fetchone()[0]
        print(f"  ✅ 联系人: {count:,} 条")
        
        cursor.execute("DETACH DATABASE contact_db")
    
    def _merge_session_table(self, cursor: sqlite3.Cursor):
        """合并会话表"""
        session_db = self.db_dir / "session" / "session.db"
        if not session_db.exists():
            print("⚠️  session.db 不存在，跳过会话表")
            return
        
        print("💬 合并会话表...")
        
        cursor.execute(f"ATTACH DATABASE '{session_db}' AS session_db")
        
        # 复制 SessionTable（会话列表）
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS session_list AS 
            SELECT * FROM session_db.SessionTable
        """)
        
        count = cursor.execute("SELECT COUNT(*) FROM session_list").fetchone()[0]
        print(f"  ✅ 会话: {count:,} 条")
        
        cursor.execute("DETACH DATABASE session_db")
    
    def _merge_message_tables(self, cursor: sqlite3.Cursor, 
                              test_mode: bool = False, 
                              test_limit: int = 100) -> int:
        """合并所有消息表"""
        print("📨 合并消息表...")
        
        # 创建统一的消息表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_db TEXT,
                source_table TEXT,
                local_id INTEGER,
                server_id INTEGER,
                local_type INTEGER,
                sort_seq INTEGER,
                real_sender_id INTEGER,
                create_time INTEGER,
                status INTEGER,
                source TEXT,
                message_content BLOB
            )
        """)
        
        total_messages = 0
        message_dbs = list(self.message_dir.glob("*.db"))
        
        # 测试模式只处理前 3 个数据库
        if test_mode:
            message_dbs = message_dbs[:3]
        
        for i, db_file in enumerate(message_dbs, 1):
            try:
                source_conn = sqlite3.connect(str(db_file))
                source_cursor = source_conn.cursor()
                
                # 获取所有 Msg_ 表
                source_cursor.execute("""
                    SELECT name FROM sqlite_master 
                    WHERE type='table' AND name LIKE 'Msg_%'
                """)
                tables = [row[0] for row in source_cursor.fetchall()]
                
                # 测试模式只处理前 5 个表
                if test_mode:
                    tables = tables[:5]
                
                db_messages = 0
                for table in tables:
                    # 读取消息（测试模式限制数量）
                    limit_clause = f"LIMIT {test_limit}" if test_mode else ""
                    source_cursor.execute(f"""
                        SELECT local_id, server_id, local_type, sort_seq, real_sender_id,
                               create_time, status, source, message_content
                        FROM {table}
                        ORDER BY create_time DESC
                        {limit_clause}
                    """)
                    
                    rows = source_cursor.fetchall()
                    
                    # 批量插入
                    cursor.executemany("""
                        INSERT INTO messages (
                            source_db, source_table, local_id, server_id, local_type,
                            sort_seq, real_sender_id, create_time, status,
                            source, message_content
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, [(db_file.name, table) + row for row in rows])
                    
                    db_messages += len(rows)
                
                total_messages += db_messages
                print(f"  [{i}/{len(message_dbs)}] {db_file.name}: {db_messages:,} 条")
                
                source_conn.close()
                
            except Exception as e:
                print(f"  ⚠️  处理 {db_file.name} 失败: {e}")
        
        return total_messages
    
    def _create_indexes(self, cursor: sqlite3.Cursor):
        """创建索引加速查询"""
        print("\n🔍 创建索引...")
        
        indexes = [
            ("idx_messages_create_time", "messages", "create_time"),
            ("idx_messages_source_table", "messages", "source_table"),
            ("idx_messages_local_type", "messages", "local_type"),
            ("idx_contact_username", "contact", "username"),
        ]
        
        for idx_name, table, column in indexes:
            try:
                cursor.execute(f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table}({column})")
                print(f"  ✅ {idx_name}")
            except Exception as e:
                print(f"  ⚠️  {idx_name}: {e}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Mac 微信数据库合并工具')
    parser.add_argument('--db-dir', default='app/Database/MacMsg', help='解密后的数据库目录')
    parser.add_argument('--output', default='data/merged_messages.db', help='输出数据库路径')
    parser.add_argument('--no-contact', action='store_true', help='不包含联系人表')
    parser.add_argument('--no-session', action='store_true', help='不包含会话表')
    parser.add_argument('--test', action='store_true', help='测试模式（只合并少量数据）')
    parser.add_argument('--test-limit', type=int, default=100, help='测试模式下每个表的最大行数')
    args = parser.parse_args()
    
    merger = MacDBMerger(args.db_dir)
    merger.merge_message_dbs(
        output_path=args.output,
        include_contact=not args.no_contact,
        include_session=not args.no_session,
        test_mode=args.test,
        test_limit=args.test_limit
    )


if __name__ == '__main__':
    main()
