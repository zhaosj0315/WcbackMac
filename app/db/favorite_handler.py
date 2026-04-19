# -*- coding: utf-8 -*-
from .db_base import DatabaseBase
from datetime import datetime

class FavoriteHandler(DatabaseBase):
    def get_favorite_count(self):
        """获取收藏数量"""
        if not self.tables_exist('fav_db_item'):
            return 0
        
        sql = "SELECT COUNT(*) FROM fav_db_item"
        result = self.execute(sql)
        return result[0][0] if result else 0
    
    def get_favorite_list(self, start_index=0, page_size=500):
        """获取收藏列表"""
        if not self.tables_exist('fav_db_item'):
            return []
        
        sql = """
            SELECT local_id, type, update_time, content, fromusr
            FROM fav_db_item
            ORDER BY update_time DESC
            LIMIT ? OFFSET ?
        """
        result = self.execute(sql, (page_size, start_index))
        
        favorites = []
        if result:
            for row in result:
                favorites.append({
                    'id': row[0],
                    'type': row[1],
                    'time': datetime.fromtimestamp(row[2]).strftime('%Y-%m-%d %H:%M:%S'),
                    'content': row[3][:200] if row[3] else '',
                    'from': row[4]
                })
        
        return favorites
