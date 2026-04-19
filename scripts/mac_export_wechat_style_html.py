#!/usr/bin/env python3
"""微信样式 HTML 导出器"""
import sqlite3
from pathlib import Path
from datetime import datetime

WECHAT_STYLE_CSS = """
<style>
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto; background: #f5f5f5; margin: 0; padding: 20px; }
.chat-container { max-width: 800px; margin: 0 auto; background: white; border-radius: 8px; padding: 20px; }
.message { display: flex; margin: 15px 0; align-items: flex-start; }
.message.sent { flex-direction: row-reverse; }
.avatar { width: 40px; height: 40px; border-radius: 50%; margin: 0 10px; background: #ddd; }
.bubble { max-width: 60%; padding: 10px 15px; border-radius: 8px; word-wrap: break-word; }
.message.received .bubble { background: white; border: 1px solid #e0e0e0; }
.message.sent .bubble { background: #95ec69; }
.time { text-align: center; color: #999; font-size: 12px; margin: 10px 0; }
.image { max-width: 200px; border-radius: 4px; }
</style>
"""

def export_wechat_style_html(db_path, output_path, contact_name="聊天记录"):
    """导出微信样式 HTML"""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT local_id, real_sender_id, local_type, create_time, message_content
        FROM messages
        ORDER BY create_time ASC
        LIMIT 1000
    """)
    
    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>{contact_name}</title>
    {WECHAT_STYLE_CSS}
</head>
<body>
<div class="chat-container">
    <h2>{contact_name}</h2>
"""
    
    for row in cursor.fetchall():
        local_id, sender_id, msg_type, create_time, content = row
        
        time_str = datetime.fromtimestamp(create_time).strftime('%Y-%m-%d %H:%M:%S')
        is_sent = (sender_id == 0)
        msg_class = "sent" if is_sent else "received"
        
        # 解码内容
        if isinstance(content, bytes):
            try:
                content = content.decode('utf-8', errors='ignore')
            except:
                content = "[二进制内容]"
        
        html += f"""
    <div class="time">{time_str}</div>
    <div class="message {msg_class}">
        <div class="avatar"></div>
        <div class="bubble">{content[:200] if content else '[空消息]'}</div>
    </div>
"""
    
    html += """
</div>
</body>
</html>
"""
    
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)
    
    conn.close()
    print(f"✅ 微信样式 HTML 已导出: {output_path}")

if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        print("用法: python3 mac_export_wechat_style_html.py <数据库路径> [输出路径]")
        sys.exit(1)
    
    db_path = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) > 2 else "data/wechat_style.html"
    export_wechat_style_html(db_path, output_path)
