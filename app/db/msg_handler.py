# -*- coding: utf-8 -*-
from .db_base import DatabaseBase
from datetime import datetime

class MsgHandler(DatabaseBase):
    def add_msg_index(self):
        """添加消息索引"""
        tables = [t for t in self.existed_tables if t.startswith('msg_')]
        for table in tables:
            self.add_index(table, ['create_time', 'real_sender_id'])
    
    def get_msg_count(self, wxids=None):
        """获取消息数量"""
        tables = [t for t in self.existed_tables if t.startswith('msg_')]
        total = 0
        counts = {}
        
        for table in tables:
            sql = f"SELECT COUNT(*) FROM {table}"
            result = self.execute(sql)
            if result:
                total += result[0][0]
        
        return {"total": total}
    
    def get_msg_list(self, start_index=0, page_size=500, 
                     start_time=None, end_time=None):
        """获取消息列表"""
        tables = [t for t in self.existed_tables if t.startswith('msg_')]
        all_msgs = []
        
        for table in tables:
            sql = f"""
                SELECT local_id, create_time, local_type, 
                       message_content, real_sender_id
                FROM {table}
                ORDER BY create_time DESC
                LIMIT ? OFFSET ?
            """
            result = self.execute(sql, (page_size, start_index))
            if result:
                for row in result:
                    all_msgs.append({
                        'id': row[0],
                        'time': datetime.fromtimestamp(row[1]).strftime('%Y-%m-%d %H:%M:%S'),
                        'type': row[2],
                        'content': row[3] if row[3] else '',
                        'sender': row[4]
                    })
        
        return all_msgs[:page_size]
