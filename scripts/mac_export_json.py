#!/usr/bin/env python3
"""
Mac 版本 JSON 导出器
导出结构化的 JSON 数据，方便程序处理
"""
import json
import sqlite3
from pathlib import Path
from datetime import datetime
from typing import Optional

from mac_message_utils import MacMediaResolver, parse_message


class MacJSONExporter:
    def __init__(self, db_dir: str = "app/Database/MacMsg", contact_mapper=None):
        self.db_dir = Path(db_dir)
        self.contact_mapper = contact_mapper
        self.message_dir = self.db_dir / "message"
        self.media_resolver = MacMediaResolver(self.db_dir)
    
    def export_messages(
        self,
        output_path: str,
        limit: Optional[int] = None,
        media_dir: str | None = None,
        table_name: str | None = None,
    ):
        """导出消息到 JSON"""
        messages = []
        exported = 0
        media_output = Path(media_dir) if media_dir else Path(output_path).with_suffix("") / "media"
        
        # 扫描消息表；指定 table 时会聚合所有包含该表的数据库分片。
        for db_file in sorted(self.message_dir.glob("message_*.db")):
            try:
                conn = sqlite3.connect(str(db_file))
                cursor = conn.cursor()
                
                cursor.execute("""
                    SELECT name FROM sqlite_master 
                    WHERE type='table' AND name LIKE 'Msg_%'
                """)
                tables = [row[0] for row in cursor.fetchall()]
                if table_name:
                    tables = [table for table in tables if table == table_name]
                
                for table in tables:
                    cursor.execute(f"""
                        SELECT local_id, server_id, local_type, create_time,
                               real_sender_id, message_content, source, sort_seq
                        FROM {table}
                        ORDER BY create_time DESC
                    """)
                    
                    for row in cursor.fetchall():
                        local_id, server_id, msg_type, create_time, sender_id, content, source, sort_seq = row
                        parsed = parse_message(
                            msg_type,
                            content,
                            table_name=table,
                            local_id=local_id,
                            create_time=create_time,
                            sort_seq=sort_seq,
                            resolver=self.media_resolver,
                        )
                        media = {
                            "kind": parsed.media_kind,
                            "path": parsed.media_path,
                            "mime": parsed.media_mime,
                        }

                        msg = {
                            'id': local_id,
                            'server_id': server_id,
                            'type': msg_type,
                            'type_name': parsed.type_name,
                            'timestamp': create_time,
                            'datetime': datetime.fromtimestamp(create_time).isoformat() if create_time else None,
                            'sender_id': sender_id,
                            'content': parsed.text,
                            'title': parsed.title,
                            'description': parsed.description,
                            'url': parsed.url,
                            'xml': parsed.xml,
                            'source': self._decode_content(source),
                            'media': media,
                            'table': table,
                            'db_file': db_file.name
                        }
                        
                        # 添加联系人信息
                        if self.contact_mapper:
                            wxid = self._extract_wxid_from_table(table)
                            msg['conversation_with'] = self.contact_mapper.get_display_name(wxid)
                        
                        messages.append(msg)
                        exported += 1
                        if limit and not table_name and exported >= limit:
                            raise StopIteration
                
                conn.close()
            except StopIteration:
                try:
                    conn.close()
                except Exception:
                    pass
                break
            except Exception as e:
                print(f"⚠️  处理 {db_file.name} 失败: {e}")
        
        # 按时间排序
        messages.sort(key=lambda x: x['timestamp'] or 0, reverse=True)
        if limit:
            messages = messages[:limit]

        for msg in messages:
            media = msg["media"]
            if media["kind"] == "video" and media["path"]:
                copied = self.media_resolver.copy_media(media["path"], media_output / "video", prefix=f"{msg['id']}_")
                media["exported_path"] = str(copied)
            elif media["kind"] == "voice":
                voice_data = self.media_resolver.get_voice_data(msg["table"], msg["id"], msg["timestamp"])
                if voice_data:
                    voice_path = self.media_resolver.write_voice(
                        voice_data, media_output / "voice", f"{msg['id']}_{msg['timestamp']}.silk"
                    )
                    media["exported_path"] = str(voice_path)
                    media["mime"] = "audio/silk"
            elif media["kind"] == "image" and media["path"]:
                media["exported_path"] = media["path"]
        
        # 写入 JSON
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(messages, f, ensure_ascii=False, indent=2)
        
        print(f"✅ 导出 {len(messages)} 条消息到 {output_path}")
    
    def _decode_content(self, content):
        """解码内容"""
        if content is None:
            return None
        if isinstance(content, bytes):
            from mac_message_utils import decode_message_blob, strip_sender_prefix
            return strip_sender_prefix(decode_message_blob(content))
        return str(content)
    
    def _extract_wxid_from_table(self, table_name: str) -> str:
        """从表名提取 wxid"""
        if self.contact_mapper:
            import hashlib
            # 尝试反查
            for wxid in list(self.contact_mapper.wxid_to_remark.keys()):
                if hashlib.md5(wxid.encode()).hexdigest() == table_name.replace('Msg_', ''):
                    return wxid
        return table_name.replace('Msg_', '')


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Mac 微信 JSON 导出')
    parser.add_argument('--db-dir', default='app/Database/MacMsg', help='数据库目录')
    parser.add_argument('--output', default='data/messages.json', help='输出文件')
    parser.add_argument('--mapping', default='data/mac_contact_mapping.json', help='联系人映射')
    parser.add_argument('--limit', type=int, help='最多导出消息数')
    parser.add_argument('--media-dir', help='导出语音/视频附件目录，默认与 JSON 同名目录下 media/')
    parser.add_argument('--table', help='只导出指定 Msg_ 表，并自动聚合所有数据库分片')
    args = parser.parse_args()
    
    # 加载联系人映射
    from mac_contact_mapper import MacContactMapper
    mapper = None
    if Path(args.mapping).exists():
        mapper = MacContactMapper.load_mapping(args.mapping)
    
    exporter = MacJSONExporter(args.db_dir, mapper)
    exporter.export_messages(args.output, args.limit, args.media_dir, args.table)


if __name__ == '__main__':
    main()
