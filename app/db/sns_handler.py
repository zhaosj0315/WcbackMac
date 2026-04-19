# -*- coding: utf-8 -*-
from .db_base import DatabaseBase

class SnsHandler(DatabaseBase):
    def get_sns_count(self):
        """获取朋友圈数量"""
        if not self.tables_exist('snstimeline'):
            return 0
        
        sql = "SELECT COUNT(*) FROM SnsTimeLine"
        result = self.execute(sql)
        return result[0][0] if result else 0
    
    def get_sns_list(self, start_index=0, page_size=500):
        """获取朋友圈列表"""
        if not self.tables_exist('snstimeline'):
            return []
        
        sql = """
            SELECT tid, user_name, content
            FROM SnsTimeLine
            ORDER BY tid DESC
            LIMIT ? OFFSET ?
        """
        result = self.execute(sql, (page_size, start_index))
        
        sns_list = []
        if result:
            for row in result:
                sns_list.append({
                    'id': row[0],
                    'user': row[1],
                    'content': row[2][:200] if row[2] else ''
                })
        
        return sns_list
