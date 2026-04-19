#!/usr/bin/env python3
from flask import Flask, render_template_string, jsonify, request
import sqlite3
import json
from pathlib import Path

app = Flask(__name__)
DB_PATH = None
CONTACT_MAPPING = {}

HTML_TEMPLATE = '''
<!DOCTYPE html>
<html>
<head>
    <title>Mac 微信查看器</title>
    <meta charset="utf-8">
    <style>
        body { font-family: Arial, sans-serif; margin: 20px; }
        .container { max-width: 1200px; margin: 0 auto; }
        .stats { display: flex; gap: 20px; margin-bottom: 20px; }
        .stat-card { flex: 1; padding: 20px; background: #f5f5f5; border-radius: 8px; }
        .messages { margin-top: 20px; }
        .message { padding: 10px; margin: 5px 0; background: white; border-radius: 4px; }
    </style>
</head>
<body>
    <div class="container">
        <h1>Mac 微信查看器</h1>
        <div class="stats" id="stats"></div>
        <div class="messages" id="messages"></div>
    </div>
    <script>
        fetch('/api/stats').then(r => r.json()).then(data => {
            document.getElementById('stats').innerHTML = `
                <div class="stat-card"><h3>总消息数</h3><p>${data.total_messages}</p></div>
                <div class="stat-card"><h3>联系人数</h3><p>${data.total_contacts}</p></div>
            `;
        });
        
        fetch('/api/messages?limit=50').then(r => r.json()).then(data => {
            const html = data.map(m => `
                <div class="message">
                    <strong>${m.time}</strong> - ${m.content}
                </div>
            `).join('');
            document.getElementById('messages').innerHTML = html;
        });
    </script>
</body>
</html>
'''

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route('/api/stats')
def stats():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM messages")
    total = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(DISTINCT source_table) FROM messages")
    contacts = cursor.fetchone()[0]
    conn.close()
    return jsonify({'total_messages': total, 'total_contacts': contacts})

@app.route('/api/messages')
def messages():
    limit = request.args.get('limit', 50, type=int)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(f"""
        SELECT create_time, message_content 
        FROM messages 
        WHERE local_type = 1
        ORDER BY create_time DESC 
        LIMIT {limit}
    """)
    results = []
    for row in cursor.fetchall():
        from datetime import datetime
        content = row[1].decode('utf-8', errors='ignore') if isinstance(row[1], bytes) else str(row[1])
        results.append({
            'time': datetime.fromtimestamp(row[0]).strftime('%Y-%m-%d %H:%M:%S'),
            'content': content[:100]
        })
    conn.close()
    return jsonify(results)

def start_server(db_path, port=5000):
    global DB_PATH
    DB_PATH = db_path
    print(f"🌐 Web UI 启动: http://localhost:{port}")
    app.run(host='0.0.0.0', port=port, debug=False)

if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        print("用法: python3 mac_web_ui.py <数据库路径> [端口]")
        sys.exit(1)
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 5000
    start_server(sys.argv[1], port)
