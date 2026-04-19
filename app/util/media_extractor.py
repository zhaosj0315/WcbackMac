#!/usr/bin/env python3
"""
媒体文件提取模块
从 hardlink.db 和文件系统提取图片/视频/语音
"""
import sqlite3
import shutil
from pathlib import Path
from typing import List, Dict, Optional


class MediaExtractor:
    """媒体文件提取器"""
    
    def __init__(self, db_dir: str = "app/Database/MacMsg", 
                 wx_files_dir: str = None):
        self.db_dir = Path(db_dir)
        self.hardlink_db = self.db_dir / "hardlink" / "hardlink.db"
        
        # 自动查找微信文件目录
        if wx_files_dir is None:
            wx_files_dir = self._find_wx_files_dir()
        self.wx_files_dir = Path(wx_files_dir) if wx_files_dir else None
    
    def _find_wx_files_dir(self) -> Optional[str]:
        """自动查找微信文件目录"""
        import os
        home = Path.home()
        possible_paths = [
            home / "Library/Containers/com.tencent.xinWeChat/Data/Library/Application Support/com.tencent.xinWeChat",
            home / "Documents/WeChat Files"
        ]
        
        for path in possible_paths:
            if path.exists():
                return str(path)
        return None
    
    def extract_images(self, output_dir: str = "data/media/images", 
                      limit: int = None) -> List[Dict]:
        """提取图片文件"""
        if not self.hardlink_db.exists():
            print(f"❌ hardlink.db 不存在: {self.hardlink_db}")
            return []
        
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)
        
        conn = sqlite3.connect(str(self.hardlink_db))
        cursor = conn.cursor()
        
        # 查询图片记录
        sql = """
            SELECT msgId, msgSvrId, createTime, reserved1, reserved2
            FROM HardLinkImageAttribute
            ORDER BY createTime DESC
        """
        if limit:
            sql += f" LIMIT {limit}"
        
        cursor.execute(sql)
        results = []
        
        for row in cursor.fetchall():
            msg_id, msg_svr_id, create_time, path1, path2 = row
            
            # 尝试从文件系统提取
            if self.wx_files_dir and path1:
                source_path = self.wx_files_dir / path1
                if source_path.exists():
                    dest_path = output / f"{msg_id}_{source_path.name}"
                    shutil.copy2(source_path, dest_path)
                    results.append({
                        'msg_id': msg_id,
                        'type': 'image',
                        'path': str(dest_path),
                        'create_time': create_time
                    })
        
        conn.close()
        print(f"✅ 提取 {len(results)} 张图片到 {output}")
        return results
    
    def extract_videos(self, output_dir: str = "data/media/videos",
                      limit: int = None) -> List[Dict]:
        """提取视频文件"""
        if not self.hardlink_db.exists():
            return []
        
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)
        
        conn = sqlite3.connect(str(self.hardlink_db))
        cursor = conn.cursor()
        
        # 查询视频记录
        sql = """
            SELECT msgId, msgSvrId, createTime, reserved1
            FROM HardLinkVideoAttribute
            ORDER BY createTime DESC
        """
        if limit:
            sql += f" LIMIT {limit}"
        
        try:
            cursor.execute(sql)
            results = []
            
            for row in cursor.fetchall():
                msg_id, msg_svr_id, create_time, path1 = row
                
                if self.wx_files_dir and path1:
                    source_path = self.wx_files_dir / path1
                    if source_path.exists():
                        dest_path = output / f"{msg_id}_{source_path.name}"
                        shutil.copy2(source_path, dest_path)
                        results.append({
                            'msg_id': msg_id,
                            'type': 'video',
                            'path': str(dest_path),
                            'create_time': create_time
                        })
            
            conn.close()
            print(f"✅ 提取 {len(results)} 个视频到 {output}")
            return results
        except sqlite3.OperationalError:
            conn.close()
            print("⚠️  HardLinkVideoAttribute 表不存在")
            return []
    
    def decrypt_dat_image(self, dat_path: str, output_path: str) -> bool:
        """解密 .dat 图片文件"""
        try:
            with open(dat_path, 'rb') as f:
                data = f.read()
            
            # 微信图片加密：XOR 0xFF
            if len(data) > 0:
                first_byte = data[0]
                # 检测是否是加密的
                if first_byte != 0xFF and first_byte != 0x89:  # PNG
                    key = first_byte ^ 0xFF
                    decrypted = bytes([b ^ key for b in data])
                    
                    with open(output_path, 'wb') as f:
                        f.write(decrypted)
                    return True
            
            # 未加密，直接复制
            shutil.copy2(dat_path, output_path)
            return True
        except Exception as e:
            print(f"解密失败: {e}")
            return False


def main():
    import argparse
    parser = argparse.ArgumentParser(description='媒体文件提取工具')
    parser.add_argument('--db-dir', default='app/Database/MacMsg', help='数据库目录')
    parser.add_argument('--wx-dir', help='微信文件目录')
    parser.add_argument('--output', default='data/media', help='输出目录')
    parser.add_argument('--limit', type=int, help='限制提取数量')
    parser.add_argument('--type', choices=['image', 'video', 'all'], default='all', help='提取类型')
    args = parser.parse_args()
    
    extractor = MediaExtractor(args.db_dir, args.wx_dir)
    
    if args.type in ['image', 'all']:
        extractor.extract_images(f"{args.output}/images", args.limit)
    
    if args.type in ['video', 'all']:
        extractor.extract_videos(f"{args.output}/videos", args.limit)


if __name__ == '__main__':
    main()
