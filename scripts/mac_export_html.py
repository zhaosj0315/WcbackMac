#!/usr/bin/env python3
"""
Mac 版本 HTML 导出器
将聊天记录导出为 HTML 格式，支持图片、视频、表情包
"""
import sqlite3
import json
import xml.etree.ElementTree as ET
import mimetypes
import re
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional
import html

from mac_message_utils import MacMediaResolver, data_uri, parse_message

try:
    import zstd
    _HAS_ZSTD = True
except ImportError:
    _HAS_ZSTD = False

try:
    import lz4.block
    _HAS_LZ4 = True
except ImportError:
    _HAS_LZ4 = False


def _decompress(data: bytes) -> str:
    """解压 Mac 微信消息内容（zstd 或 lz4）"""
    if not data:
        return ""
    # Mac 新版用 zstd（魔数 0x28B52FFD）
    if _HAS_ZSTD and data[:4] == b'\x28\xb5\x2f\xfd':
        try:
            return zstd.decompress(data).decode('utf-8', errors='replace')
        except Exception:
            pass
    # 旧版用 lz4
    if _HAS_LZ4:
        try:
            return lz4.block.decompress(data, uncompressed_size=len(data) << 10).decode('utf-8', errors='replace').replace('\x00', '')
        except Exception:
            pass
    # 直接 utf-8
    try:
        return data.decode('utf-8', errors='replace')
    except Exception:
        return ""


def _strip_sender(text: str) -> str:
    """去掉 'wxid_xxx:\n' 前缀"""
    if '\n' in text:
        first, rest = text.split('\n', 1)
        if ':' in first and len(first) < 80:
            return rest
    return text


_APP_SUPPORT = Path(
    "~/Library/Containers/com.tencent.xinWeChat/Data/Library/Application Support/com.tencent.xinWeChat"
).expanduser()
_IMAGE_NAME_RE = re.compile(r"^(\d+)(\d{10})_\.pic(?:_hd|_thumb)?\.(jpg|jpeg|png|gif|webp)$", re.IGNORECASE)
_IMAGE_INDEX: dict[tuple[str, int, int], Path] | None = None
_IMAGE_BY_LOCAL_ID: dict[tuple[str, int], list[Path]] | None = None


def _message_temp_roots() -> list[Path]:
    """发现 Mac 微信本地媒体缓存目录。"""
    if not _APP_SUPPORT.exists():
        return []
    return sorted(path for path in _APP_SUPPORT.glob("*/*/Message/MessageTemp") if path.exists())


def _image_rank(path: Path) -> int:
    name = path.name.lower()
    if "_hd." in name:
        return 3
    if "_thumb." in name:
        return 1
    return 2


def _build_image_index() -> tuple[dict[tuple[str, int, int], Path], dict[tuple[str, int], list[Path]]]:
    """按 MessageTemp 文件名建立图片索引：会话 hash + local_id + create_time。"""
    exact: dict[tuple[str, int, int], Path] = {}
    by_local_id: dict[tuple[str, int], list[Path]] = {}

    for root in _message_temp_roots():
        for image_dir in root.glob("*/Image"):
            if not image_dir.is_dir():
                continue
            conv_hash = image_dir.parent.name
            for path in image_dir.iterdir():
                if not path.is_file():
                    continue
                match = _IMAGE_NAME_RE.match(path.name)
                if not match:
                    continue
                local_id = int(match.group(1))
                create_time = int(match.group(2))
                key = (conv_hash, local_id, create_time)
                previous = exact.get(key)
                if previous is None or _image_rank(path) > _image_rank(previous):
                    exact[key] = path
                by_local_id.setdefault((conv_hash, local_id), []).append(path)

    for paths in by_local_id.values():
        paths.sort(key=_image_rank, reverse=True)
    return exact, by_local_id


