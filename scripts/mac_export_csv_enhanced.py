#!/usr/bin/env python3
"""改进的 CSV 导出器 - 集成消息解密"""
import sys
sys.path.insert(0, 'app')
from util.message_decryptor import MessageDecryptor
from scripts.mac_export_messages import *

def export_with_decryption(db_dir, output_path, contact_mapper=None):
    """导出并解密消息"""
    decryptor = MessageDecryptor()
    rows = iter_rows(Path(db_dir), latest=0)
    
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    
    fields = ['local_id', 'create_time', 'datetime', 'local_type', 
              'message_content', 'decrypted_content', 'parsed_type']
    
    count = 0
    with output.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        
        for row in rows:
            # 解密消息
            content = row.get('message_content')
            compress = row.get('compress_content')
            
            if isinstance(content, (bytes, str)):
                result = decryptor.decrypt_message(
                    content.encode() if isinstance(content, str) else content,
                    compress.encode() if isinstance(compress, str) else compress
                )
                
                decrypted = result.get('decrypted', '')
                parsed = result.get('parsed', {})
                
                writer.writerow({
                    'local_id': row.get('local_id'),
                    'create_time': row.get('create_time'),
                    'datetime': row.get('datetime'),
                    'local_type': row.get('local_type'),
                    'message_content': str(content)[:100] if content else '',
                    'decrypted_content': decrypted[:200] if decrypted else '',
                    'parsed_type': parsed.get('type', 'unknown')
                })
                count += 1
    
    print(f"✅ 导出 {count} 条消息（已解密）到 {output}")

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--db-dir', default='app/Database/MacMsg')
    parser.add_argument('--output', default='data/messages_decrypted.csv')
    args = parser.parse_args()
    
    export_with_decryption(args.db_dir, args.output)
