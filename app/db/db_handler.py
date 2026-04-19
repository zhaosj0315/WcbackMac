# -*- coding: utf-8 -*-
from .db_base import DatabaseBase

class DBHandler(DatabaseBase):
    def __init__(self, db_config, my_wxid=""):
        super().__init__(db_config)
        self.my_wxid = my_wxid
    
    def get_msg_count(self):
        tables = [t for t in self.existed_tables if t.startswith('msg_')]
        total = 0
        for table in tables:
            result = self.execute(f"SELECT COUNT(*) FROM {table}")
            if result: total += result[0][0]
        return {"total": total}
    
    def get_favorite_count(self):
        if not self.tables_exist('fav_db_item'): return 0
        result = self.execute("SELECT COUNT(*) FROM fav_db_item")
        return result[0][0] if result else 0
    
    def get_sns_count(self):
        if not self.tables_exist('snstimeline'): return 0
        result = self.execute("SELECT COUNT(*) FROM SnsTimeLine")
        return result[0][0] if result else 0
    
    def get_all_counts(self):
        return {
            'messages': self.get_msg_count(),
            'favorites': self.get_favorite_count(),
            'sns': self.get_sns_count()
        }