def _find_wechat_image(table_name: str, local_id: int, sort_seq: int, create_time: int = 0) -> Optional[Path]:
    """在微信 MessageTemp 里找图片文件"""
    global _IMAGE_INDEX, _IMAGE_BY_LOCAL_ID
    if _IMAGE_INDEX is None or _IMAGE_BY_LOCAL_ID is None:
        _IMAGE_INDEX, _IMAGE_BY_LOCAL_ID = _build_image_index()

    wxid_md5 = table_name[4:] if table_name.startswith('Msg_') else table_name

    for ts in filter(None, [create_time, sort_seq // 1000 if sort_seq > 10_000_000_000 else sort_seq]):
        path = _IMAGE_INDEX.get((wxid_md5, int(local_id), int(ts)))
        if path:
            return path

    # 不按 local_id 单独降级匹配，避免旧缓存命中错误图片。
    return None


def _img_to_base64(path: Path) -> str:
    import base64
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    data = base64.b64encode(path.read_bytes()).decode()
    return f'data:{mime};base64,{data}'


class MacHTMLExporter:
    def __init__(self, db_dir: str = "app/Database/MacMsg", 
                 contact_mapper=None):
        self.db_dir = Path(db_dir)
        self.contact_mapper = contact_mapper
        self.message_dir = self.db_dir / "message"
        self.media_resolver = MacMediaResolver(self.db_dir)
        self.media_output_dir = Path("data/export/html_media")
        
    def export_conversation(self, table_name: str, output_path: str, 
                           limit: Optional[int] = None):
        """导出单个会话到 HTML"""
        db_files = self._find_message_dbs(table_name)
        if not db_files:
            print(f"❌ 未找到表 {table_name}")
            return

        messages = []
        for db_file in db_files:
            conn = sqlite3.connect(str(db_file))
            cursor = conn.cursor()
            cursor.execute(f"""
                SELECT local_id, real_sender_id, local_type, create_time, message_content, sort_seq
                FROM {table_name}
            """)
            messages.extend(cursor.fetchall())
            conn.close()
        messages.sort(key=lambda row: (row[3] or 0, row[0] or 0))
        if limit:
            messages = messages[:limit]
        
        # 生成 HTML
        self._generate_html(messages, table_name, output_path)
        print(f"✅ 导出 {len(messages)} 条消息到 {output_path}")
    
    def export_all_conversations(self, output_dir: str = "data/html_export",
                                limit_per_chat: Optional[int] = None):
        """导出所有会话"""
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)
        
        # 扫描所有消息表
        tables = self._get_all_message_tables()
        print(f"📊 找到 {len(tables)} 个会话")
        
        # 构建表名到 wxid 的映射
        import hashlib
        table_to_wxid = {}
        if self.contact_mapper:
            all_wxids = list(self.contact_mapper.wxid_to_remark.keys()) + list(self.contact_mapper.chatroom_to_name.keys())
            for wxid in all_wxids:
                table_hash = hashlib.md5(wxid.encode()).hexdigest()
                table_name = f"Msg_{table_hash}"
                table_to_wxid[table_name] = wxid
        
        for i, (db_file, table_name) in enumerate(tables, 1):
            try:
                # 从表名反查 wxid
                wxid = table_to_wxid.get(table_name, table_name.replace('Msg_', ''))
                
                if self.contact_mapper:
                    if '@chatroom' in wxid:
                        display_name = self.contact_mapper.get_chatroom_name(wxid)
                    else:
                        display_name = self.contact_mapper.get_display_name(wxid)
                else:
                    display_name = wxid
                
                # 安全的文件名
                safe_name = "".join(c if c.isalnum() or c in (' ', '_', '-') else '_' for c in display_name)
                output_file = output / f"{safe_name}.html"
                
                # 如果文件名重复，添加 wxid 后缀
                if output_file.exists():
                    output_file = output / f"{safe_name}_{wxid[:8]}.html"
                
                self.export_conversation(table_name, str(output_file), limit_per_chat)
                
                if i % 10 == 0:
                    print(f"  进度: {i}/{len(tables)}")
            except Exception as e:
                print(f"  ⚠️  导出失败 {table_name}: {e}")
        
        print(f"\n✅ 全部导出完成: {output.absolute()}")
    
    def _find_message_dbs(self, table_name: str) -> List[Path]:
        """查找包含指定表的全部数据库分片"""
        db_files = []
        for db_file in sorted(self.message_dir.glob("message_*.db")):
            try:
                conn = sqlite3.connect(str(db_file))
                cursor = conn.cursor()
                cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,))
                if cursor.fetchone():
                    db_files.append(db_file)
                conn.close()
            except:
                pass
        return db_files
    
    def _get_all_message_tables(self) -> List[tuple]:
        """获取所有消息表"""
        tables = []
        seen = set()
        for db_file in sorted(self.message_dir.glob("message_*.db")):
            try:
                conn = sqlite3.connect(str(db_file))
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'")
                for row in cursor.fetchall():
                    if row[0] not in seen:
                        seen.add(row[0])
                        tables.append((db_file, row[0]))
                conn.close()
            except Exception as e:
                print(f"⚠️  读取 {db_file.name} 失败: {e}")
        return tables
    
    def _generate_html(self, messages: List[tuple], table_name: str, output_path: str):
        """生成 HTML 文件"""
        wxid = table_name.replace('Msg_', '')
        display_name = self.contact_mapper.get_display_name(wxid) if self.contact_mapper else wxid
        output_base = Path(output_path).with_suffix("")
        self.media_output_dir = output_base.parent / f"{output_base.name}_media"
        
        html_content = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{html.escape(display_name)} - 聊天记录</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            max-width: 800px;
            margin: 0 auto;
            padding: 20px;
            background: #f5f5f5;
        }}
        .header {{
            background: white;
            padding: 20px;
            border-radius: 8px;
            margin-bottom: 20px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        .message {{
            background: white;
            padding: 12px 16px;
            margin-bottom: 8px;
            border-radius: 8px;
            box-shadow: 0 1px 2px rgba(0,0,0,0.05);
        }}
        .message.sent {{
            background: #95ec69;
            margin-left: 60px;
        }}
        .message.received {{
            background: white;
            margin-right: 60px;
        }}
        .time {{
            color: #999;
            font-size: 12px;
            margin-bottom: 4px;
        }}
        .sender {{
            color: #576b95;
            font-weight: 500;
            font-size: 14px;
            margin-bottom: 4px;
        }}
        .content {{
            word-wrap: break-word;
            white-space: pre-wrap;
        }}
        .image {{
            max-width: 100%;
            border-radius: 4px;
            margin-top: 8px;
        }}
        .system {{
            text-align: center;
            color: #999;
            font-size: 12px;
            margin: 16px 0;
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>{html.escape(display_name)}</h1>
        <p>微信号: {html.escape(wxid)}</p>
        <p>消息数量: {len(messages)}</p>
        <p>导出时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
    </div>
    <div class="messages">
"""
        
        for msg in messages:
            local_id, real_sender_id, msg_type, create_time, content, sort_seq = msg

            is_sent = (real_sender_id == 0 or real_sender_id is None)
            msg_class = "sent" if is_sent else "received"

            time_str = datetime.fromtimestamp(create_time).strftime('%Y-%m-%d %H:%M:%S')

            content_html = self._parse_message_content(msg_type, content, table_name, local_id, sort_seq, create_time)
            
            html_content += f"""
    <div class="message {msg_class}">
        <div class="time">{time_str}</div>
        <div class="content">{content_html}</div>
    </div>
"""
        
        html_content += """
    </div>
</body>
</html>
"""
        
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(html_content)
    
    def _parse_message_content(self, msg_type: int, content, table_name: str = "", local_id: int = 0, sort_seq: int = 0, create_time: int = 0) -> str:
        """解析消息内容（支持 zstd/lz4 解压）"""
        parsed = parse_message(
            msg_type,
            content,
            table_name=table_name,
            local_id=local_id,
            create_time=create_time,
            sort_seq=sort_seq,
            resolver=self.media_resolver,
        )
        if parsed.type_name == "文本":
            return html.escape(parsed.text)
        if parsed.media_kind == "image":
            if parsed.media_path:
                try:
                    return f'<img src="{data_uri(Path(parsed.media_path))}" style="max-width:300px;border-radius:4px" alt="图片">'
                except Exception:
                    pass
            return f'<div style="color:#888;font-style:italic">{html.escape(parsed.text)}</div>'
        if parsed.media_kind == "voice":
            voice_data = self.media_resolver.get_voice_data(table_name, local_id, create_time)
            if voice_data:
                media_dir = self.media_output_dir / "voice"
                voice_path = self.media_resolver.write_voice(voice_data, media_dir, f"{local_id}_{create_time}.silk")
                return f'<div style="color:#888;font-style:italic">{html.escape(parsed.text)}</div><div style="font-size:11px;color:#999">语音文件: {html.escape(str(voice_path))}</div>'
            return f'<div style="color:#888;font-style:italic">{html.escape(parsed.text)}</div>'
        if parsed.media_kind == "video":
            if parsed.media_path:
                try:
                    src = data_uri(Path(parsed.media_path))
                    return f'<video src="{src}" controls style="max-width:320px;border-radius:4px"></video>'
                except Exception:
                    return f'<div style="color:#888;font-style:italic">[视频文件] {html.escape(parsed.media_path)}</div>'
            return '<div style="color:#888;font-style:italic">[视频消息]</div>'
        if parsed.media_kind == "emoji":
            if parsed.url:
                return f'<img src="{html.escape(parsed.url)}" style="max-width:120px" alt="[表情包]">'
            return '<div style="color:#888;font-style:italic">[表情包]</div>'
        if parsed.type_name == "分享/文件":
            if parsed.url:
                return (f'<div>📎 <a href="{html.escape(parsed.url)}" target="_blank">'
                        f'{html.escape(parsed.title or parsed.url)}</a></div>'
                        + (f'<div style="font-size:12px;color:#666">{html.escape(parsed.description)}</div>' if parsed.description else ''))
            return f'<div>📎 {html.escape(parsed.text)}</div>'
        if parsed.type_name == "系统消息":
            return f'<div class="system">{html.escape(parsed.text)}</div>'
        return f'<div style="color:#aaa;font-size:12px">{html.escape(parsed.text)}</div>'

        # 解压 bytes
        if isinstance(content, bytes) and content:
            content = _decompress(content)

        if not content:
            content = ""

        # 去掉 'wxid:\n' 前缀
        text = _strip_sender(content)

        if msg_type == 1:  # 文本
            return html.escape(text)

        elif msg_type == 3:  # 图片
            # 先尝试找本地图片文件
            if table_name and local_id:
                img_path = _find_wechat_image(table_name, local_id, sort_seq, create_time)
                if img_path:
                    try:
                        src = _img_to_base64(img_path)
                        return f'<img src="{src}" style="max-width:300px;border-radius:4px" alt="图片">'
                    except Exception:
                        pass
            # 降级：显示占位符 + md5
            try:
                root = ET.fromstring(text)
                img = root.find('img')
                if img is not None:
                    md5 = img.get('md5', '')
                    w = img.get('cdnthumbwidth', '')
                    h = img.get('cdnthumbheight', '')
                    size_info = f'{w}×{h}' if w and h else ''
                    return (f'<div style="color:#888;font-style:italic">[图片{" " + size_info if size_info else ""}]</div>'
                            + (f'<div style="font-size:11px;color:#bbb">md5: {html.escape(md5)}</div>' if md5 else ''))
            except Exception:
                pass
            return '<div style="color:#888;font-style:italic">[图片]</div>'

        elif msg_type == 34:  # 语音
            return '<div style="color:#888;font-style:italic">[语音消息]</div>'

        elif msg_type == 43:  # 视频
            return '<div style="color:#888;font-style:italic">[视频消息]</div>'

        elif msg_type == 47:  # 表情包
            try:
                root = ET.fromstring(text)
                emoji = root.find('.//emoji')
                if emoji is not None:
                    cdnurl = emoji.get('cdnurl', '')
                    if cdnurl:
                        return f'<img src="{html.escape(cdnurl)}" style="max-width:120px" alt="[表情包]">'
            except Exception:
                pass
            return '<div style="color:#888;font-style:italic">[表情包]</div>'

        elif msg_type == 49:  # 分享/文件/引用
            try:
                root = ET.fromstring(text)
                appmsg = root.find('appmsg')
                if appmsg is not None:
                    title = appmsg.findtext('title', '')
                    desc = appmsg.findtext('des', '')
                    url = appmsg.findtext('url', '')
                    app_type = appmsg.findtext('type', '')
                    if url:
                        return (f'<div>📎 <a href="{html.escape(url)}" target="_blank">'
                                f'{html.escape(title or url)}</a></div>'
                                + (f'<div style="font-size:12px;color:#666">{html.escape(desc)}</div>' if desc else ''))
                    return f'<div>📎 {html.escape(title or f"[分享 type={app_type}]")}</div>'
            except Exception:
                pass
            return f'<div style="color:#888;font-style:italic">[分享/文件]</div>'

        elif msg_type == 10000:  # 系统消息
            return f'<div class="system">{html.escape(text)}</div>'

        else:
            return f'<div style="color:#aaa;font-size:12px">[类型 {msg_type}]</div>'


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Mac 微信 HTML 导出')
    parser.add_argument('--db-dir', default='app/Database/MacMsg', help='数据库目录')
    parser.add_argument('--output', default='data/html_export', help='输出目录')
    parser.add_argument('--mapping', default='data/mac_contact_mapping.json', help='联系人映射文件')
    parser.add_argument('--limit', type=int, help='每个会话最多导出消息数')
    parser.add_argument('--table', help='只导出指定表（如 Msg_123456）')
    args = parser.parse_args()
    
    # 加载联系人映射
    from mac_contact_mapper import MacContactMapper
    mapper = None
    if Path(args.mapping).exists():
        mapper = MacContactMapper.load_mapping(args.mapping)
    
    exporter = MacHTMLExporter(args.db_dir, mapper)
    
    if args.table:
        output_file = Path(args.output) / f"{args.table}.html"
        exporter.export_conversation(args.table, str(output_file), args.limit)
    else:
        exporter.export_all_conversations(args.output, args.limit)


if __name__ == '__main__':
    main()
