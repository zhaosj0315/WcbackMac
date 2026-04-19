#!/usr/bin/env python3
import sqlite3
import sys
from pathlib import Path

from mac_message_utils import decode_message_blob, strip_sender_prefix

def generate_wordcloud(db_path, output_path):
    try:
        from wordcloud import WordCloud
        import jieba
    except ImportError:
        print("请安装依赖: pip install wordcloud jieba")
        return False
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT message_content FROM messages 
        WHERE local_type = 1 AND message_content IS NOT NULL
        LIMIT 10000
    """)
    
    texts = []
    for row in cursor.fetchall():
        try:
            text = strip_sender_prefix(decode_message_blob(row[0]))
            if text and not text.lstrip().startswith("<"):
                texts.append(text)
        except:
            pass
    
    conn.close()
    
    if not texts:
        print("没有找到文本消息")
        return False
    
    all_text = ' '.join(texts)
    words = jieba.cut(all_text)
    
    font_candidates = [
        '/System/Library/Fonts/PingFang.ttc',
        '/System/Library/Fonts/STHeiti Light.ttc',
        '/Library/Fonts/Arial Unicode.ttf',
    ]
    font_path = next((path for path in font_candidates if Path(path).exists()), None)

    wc = WordCloud(
        font_path=font_path,
        width=1200,
        height=800,
        background_color='white',
        max_words=200
    ).generate(' '.join(words))
    
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    wc.to_file(output_path)
    print(f"✅ 词云已生成: {output_path}")
    return True

if __name__ == '__main__':
    if len(sys.argv) < 3:
        print("用法: python3 mac_wordcloud.py <数据库路径> <输出图片路径>")
        sys.exit(1)
    generate_wordcloud(sys.argv[1], sys.argv[2])
