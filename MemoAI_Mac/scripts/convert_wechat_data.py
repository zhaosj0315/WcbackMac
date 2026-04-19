#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import argparse
from bs4 import BeautifulSoup
from tqdm import tqdm

def parse_html_file(file_path):
    """解析HTML格式的微信聊天记录"""
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    soup = BeautifulSoup(content, 'html.parser')
    messages = []
    
    for msg in soup.find_all('div', class_='message'):
        sender = msg.find('div', class_='sender').text.strip()
        content = msg.find('div', class_='content').text.strip()
        messages.append({
            'sender': sender,
            'content': content
        })
    
    return messages

def parse_txt_file(file_path):
    """解析TXT格式的微信聊天记录"""
    messages = []
    current_sender = None
    current_content = []
    
    with open(file_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
                
            if line.startswith('[') and ']' in line:
                if current_sender and current_content:
                    messages.append({
                        'sender': current_sender,
                        'content': '\n'.join(current_content)
                    })
                current_sender = line[line.find(']')+1:].strip()
                current_content = []
            else:
                current_content.append(line)
    
    if current_sender and current_content:
        messages.append({
            'sender': current_sender,
            'content': '\n'.join(current_content)
        })
    
    return messages

def create_conversations(messages, my_name):
    """将消息转换为对话格式"""
    conversations = []
    current_conversation = []
    
    for msg in messages:
        role = 'assistant' if msg['sender'] != my_name else 'user'
        current_conversation.append({
            'role': role,
            'content': msg['content']
        })
        
        if len(current_conversation) >= 2:
            conversations.append({
                'conversations': current_conversation
            })
            current_conversation = []
    
    return conversations

def main():
    parser = argparse.ArgumentParser(description='转换微信聊天记录为训练数据')
    parser.add_argument('--my_name', required=True, help='你的微信名称')
    parser.add_argument('--data_dir', default='./wechat_exports', help='微信聊天记录导出文件目录')
    parser.add_argument('--output_dir', default='./', help='输出目录')
    args = parser.parse_args()
    
    # 确保输出目录存在
    os.makedirs(args.output_dir, exist_ok=True)
    
    all_messages = []
    for filename in os.listdir(args.data_dir):
        file_path = os.path.join(args.data_dir, filename)
        if filename.endswith('.html'):
            messages = parse_html_file(file_path)
        elif filename.endswith('.txt'):
            messages = parse_txt_file(file_path)
        else:
            continue
        all_messages.extend(messages)
    
    print(f'总共解析出 {len(all_messages)} 条消息')
    
    # 创建对话
    print('创建对话...')
    conversations = create_conversations(all_messages, args.my_name)
    
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