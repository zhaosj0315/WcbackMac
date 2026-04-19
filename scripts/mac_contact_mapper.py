#!/usr/bin/env python3
"""
Mac 版本联系人映射模块
从 contact.db 和 session.db 提取 wxid → 昵称/群名 映射
"""
import sqlite3
import json
from pathlib import Path
from typing import Dict, Optional


class MacContactMapper:
    def __init__(self, db_dir: str = "app/Database/MacMsg"):
        self.db_dir = Path(db_dir)
        self.contact_db = self.db_dir / "contact" / "contact.db"
        self.session_db = self.db_dir / "session" / "session.db"
        self.wxid_to_name: Dict[str, str] = {}
        self.wxid_to_remark: Dict[str, str] = {}
        self.chatroom_to_name: Dict[str, str] = {}
        
    def load_contacts(self) -> Dict[str, Dict]:
        """从 contact.db 加载联系人信息"""
        if not self.contact_db.exists():
            print(f"⚠️  contact.db 不存在: {self.contact_db}")
            return {}
        
        conn = sqlite3.connect(str(self.contact_db))
        cursor = conn.cursor()
        
        # 查询所有联系人（Mac 版本使用 contact 表）
        cursor.execute("""
            SELECT username, alias, remark, nick_name, small_head_url
            FROM contact
            WHERE delete_flag = 0
        """)
        
        contacts = {}
        for row in cursor.fetchall():
            wxid = row[0]
            alias = row[1] or ""
            remark = row[2] or ""
            nickname = row[3] or wxid
            small_head_url = row[4] or ""
            
            # 显示名称优先级：备注 > 昵称 > wxid
            display_name = remark if remark else nickname
            
            self.wxid_to_name[wxid] = nickname
            self.wxid_to_remark[wxid] = display_name
            
            contacts[wxid] = {
                'wxid': wxid,
                'alias': alias,
                'nickname': nickname,
                'remark': remark,
                'display_name': display_name,
                'is_chatroom': '@chatroom' in wxid
            }
        
        conn.close()
        print(f"✅ 加载 {len(contacts)} 个联系人")
        return contacts
    
    def load_chatrooms(self) -> Dict[str, Dict]:
        """从 contact.db 加载群聊信息（Mac 版本群聊也在 contact 表）"""
        if not self.contact_db.exists():
            print(f"⚠️  contact.db 不存在: {self.contact_db}")
            return {}
        
        conn = sqlite3.connect(str(self.contact_db))
        cursor = conn.cursor()
        
        # 查询所有群聊（username 包含 @chatroom）
        cursor.execute("""
            SELECT username, nick_name, remark
            FROM contact
            WHERE username LIKE '%@chatroom' AND delete_flag = 0
        """)
        
        chatrooms = {}
        for row in cursor.fetchall():
            chatroom_id = row[0]
            nick_name = row[1] or chatroom_id
            remark = row[2] or ""
            
            # 群名优先级：备注 > 昵称
            chatroom_name = remark if remark else nick_name
            
            self.chatroom_to_name[chatroom_id] = chatroom_name
            
            chatrooms[chatroom_id] = {
                'chatroom_id': chatroom_id,
                'chatroom_name': chatroom_name
            }
        
        conn.close()
        print(f"✅ 加载 {len(chatrooms)} 个群聊")
        return chatrooms
    
    def get_display_name(self, wxid: str) -> str:
        """获取显示名称（优先备注，其次昵称）"""
        return self.wxid_to_remark.get(wxid, wxid)
    
    def get_chatroom_name(self, chatroom_id: str) -> str:
        """获取群聊名称"""
        return self.chatroom_to_name.get(chatroom_id, chatroom_id)
    
    def save_mapping(self, output_path: str = "data/mac_contact_mapping.json"):
        """保存映射到 JSON 文件"""
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        
        mapping = {
            'contacts': self.wxid_to_remark,
            'chatrooms': self.chatroom_to_name
        }
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(mapping, f, ensure_ascii=False, indent=2)
        
        print(f"✅ 映射已保存到 {output_path}")
    
    @classmethod
    def load_mapping(cls, mapping_path: str = "data/mac_contact_mapping.json") -> 'MacContactMapper':
        """从 JSON 文件加载映射"""
        mapper = cls()
        
        if Path(mapping_path).exists():
            with open(mapping_path, 'r', encoding='utf-8') as f:
                mapping = json.load(f)
            
            mapper.wxid_to_remark = mapping.get('contacts', {})
            mapper.chatroom_to_name = mapping.get('chatrooms', {})
            
            print(f"✅ 从 {mapping_path} 加载映射")
        
        return mapper


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Mac 微信联系人映射工具')
    parser.add_argument('--db-dir', default='app/Database/MacMsg', help='解密后的数据库目录')
    parser.add_argument('--output', default='data/mac_contact_mapping.json', help='输出映射文件')
    args = parser.parse_args()
    
    mapper = MacContactMapper(args.db_dir)
    mapper.load_contacts()
    mapper.load_chatrooms()
    mapper.save_mapping(args.output)
    
    print(f"\n📊 统计:")
    print(f"  联系人: {len(mapper.wxid_to_remark)}")
    print(f"  群聊: {len(mapper.chatroom_to_name)}")


if __name__ == '__main__':
    main()
