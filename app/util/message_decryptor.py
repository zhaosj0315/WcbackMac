#!/usr/bin/env python3
"""
消息内容解密模块
解压缩 CompressContent，解析 XML，解密文本
"""
import zlib
import xml.etree.ElementTree as ET
from typing import Optional, Dict, Any

try:
    import zstd
except ImportError:
    zstd = None


class MessageDecryptor:
    """消息内容解密器"""

    @staticmethod
    def strip_sender_prefix(text: str) -> str:
        if '\n' in text:
            first, rest = text.split('\n', 1)
            if ':' in first and len(first) < 80:
                return rest
        return text
    
    @staticmethod
    def decompress_content(compressed_data: bytes) -> Optional[str]:
        """解压缩消息内容"""
        if not compressed_data:
            return None

        if zstd is not None and compressed_data[:4] == b'\x28\xb5\x2f\xfd':
            try:
                return zstd.decompress(compressed_data).decode('utf-8', errors='replace')
            except Exception:
                pass
        
        try:
            # 尝试 zlib 解压
            decompressed = zlib.decompress(compressed_data)
            return decompressed.decode('utf-8', errors='ignore')
        except:
            try:
                # 尝试 zlib 带 header
                decompressed = zlib.decompress(compressed_data, zlib.MAX_WBITS | 16)
                return decompressed.decode('utf-8', errors='ignore')
            except:
                return None
    
    @staticmethod
    def parse_xml_message(xml_str: str) -> Dict[str, Any]:
        """解析 XML 格式的消息"""
        if not xml_str or not xml_str.strip().startswith('<'):
            return {'type': 'text', 'content': xml_str}
        
        try:
            root = ET.fromstring(xml_str)
            msg_type = root.tag
            
            # 分享消息
            if msg_type == 'msg':
                appmsg = root.find('.//appmsg')
                if appmsg is not None:
                    return {
                        'type': 'share',
                        'title': appmsg.findtext('title', ''),
                        'des': appmsg.findtext('des', ''),
                        'url': appmsg.findtext('url', ''),
                        'thumburl': appmsg.findtext('thumburl', '')
                    }
            
            # 文件消息
            if msg_type == 'msg':
                appmsg = root.find('.//appmsg')
                if appmsg is not None and appmsg.findtext('type') == '6':
                    return {
                        'type': 'file',
                        'title': appmsg.findtext('title', ''),
                        'filesize': appmsg.findtext('appattach/totallen', '0')
                    }
            
            # 位置消息
            location = root.find('.//location')
            if location is not None:
                return {
                    'type': 'location',
                    'label': location.get('label', ''),
                    'poiname': location.get('poiname', ''),
                    'x': location.get('x', ''),
                    'y': location.get('y', '')
                }
            
            return {'type': 'xml', 'content': xml_str}
        except:
            return {'type': 'text', 'content': xml_str}
    
    @staticmethod
    def decrypt_message(message_content: bytes, compress_content: bytes = None) -> Dict[str, Any]:
        """完整解密消息"""
        result = {'raw': message_content, 'decrypted': None, 'parsed': None}
        
        # 1. 尝试直接解码
        if isinstance(message_content, bytes):
            decompressed = MessageDecryptor.decompress_content(message_content)
            if decompressed:
                decompressed = MessageDecryptor.strip_sender_prefix(decompressed)
                result['decrypted'] = decompressed
                result['parsed'] = MessageDecryptor.parse_xml_message(decompressed)
                return result
            try:
                text = message_content.decode('utf-8', errors='ignore')
                text = MessageDecryptor.strip_sender_prefix(text)
                result['decrypted'] = text
                result['parsed'] = MessageDecryptor.parse_xml_message(text)
                return result
            except:
                pass
        
        # 2. 尝试解压缩
        if compress_content:
            decompressed = MessageDecryptor.decompress_content(compress_content)
            if decompressed:
                decompressed = MessageDecryptor.strip_sender_prefix(decompressed)
                result['decrypted'] = decompressed
                result['parsed'] = MessageDecryptor.parse_xml_message(decompressed)
                return result
        
        return result


def test_decryptor():
    """测试解密器"""
    import sqlite3
    
    db_path = "app/Database/MacMsg/message/message_0.db"
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT local_id, local_type, message_content, compress_content
        FROM Msg_77a92a5c702d2f29a9f9ac173b45d689
        LIMIT 10
    """)
    
    decryptor = MessageDecryptor()
    
    for row in cursor.fetchall():
        local_id, msg_type, content, compress = row
        result = decryptor.decrypt_message(content, compress)
        
        print(f"\n消息 {local_id} (类型 {msg_type}):")
        if result['parsed']:
            print(f"  解析结果: {result['parsed']}")
        elif result['decrypted']:
            print(f"  解密内容: {result['decrypted'][:100]}")
        else:
            print(f"  原始数据: {str(content)[:100]}")
    
    conn.close()


if __name__ == '__main__':
    test_decryptor()
