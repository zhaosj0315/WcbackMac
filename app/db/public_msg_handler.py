# -*- coding: utf-8 -*-
from .msg_handler import MsgHandler

class PublicMsgHandler(MsgHandler):
    def get_public_msg_count(self, wxids=None):
        """获取公众号消息数量"""
        tables = [t for t in self.existed_tables if t.startswith('msg_') 
                  and 'biz' not in t]
        total = 0
        
        for table in tables:
            sql = f"SELECT COUNT(*) FROM {table}"
            result = self.execute(sql)
            if result:
                total += result[0][0]
        
        return {"total": total}
    
    def get_public_msg_list(self, start_index=0, page_size=500):
        """获取公众号消息列表"""
        return self.get_msg_list(start_index, page_size)
