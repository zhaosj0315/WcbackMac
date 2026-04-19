import os
import json
import docx
from pathlib import Path
from typing import List, Dict, Any
import re
from datetime import datetime

def extract_messages_from_docx(docx_path: str) -> List[Dict[str, Any]]:
    doc = docx.Document(docx_path)
    messages = []
    current_conversation = []
    
    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue
            
        # 匹配消息格式：时间 发送者: 内容
        match = re.match(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) (.*?): (.*)', text)
        if match:
            timestamp, sender, content = match.groups()
            
            # 如果是新对话的开始，保存之前的对话
            if current_conversation and len(current_conversation) > 1:
                messages.append({
                    "conversations": current_conversation
                })
                current_conversation = []
            
            # 添加用户消息
            current_conversation.append({
                "role": "user",
                "content": f"{sender}: {content}"
            })
            
            # 添加助手回复（这里可以根据需要修改）
            current_conversation.append({
                "role": "assistant",
                "content": "我明白了，这是一个关于京东内购的群聊消息。"
            })
    
    # 添加最后一个对话
    if current_conversation and len(current_conversation) > 1:
        messages.append({
            "conversations": current_conversation
        })
    
    return messages

def process_docx_directory(input_dir: str, output_file: str):
    all_messages = []
    docx_files = sorted([f for f in os.listdir(input_dir) if f.endswith('.docx')])
    
    for docx_file in docx_files:
        print(f"Processing {docx_file}...")
        docx_path = os.path.join(input_dir, docx_file)
        messages = extract_messages_from_docx(docx_path)
        all_messages.extend(messages)
    
    # 保存为JSON文件
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(all_messages, f, ensure_ascii=False, indent=2)
    
    print(f"Processed {len(docx_files)} files, extracted {len(all_messages)} conversations")
    print(f"Saved to {output_file}")

def main():
    input_dir = "/Users/zhaosj/Desktop/data/聊天记录/JD921京东捡漏内购群🚚(43661891285@chatroom)"
    output_file = "data/raw_data.json"
    
    # 创建输出目录
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    
    process_docx_directory(input_dir, output_file)

if __name__ == "__main__":
    main() 