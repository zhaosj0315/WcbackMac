import json
import argparse
from pathlib import Path
import random
from typing import List, Dict, Any

def load_json_file(file_path: str) -> List[Dict[str, Any]]:
    with open(file_path, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_json_file(data: List[Dict[str, Any]], file_path: str):
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def split_data(data: List[Dict[str, Any]], train_ratio: float = 0.9) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    random.shuffle(data)
    split_idx = int(len(data) * train_ratio)
    return data[:split_idx], data[split_idx:]

def process_conversations(conversations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    processed = []
    for conv in conversations:
        # 确保对话格式正确
        if not isinstance(conv, dict) or 'conversations' not in conv:
            continue
            
        # 验证对话格式
        valid_conv = True
        for msg in conv['conversations']:
            if not isinstance(msg, dict) or 'role' not in msg or 'content' not in msg:
                valid_conv = False
                break
            if msg['role'] not in ['system', 'user', 'assistant']:
                valid_conv = False
                break
                
        if valid_conv:
            processed.append(conv)
            
    return processed

def main():
    parser = argparse.ArgumentParser(description='Prepare training data for MemoAI')
    parser.add_argument('--input', type=str, required=True, help='Input JSON file path')
    parser.add_argument('--output_dir', type=str, required=True, help='Output directory for processed data')
    parser.add_argument('--train_ratio', type=float, default=0.9, help='Ratio of training data')
    args = parser.parse_args()
    
    # 创建输出目录
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # 加载数据
    data = load_json_file(args.input)
    print(f"Loaded {len(data)} conversations")
    
    # 处理对话
    processed_data = process_conversations(data)
    print(f"Processed {len(processed_data)} valid conversations")
    
    # 分割训练集和验证集
    train_data, dev_data = split_data(processed_data, args.train_ratio)
    print(f"Split into {len(train_data)} training and {len(dev_data)} validation conversations")
    
    # 保存处理后的数据
    save_json_file(train_data, output_dir / 'train.json')
    save_json_file(dev_data, output_dir / 'dev.json')
    print(f"Saved processed data to {output_dir}")

if __name__ == "__main__":
    main() 