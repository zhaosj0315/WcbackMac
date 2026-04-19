#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import argparse
import pandas as pd
from collections import Counter
from tqdm import tqdm

def parse_csv_file(file_path):
    """解析CSV格式的微信聊天记录"""
    print(f'解析CSV文件: {file_path}')
    df = pd.read_csv(file_path)
    
    # 假设CSV文件包含以下列：时间、发送者、消息内容
    messages = []
    for _, row in df.iterrows():
        messages.append({
            'sender': row['发送者'],
            'content': row['消息内容']
        })
    
    return messages

def create_conversations(messages, min_messages=2):
    """将消息转换为对话格式"""
    conversations = []
    current_conversation = []
    
    # 将张三设为用户，李四设为助手
    for msg in messages:
        # 添加消息到当前对话
        role = 'user' if msg['sender'] == '张三' else 'assistant'
        current_conversation.append({
            'role': role,
            'content': msg['content']
        })
    
    # 添加对话
    if len(current_conversation) >= min_messages:
        conversations.append({
            'conversations': current_conversation
        })
    
    return conversations

def main():
    parser = argparse.ArgumentParser(description='转换CSV格式的微信聊天记录为训练数据')
    parser.add_argument('--csv_file', required=True, help='CSV文件路径')
    parser.add_argument('--output_dir', default='./', help='输出目录')
    args = parser.parse_args()
    
    # 确保输出目录存在
    os.makedirs(args.output_dir, exist_ok=True)
    
    # 解析CSV文件
    messages = parse_csv_file(args.csv_file)
    print(f'从CSV文件中解析出 {len(messages)} 条消息')
    
    # 统计发言最多的用户
    sender_counter = Counter(msg['sender'] for msg in messages)
    top_senders = [sender for sender, _ in sender_counter.most_common(5)]
    print(f'发言最多的5个人: {top_senders}')
    
    # 创建对话
    print('创建对话...')
    conversations = create_conversations(messages, min_messages=1)
    print(f'生成了 {len(conversations)} 个对话样本')
    
    # 分割训练集和验证集
    train_size = int(len(conversations) * 0.9)
    train_data = conversations[:train_size]
    dev_data = conversations[train_size:]
    
    # 保存数据
    train_file = os.path.join(args.output_dir, 'train.json')
    dev_file = os.path.join(args.output_dir, 'dev.json')
    
    with open(train_file, 'w', encoding='utf-8') as f:
        json.dump(train_data, f, ensure_ascii=False, indent=2)
    
    with open(dev_file, 'w', encoding='utf-8') as f:
        json.dump(dev_data, f, ensure_ascii=False, indent=2)
    
    print(f'处理完成! 训练样本: {len(train_data)}, 验证样本: {len(dev_data)}')
    print(f'训练数据保存至: {train_file}')
    print(f'验证数据保存至: {dev_file}')

if __name__ == '__main__':
    main() 