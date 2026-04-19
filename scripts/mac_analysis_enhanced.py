#!/usr/bin/env python3
import sys
sys.path.insert(0, 'app')
from db.db_handler import DBHandler
import json

def analyze_all(db_path, output_path):
    config = {"path": db_path, "key": "analysis"}
    handler = DBHandler(config)
    
    stats = handler.get_all_counts()
    
    print("=" * 50)
    print("数据统计")
    print("=" * 50)
    print(f"消息总数: {stats['messages'].get('total', 0)}")
    print(f"公众号消息: {stats['public_msgs'].get('total', 0)}")
    print(f"收藏数量: {stats['favorites']}")
    print(f"朋友圈数量: {stats['sns']}")
    print("=" * 50)
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)
    
    print(f"✅ 统计完成: {output_path}")

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', required=True)
    parser.add_argument('--output', default='data/analysis_enhanced.json')
    args = parser.parse_args()
    
    analyze_all(args.db, args.output)
