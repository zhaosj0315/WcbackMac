#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import argparse
import requests
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)

def get_ollama_response(model, prompt, system_prompt=None):
    """调用 Ollama API 获取响应"""
    url = 'http://localhost:11434/api/generate'
    
    data = {
        'model': model,
        'prompt': prompt,
        'stream': False
    }
    
    if system_prompt:
        data['system'] = system_prompt
    
    try:
        response = requests.post(url, json=data)
        response.raise_for_status()
        return response.json()['response']
    except Exception as e:
        return f'Error: {str(e)}'

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/chat', methods=['POST'])
def chat():
    data = request.json
    prompt = data.get('prompt', '')
    model = data.get('model', 'my-chat-ai')
    
    response = get_ollama_response(model, prompt)
    return jsonify({'response': response})

def main():
    parser = argparse.ArgumentParser(description='启动 Web 界面')
    parser.add_argument('--model', default='my-chat-ai', help='Ollama 模型名称')
    parser.add_argument('--port', type=int, default=5000, help='Web 服务器端口')
    parser.add_argument('--host', default='127.0.0.1', help='Web 服务器主机')
    args = parser.parse_args()
    
    # 创建模板目录
    os.makedirs('templates', exist_ok=True)
    
    # 创建 HTML 模板
    with open('templates/index.html', 'w', encoding='utf-8') as f:
        f.write('''
<!DOCTYPE html>
<html>
<head>
    <title>AI 助手</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            max-width: 800px;
            margin: 0 auto;
            padding: 20px;
            background-color: #f5f5f5;
        }
        .chat-container {
            background-color: white;
            border-radius: 10px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            padding: 20px;
            margin-bottom: 20px;
        }
        .message {
            margin-bottom: 15px;
            padding: 10px;
            border-radius: 5px;
        }
        .user-message {
            background-color: #e3f2fd;
            margin-left: 20%;
        }
        .assistant-message {
            background-color: #f5f5f5;
            margin-right: 20%;
        }
        .input-container {
            display: flex;
            gap: 10px;
        }
        input[type="text"] {
            flex: 1;
            padding: 10px;
            border: 1px solid #ddd;
            border-radius: 5px;
            font-size: 16px;
        }
        button {
            padding: 10px 20px;
            background-color: #2196f3;
            color: white;
            border: none;
            border-radius: 5px;
            cursor: pointer;
            font-size: 16px;
        }
        button:hover {
            background-color: #1976d2;
        }
        .loading {
            text-align: center;
            color: #666;
            margin: 10px 0;
        }
    </style>
</head>
<body>
    <div class="chat-container">
        <div id="chat-messages"></div>
        <div class="loading" id="loading" style="display: none;">AI 正在思考...</div>
        <div class="input-container">
            <input type="text" id="user-input" placeholder="输入你的问题..." autofocus>
            <button onclick="sendMessage()">发送</button>
        </div>
    </div>

    <script>
        function addMessage(content, isUser) {
            const messagesDiv = document.getElementById('chat-messages');
            const messageDiv = document.createElement('div');
            messageDiv.className = `message ${isUser ? 'user-message' : 'assistant-message'}`;
            messageDiv.textContent = content;
            messagesDiv.appendChild(messageDiv);
            messagesDiv.scrollTop = messagesDiv.scrollHeight;
        }

        async function sendMessage() {
            const input = document.getElementById('user-input');
            const message = input.value.trim();
            if (!message) return;

            // 显示用户消息
            addMessage(message, true);
            input.value = '';

            // 显示加载状态
            const loading = document.getElementById('loading');
            loading.style.display = 'block';

            try {
                const response = await fetch('/chat', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        prompt: message,
                        model: 'my-chat-ai'
                    })
                });

                const data = await response.json();
                addMessage(data.response, false);
            } catch (error) {
                addMessage('Error: ' + error.message, false);
            } finally {
                loading.style.display = 'none';
            }
        }

        // 支持按回车发送消息
        document.getElementById('user-input').addEventListener('keypress', function(e) {
            if (e.key === 'Enter') {
                sendMessage();
            }
        });
    </script>
</body>
</html>
        ''')
    
    app.run(host=args.host, port=args.port)

if __name__ == '__main__':
    main() 