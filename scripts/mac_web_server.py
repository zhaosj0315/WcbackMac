#!/usr/bin/env python3
"""
Mac 4.x FastAPI Web 服务
参考 PyWxDump remote_server.py，适配 Mac 4.x 数据库结构
启动: python3 scripts/mac_web_server.py
访问: http://127.0.0.1:5000
"""
import hashlib
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

try:
    import zstd
except ImportError:
    zstd = None

try:
    from fastapi import FastAPI, Query, Body
    from fastapi.responses import JSONResponse, FileResponse, Response
    from fastapi.middleware.cors import CORSMiddleware
    import uvicorn
except ImportError:
    print("请安装依赖: pip install fastapi uvicorn")
    sys.exit(1)

# ── 配置 ──────────────────────────────────────────────────────────────────────
DB_DIR = ROOT_DIR / "app" / "DataBase" / "MacMsg"
CONTACT_MAPPING_PATH = ROOT_DIR / "data" / "mac_contact_mapping.json"

app = FastAPI(title="Mac 微信数据查看器", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── 工具函数 ──────────────────────────────────────────────────────────────────

def _decode(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        if zstd and value[:4] == b"\x28\xb5\x2f\xfd":
            try:
                text = zstd.decompress(value).decode("utf-8", errors="replace")
                if "\n" in text:
                    first, rest = text.split("\n", 1)
                    if ":" in first and len(first) < 80:
                        return rest
                return text
            except Exception:
                pass
        return value.decode("utf-8", errors="replace")
    return str(value)


def _ts(ts: Any) -> str:
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ""


def _parse_content(msg_type: int, text: str) -> str:
    """把 XML 消息解析成可读文本"""
    import xml.etree.ElementTree as ET
    text = text.strip()
    if msg_type == 1:
        return text
    if msg_type == 3:
        try:
            root = ET.fromstring(text)
            img = root.find("img")
            if img is not None:
                w, h = img.get("cdnthumbwidth",""), img.get("cdnthumbheight","")
                return f"[图片 {w}×{h}]" if w and h else "[图片]"
        except Exception:
            pass
        return "[图片]"
    if msg_type == 34:
        return "[语音消息]"
    if msg_type == 43:
        return "[视频消息]"
    if msg_type == 47:
        return "[表情包]"
    if msg_type == 49:
        try:
            root = ET.fromstring(text)
            appmsg = root.find("appmsg")
            if appmsg is not None:
                title = (appmsg.findtext("title") or "").strip()
                app_type = appmsg.findtext("type") or ""
                url = (appmsg.findtext("url") or "").strip()
                type_names = {"1":"文字","2":"图片","3":"音乐","4":"视频","5":"链接",
                              "6":"文件","8":"表情","19":"合并转发","33":"小程序",
                              "36":"小程序","43":"视频号","49":"文字","57":"引用消息",
                              "2000":"转账","2003":"红包"}
                kind = type_names.get(app_type, f"type={app_type}")
                if title:
                    return f"[{kind}] {title}" + (f"\n{url}" if url else "")
                return f"[{kind}]"
        except Exception:
            pass
        return "[分享/文件]"
    if msg_type == 50:
        return "[语音/视频通话]"
    if msg_type == 10000:
        # 系统消息通常是纯文本
        return text if text and not text.startswith("<") else "[系统消息]"
    # 其他类型：尝试提取 XML title
    if text.startswith("<"):
        try:
            root = ET.fromstring(text)
            for tag in ("title", "des", "content"):
                val = root.findtext(f".//{tag}")
                if val and val.strip():
                    return val.strip()[:200]
        except Exception:
            pass
        return f"[消息类型 {msg_type}]"
    return text[:500]


def _contact_db() -> Path:
    return DB_DIR / "contact" / "contact.db"


MY_WXID = ""  # 自动从 xwechat_files 目录名检测，也可通过 --my-wxid 参数覆盖

def _detect_my_wxid() -> str:
    xwechat = Path.home() / "Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files"
    candidates = sorted(xwechat.glob("wxid_*"), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    if candidates:
        name = candidates[0].name
        return name.rsplit("_", 1)[0] if "_" in name else name
    return ""


def _load_shard_maps() -> tuple[dict[str, dict[int, str]], dict[str, int]]:
    """返回 (shard_map, shard_my_rowid)，每个分片独立的 rowid->name 映射"""
    wxid_to_name: dict[str, str] = {}
    contact_db = _contact_db()
    if contact_db.exists():
        try:
            conn = sqlite3.connect(contact_db)
            for username, remark, nick_name in conn.execute(
                "select username, remark, nick_name from contact"
            ):
                wxid_to_name[username] = remark or nick_name or username
            conn.close()
        except Exception:
            pass

    shard_map: dict[str, dict[int, str]] = {}
    shard_my_rowid: dict[str, int] = {}
    msg_dir = _message_dir()
    if not msg_dir.exists():
        return shard_map, shard_my_rowid

    for db_file in sorted(msg_dir.glob("message_*.db")):
        if "fts" in db_file.name:
            continue
        stem = db_file.stem
        rowid_map: dict[int, str] = {}
        my_rowid = -1
        try:
            conn = sqlite3.connect(db_file)
            has = conn.execute(
                "select name from sqlite_master where type='table' and name='Name2Id'"
            ).fetchone()
            if has:
                for rowid, user_name in conn.execute("select rowid, user_name from Name2Id"):
                    if user_name:
                        rowid_map[int(rowid)] = wxid_to_name.get(user_name, user_name)
                        if user_name == (MY_WXID or _detect_my_wxid()):
                            my_rowid = int(rowid)
            conn.close()
        except Exception:
            continue
        shard_map[stem] = rowid_map
        if my_rowid >= 0:
            shard_my_rowid[stem] = my_rowid

    return shard_map, shard_my_rowid


def _message_dir() -> Path:
    return DB_DIR / "message"


def _load_contact_map() -> dict[str, str]:
    """wxid -> 显示名称"""
    if CONTACT_MAPPING_PATH.exists():
        raw = json.loads(CONTACT_MAPPING_PATH.read_text(encoding="utf-8"))
        result = {}
        if "contacts" in raw:
            result.update(raw["contacts"])
        if "chatrooms" in raw:
            result.update(raw["chatrooms"])
        return result if result else raw
    return {}


def _table_to_wxid(contact_map: dict[str, str]) -> dict[str, str]:
    """Msg_{md5} -> wxid"""
    result = {}
    for wxid in contact_map:
        h = hashlib.md5(wxid.encode()).hexdigest()
        result[f"Msg_{h}"] = wxid
    return result


def _ok(data: Any) -> JSONResponse:
    return JSONResponse({"code": 0, "data": data})


def _err(msg: str, code: int = 1001) -> JSONResponse:
    return JSONResponse({"code": code, "msg": msg})


# ── 联系人 API ────────────────────────────────────────────────────────────────

@app.get("/api/contacts")
def get_contacts(q: str = Query(""), word: str = Query(""), limit: int = Query(100), offset: int = Query(0)):
    """获取联系人列表，支持关键字搜索和分页"""
    keyword = q or word
    db = _contact_db()
    if not db.exists():
        return _err("contact.db 不存在")
    conn = sqlite3.connect(db)
    cur = conn.cursor()
    if keyword:
        pattern = f"%{keyword}%"
        where = "delete_flag=0 and (username like ? or nick_name like ? or remark like ? or alias like ?)"
        params_count = (pattern, pattern, pattern, pattern)
        params_page = (pattern, pattern, pattern, pattern, limit, offset)
        cur.execute(f"select count(*) from contact where {where}", params_count)
        total = cur.fetchone()[0]
        cur.execute(
            f"select username, nick_name, remark, alias, big_head_url, small_head_url from contact "
            f"where {where} order by nick_name limit ? offset ?", params_page)
    else:
        cur.execute("select count(*) from contact where delete_flag=0")
        total = cur.fetchone()[0]
        cur.execute(
            "select username, nick_name, remark, alias, big_head_url, small_head_url from contact "
            "where delete_flag=0 order by nick_name limit ? offset ?", (limit, offset))
    rows = cur.fetchall()
    conn.close()
    contacts = [
        {"username": r[0], "nick_name": r[1], "remark": r[2], "alias": r[3],
         "display_name": r[2] or r[1] or r[0]}
        for r in rows
    ]
    return _ok({"total": total, "contacts": contacts})


@app.get("/api/sessions")
def get_sessions(limit: int = Query(100)):
    """获取会话列表（按消息数量排序）"""
    msg_dir = _message_dir()
    if not msg_dir.exists():
        return _err("message 目录不存在")

    contact_map = _load_contact_map()
    t2w = _table_to_wxid(contact_map)

    table_counts: dict[str, int] = {}
    table_latest: dict[str, int] = {}

    for db_file in sorted(msg_dir.glob("message_*.db")):
        if "fts" in db_file.name:
            continue
        try:
            conn = sqlite3.connect(db_file)
            cur = conn.cursor()
            cur.execute("select name from sqlite_master where type='table' and name like 'Msg_%'")
            for (table,) in cur.fetchall():
                cur.execute(f'select count(*), max(create_time) from "{table}"')
                row = cur.fetchone()
                cnt, latest = row if row else (0, 0)
                table_counts[table] = table_counts.get(table, 0) + (cnt or 0)
                table_latest[table] = max(table_latest.get(table, 0), latest or 0)
            conn.close()
        except sqlite3.Error:
            continue

    sessions = []
    for table, count in sorted(table_counts.items(), key=lambda x: -x[1])[:limit]:
        wxid = t2w.get(table, "")
        display = contact_map.get(wxid, wxid or table.replace("Msg_", "")[:16])
        sessions.append({
            "table": table,
            "wxid": wxid,
            "display_name": display,
            "message_count": count,
            "last_time": _ts(table_latest.get(table, 0)),
        })
    return _ok(sessions)


# ── 消息 API ──────────────────────────────────────────────────────────────────

MSG_TYPE_NAMES = {
    1: "文本", 3: "图片", 34: "语音", 43: "视频", 47: "表情包",
    49: "分享/文件", 50: "语音通话", 10000: "系统消息",
}


def _find_dbs_for_table(table: str) -> list[Path]:
    msg_dir = _message_dir()
    result = []
    for db_file in sorted(msg_dir.glob("message_*.db")):
        if "fts" in db_file.name:
            continue
        try:
            conn = sqlite3.connect(db_file)
            cur = conn.cursor()
            cur.execute(
                "select name from sqlite_master where type='table' and name=?", (table,)
            )
            if cur.fetchone():
                result.append(db_file)
            conn.close()
        except sqlite3.Error:
            continue
    return result


@app.get("/api/messages")
def get_messages(
    table: str = Query(..., description="Msg_xxx 表名"),
    start: int = Query(0),
    limit: int = Query(50, description="每页条数，0=全部"),
    type_filter: str = Query("", description="消息类型过滤，逗号分隔，如 3,34,43"),
    order: str = Query("asc", description="asc 正序 / desc 倒序"),
):
    """获取指定会话的消息列表（跨分片聚合）"""
    db_files = _find_dbs_for_table(table)
    if not db_files:
        return _err(f"未找到表 {table}")

    # 解析类型过滤
    filter_types: set[int] = set()
    if type_filter:
        for t in type_filter.split(","):
            try: filter_types.add(int(t.strip()))
            except ValueError: pass

    rows = []
    for db_file in db_files:
        conn = sqlite3.connect(db_file)
        cur = conn.cursor()
        cur.execute(
            f'select local_id, local_type, create_time, real_sender_id, message_content '
            f'from "{table}" order by create_time asc'
        )
        shard = db_file.stem
        rows.extend((*r, shard) for r in cur.fetchall())
        conn.close()

    rows.sort(key=lambda r: (r[2] or 0, r[0] or 0), reverse=(order == "desc"))

    # 类型过滤（在排序后过滤，保持分页语义正确）
    if filter_types:
        rows = [r for r in rows if (r[1] & 0xFFFFFFFF if r[1] and r[1] > 0xFFFFFFFF else (r[1] or 0)) in filter_types]
    total = len(rows)
    page = rows[start:] if limit == 0 else rows[start: start + limit]

    shard_map, shard_my_rowid = _load_shard_maps()

    msgs = []
    for local_id, msg_type, create_time, sender_id, content, shard in page:
        base_type = msg_type & 0xFFFFFFFF if msg_type and msg_type > 0xFFFFFFFF else (msg_type or 0)
        text = _decode(content)
        readable = _parse_content(base_type, text)
        is_me = sender_id == 0 or sender_id == shard_my_rowid.get(shard, -1)
        sender_name = "我" if is_me else shard_map.get(shard, {}).get(sender_id, str(sender_id))
        # 提取文件名（type=49 subtype=6）
        file_name = None
        if base_type == 49:
            try:
                import xml.etree.ElementTree as _ET
                _root = _ET.fromstring(text.strip())
                _appmsg = _root.find("appmsg")
                if _appmsg is not None and _appmsg.findtext("type") == "6":
                    file_name = _appmsg.findtext("title") or None
            except Exception:
                pass
        msg = {
            "local_id": local_id,
            "type": base_type,
            "type_name": MSG_TYPE_NAMES.get(base_type, f"其他({base_type})"),
            "create_time": create_time,
            "datetime": _ts(create_time),
            "is_sender": is_me,
            "sender_name": sender_name,
            "content": readable,
        }
        if file_name:
            msg["file_name"] = file_name
        msgs.append(msg)
    return _ok({"total": total, "messages": msgs})


# ── 媒体 API ──────────────────────────────────────────────────────────────────

from mac_message_utils import MacMediaResolver  # noqa: E402

_resolver: MacMediaResolver | None = None


def _get_resolver() -> MacMediaResolver:
    global _resolver
    if _resolver is None:
        _resolver = MacMediaResolver(DB_DIR)
    return _resolver


@app.get("/api/image")
def get_image(table: str = Query(...), local_id: int = Query(...), create_time: int = Query(...)):
    """获取图片（本地缓存）"""
    path = _get_resolver().find_image(table, local_id, create_time)
    if path and path.exists():
        return FileResponse(str(path))
    return _err("图片缓存不存在", 4004)


@app.get("/api/video")
def get_video(table: str = Query(...), local_id: int = Query(...), create_time: int = Query(...)):
    """获取视频（本地缓存）"""
    path = _get_resolver().find_video(table, local_id, create_time)
    if path and path.exists():
        import mimetypes
        mt = mimetypes.guess_type(str(path))[0] or "video/mp4"
        return FileResponse(str(path), media_type=mt)
    return _err("视频缓存不存在", 4004)


@app.get("/api/file")
def get_file(table: str = Query(...), filename: str = Query(...)):
    """获取文件附件（MessageTemp/File 目录）"""
    conv_hash = table[4:] if table.startswith("Msg_") else table
    resolver = _get_resolver()
    for mt_root in resolver.message_temp_roots():
        candidate = mt_root / conv_hash / "File" / filename
        if candidate.exists():
            import mimetypes
            mt = mimetypes.guess_type(str(candidate))[0] or "application/octet-stream"
            return FileResponse(str(candidate), media_type=mt, filename=filename)
    # fallback: msg/file 目录按文件名搜索
    file_base = resolver.attach_base.parent / "file"
    if file_base.exists():
        for f in file_base.rglob(filename):
            import mimetypes
            mt = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
            return FileResponse(str(f), media_type=mt, filename=filename)
    return _err("文件不存在", 4004)


@app.get("/api/voice")
def get_voice(table: str = Query(...), local_id: int = Query(...), create_time: int = Query(...)):
    """获取语音（优先返回 wav，降级返回 silk 原始数据）"""
    data = _get_resolver().get_voice_data(table, local_id, create_time)
    if not data:
        return _err("语音数据不存在", 4004)
    from mac_message_utils import silk_to_wav
    wav = silk_to_wav(data)
    if wav:
        return Response(content=wav, media_type="audio/wav")
    return Response(content=data, media_type="application/octet-stream")


# ── 朋友圈 API ────────────────────────────────────────────────────────────────

@app.get("/api/sns")
def get_sns(limit: int = Query(100), offset: int = Query(0)):
    """获取朋友圈列表"""
    db = DB_DIR / "sns" / "sns.db"
    if not db.exists():
        return _err("sns.db 不存在")
    conn = sqlite3.connect(db)
    cur = conn.cursor()
    cur.execute(
        "select tid, user_name, content from SnsTimeLine order by tid desc limit ? offset ?",
        (limit, offset),
    )
    rows = cur.fetchall()
    cur.execute("select count(*) from SnsTimeLine")
    total = cur.fetchone()[0]
    conn.close()

    import xml.etree.ElementTree as ET
    results = []
    for tid, user_name, content in rows:
        text = _decode(content)
        desc = ""
        try:
            root = ET.fromstring(text)
            obj = root.find("TimelineObject") or root
            desc = (obj.findtext("contentDesc") or "").strip()
        except ET.ParseError:
            pass
        results.append({"tid": tid, "user_name": user_name, "desc": desc, "xml": text[:300]})
    return _ok({"total": total, "items": results})


# ── 收藏 API ──────────────────────────────────────────────────────────────────

@app.get("/api/favorites")
def get_favorites(limit: int = Query(100), offset: int = Query(0)):
    """获取收藏列表"""
    db = DB_DIR / "favorite" / "favorite.db"
    if not db.exists():
        return _err("favorite.db 不存在")
    conn = sqlite3.connect(db)
    cur = conn.cursor()
    cur.execute(
        "select local_id, type, update_time, content from fav_db_item order by update_time desc limit ? offset ?",
        (limit, offset),
    )
    rows = cur.fetchall()
    cur.execute("select count(*) from fav_db_item")
    total = cur.fetchone()[0]
    conn.close()

    FAV_TYPES = {1:"文本",2:"图片",3:"语音",4:"视频",5:"链接",8:"文件",14:"聊天记录",18:"笔记"}
    results = []
    for local_id, fav_type, update_time, content in rows:
        text = _decode(content)
        results.append({
            "local_id": local_id,
            "type": fav_type,
            "type_name": FAV_TYPES.get(fav_type, f"其他({fav_type})"),
            "update_time": _ts(update_time),
            "xml": text[:300],
        })
    return _ok({"total": total, "items": results})


# ── 统计 API ──────────────────────────────────────────────────────────────────

@app.get("/api/stats")
def get_stats():
    """基础统计数据"""
    msg_dir = _message_dir()
    total = 0
    if msg_dir.exists():
        for db_file in msg_dir.glob("message_*.db"):
            if "fts" in db_file.name:
                continue
            try:
                conn = sqlite3.connect(db_file)
                cur = conn.cursor()
                cur.execute("select name from sqlite_master where type='table' and name like 'Msg_%'")
                for (t,) in cur.fetchall():
                    cur.execute(f'select count(*) from "{t}"')
                    total += cur.fetchone()[0]
                conn.close()
            except sqlite3.Error:
                continue

    contact_count = 0
    contact_db = _contact_db()
    if contact_db.exists():
        conn = sqlite3.connect(contact_db)
        contact_count = conn.execute("select count(*) from contact where delete_flag=0").fetchone()[0]
        conn.close()

    sns_count = 0
    sns_db = DB_DIR / "sns" / "sns.db"
    if sns_db.exists():
        conn = sqlite3.connect(sns_db)
        sns_count = conn.execute("select count(*) from SnsTimeLine").fetchone()[0]
        conn.close()

    fav_count = 0
    fav_db = DB_DIR / "favorite" / "favorite.db"
    if fav_db.exists():
        conn = sqlite3.connect(fav_db)
        fav_count = conn.execute("select count(*) from fav_db_item").fetchone()[0]
        conn.close()

    return _ok({
        "total_messages": total,
        "total_contacts": contact_count,
        "total_sns": sns_count,
        "total_favorites": fav_count,
    })


# ── 主页 HTML ─────────────────────────────────────────────────────────────────

_INDEX_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>Mac 微信数据查看器</title>
<style>
*{box-sizing:border-box}
body{font-family:PingFang SC,Arial,sans-serif;margin:0;background:#f0f2f5;height:100vh;display:flex;flex-direction:column}
.header{background:#07c160;color:#fff;padding:12px 20px;font-size:17px;font-weight:bold;flex-shrink:0}
.main{display:flex;flex:1;overflow:hidden}
.sidebar{width:280px;background:#fff;border-right:1px solid #e8e8e8;display:flex;flex-direction:column;flex-shrink:0}
.search-box{padding:10px;border-bottom:1px solid #f0f0f0}
.search-box input{width:100%;padding:7px 10px;border:1px solid #d9d9d9;border-radius:6px;font-size:13px;outline:none}
.session-list{flex:1;overflow-y:auto}
.session-item{padding:12px 14px;cursor:pointer;border-bottom:1px solid #f5f5f5;transition:background .15s}
.session-item:hover,.session-item.active{background:#f0faf4}
.session-item .name{font-size:14px;font-weight:500;color:#333;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.session-item .meta{font-size:12px;color:#999;margin-top:3px}
.chat-area{flex:1;display:flex;flex-direction:column;overflow:hidden}
.chat-header{padding:10px 20px;border-bottom:1px solid #e8e8e8;background:#fff;font-weight:bold;font-size:15px;flex-shrink:0;display:flex;align-items:center;gap:10px}
.chat-header .title{flex:1}
.toolbar{display:flex;gap:6px;align-items:center;flex-wrap:wrap}
.toolbar select,.toolbar button{padding:4px 10px;border:1px solid #d9d9d9;border-radius:5px;background:#fff;cursor:pointer;font-size:12px;color:#555}
.toolbar select:focus,.toolbar button:hover{border-color:#07c160;color:#07c160;outline:none}
.toolbar button.active{background:#07c160;color:#fff;border-color:#07c160}
.msg-list{flex:1;overflow-y:auto;padding:16px 20px;display:flex;flex-direction:column;gap:10px}
.msg{display:flex;gap:10px;max-width:75%}
.msg.sent{align-self:flex-end;flex-direction:row-reverse}
.bubble{padding:9px 13px;border-radius:10px;font-size:14px;line-height:1.5;word-break:break-word}
.msg.recv .bubble{background:#fff;box-shadow:0 1px 3px rgba(0,0,0,.08)}
.msg.sent .bubble{background:#95ec69}
.msg .time{font-size:11px;color:#bbb;align-self:flex-end;white-space:nowrap}
.msg img{max-width:200px;max-height:200px;border-radius:6px;cursor:pointer;display:block}
.msg audio{max-width:220px}
.msg .sys{color:#999;font-size:12px;text-align:center;width:100%;padding:4px 0}
.msg .name{font-size:11px;color:#999;margin-bottom:2px}
.pagination{padding:10px 20px;border-top:1px solid #f0f0f0;display:flex;gap:8px;align-items:center;background:#fff;flex-shrink:0;flex-wrap:wrap}
.btn{padding:6px 14px;border:1px solid #d9d9d9;border-radius:5px;background:#fff;cursor:pointer;font-size:13px}
.btn:hover{border-color:#07c160;color:#07c160}
.btn:disabled{opacity:.4;cursor:default}
.page-info{font-size:13px;color:#666}
.stats-bar{display:flex;gap:0;background:#fff;border-bottom:1px solid #e8e8e8;flex-shrink:0}
.stat{text-align:center;flex:1;padding:10px 0;cursor:pointer;border-right:1px solid #f0f0f0;transition:background .15s}
.stat:last-child{border-right:none}
.stat:hover{background:#f6fff9}
.stat .n{font-size:20px;font-weight:bold;color:#07c160}
.stat .l{font-size:11px;color:#999}
/* 面板覆盖层 */
.panel{display:none;position:fixed;inset:0;background:rgba(0,0,0,.45);z-index:100;align-items:center;justify-content:center}
.panel.open{display:flex}
.panel-box{background:#fff;border-radius:10px;width:700px;max-width:95vw;max-height:85vh;display:flex;flex-direction:column;overflow:hidden}
.panel-head{padding:14px 20px;border-bottom:1px solid #f0f0f0;font-weight:bold;font-size:15px;display:flex;justify-content:space-between;align-items:center}
.panel-body{flex:1;overflow-y:auto;padding:16px 20px}
.panel-close{cursor:pointer;font-size:20px;color:#999;line-height:1}
.panel-close:hover{color:#333}
.contact-item{padding:8px 0;border-bottom:1px solid #f5f5f5;font-size:13px;display:flex;gap:10px}
.contact-item .cname{font-weight:500;min-width:120px}
.contact-item .cwxid{color:#999;font-size:12px}
.sns-item{padding:10px 0;border-bottom:1px solid #f5f5f5}
.sns-item .smeta{font-size:12px;color:#999;margin-bottom:4px}
.sns-item .sdesc{font-size:14px;color:#333}
.panel-footer{padding:10px 20px;border-top:1px solid #f0f0f0;display:flex;gap:8px;align-items:center;background:#fafafa}
</style></head>
<body>
<div class="header">🍃 Mac 微信数据查看器 &nbsp;<small style="font-weight:normal;font-size:13px"><a href="/docs" style="color:#d4f5e2" target="_blank">API 文档</a></small></div>
<div class="stats-bar" id="stats-bar">加载中...</div>
<div class="main">
  <div class="sidebar">
    <div class="search-box">
      <input id="search" placeholder="搜索联系人..." oninput="onSearch(this.value)">
    </div>
    <div class="session-list" id="session-list">加载中...</div>
  </div>
  <div class="chat-area">
    <div class="chat-header">
      <span class="title" id="chat-title">请选择会话</span>
      <div class="toolbar" id="toolbar" style="display:none">
        <select id="type-filter" onchange="onFilterChange()">
          <option value="">全部类型</option>
          <option value="1">文字</option>
          <option value="3">图片</option>
          <option value="34">语音</option>
          <option value="43">视频</option>
          <option value="47">表情</option>
          <option value="49">文件/链接</option>
          <option value="10000">系统消息</option>
        </select>
        <button id="btn-order" onclick="toggleOrder()" title="切换排序">⬆ 正序</button>
      </div>
    </div>
    <div class="msg-list" id="msg-list"></div>
    <div class="pagination" id="pagination" style="display:none">
      <button class="btn" id="btn-prev" onclick="changePage(-1)" disabled>上一页</button>
      <span class="page-info" id="page-info"></span>
      <button class="btn" id="btn-next" onclick="changePage(1)">下一页</button>
      <select id="page-size" onchange="changePageSize(this.value)" style="margin-left:12px;padding:5px 8px;border:1px solid #d9d9d9;border-radius:5px;font-size:13px">
        <option value="50">50条/页</option>
        <option value="100">100条/页</option>
        <option value="200">200条/页</option>
        <option value="500">500条/页</option>
        <option value="0">全部</option>
      </select>
    </div>
  </div>
</div>

<!-- 联系人面板 -->
<div class="panel" id="panel-contacts" onclick="if(event.target===this)closePanel('contacts')">
  <div class="panel-box">
    <div class="panel-head">
      <span>联系人列表</span>
      <span class="panel-close" onclick="closePanel('contacts')">×</span>
    </div>
    <div class="panel-body">
      <input id="contact-search" placeholder="搜索..." oninput="loadContacts(1)" style="width:100%;padding:7px 10px;border:1px solid #d9d9d9;border-radius:6px;font-size:13px;margin-bottom:12px;outline:none">
      <div id="contact-list"></div>
    </div>
    <div class="panel-footer">
      <button class="btn" id="contact-prev" onclick="loadContacts(-1)" disabled>上一页</button>
      <span id="contact-info" style="font-size:13px;color:#666"></span>
      <button class="btn" id="contact-next" onclick="loadContacts(1)">下一页</button>
    </div>
  </div>
</div>

<!-- 朋友圈面板 -->
<div class="panel" id="panel-sns" onclick="if(event.target===this)closePanel('sns')">
  <div class="panel-box">
    <div class="panel-head">
      <span>朋友圈</span>
      <span class="panel-close" onclick="closePanel('sns')">×</span>
    </div>
    <div class="panel-body" id="sns-list"></div>
    <div class="panel-footer">
      <button class="btn" id="sns-prev" onclick="loadSns(-1)" disabled>上一页</button>
      <span id="sns-info" style="font-size:13px;color:#666"></span>
      <button class="btn" id="sns-next" onclick="loadSns(1)">下一页</button>
    </div>
  </div>
</div>
<script>
let PAGE=50, curTable='', curPage=0, curTotal=0, allSessions=[];
let curOrder='asc', curFilter='';
let contactPage=0, contactTotal=0;
let snsPage=0, snsTotal=0;
const SNS_PAGE=50, CONTACT_PAGE=100;

// 统计栏（可点击）
fetch('/api/stats').then(r=>r.json()).then(d=>{
  const s=d.data;
  const labels=['总消息数','联系人','朋友圈','收藏'];
  const vals=[s.total_messages,s.total_contacts,s.total_sns,s.total_favorites];
  const actions=[null,'contacts','sns',null];
  document.getElementById('stats-bar').innerHTML=labels.map((l,i)=>{
    const onclick=actions[i]?`onclick="openPanel('${actions[i]}')"`:'' ;
    return `<div class="stat" ${onclick}><div class="n">${(vals[i]||0).toLocaleString()}</div><div class="l">${l}</div></div>`;
  }).join('');
});

// 会话列表（加载全部，前端搜索）
fetch('/api/sessions?limit=2000').then(r=>r.json()).then(d=>{
  allSessions=d.data||[];
  renderSessions(allSessions);
});

function renderSessions(list){
  document.getElementById('session-list').innerHTML=list.map(s=>
    `<div class="session-item" data-table="${escHtml(s.table)}" data-name="${escHtml(s.display_name)}">
      <div class="name">${escHtml(s.display_name)}</div>
      <div class="meta">${s.message_count.toLocaleString()} 条 · ${s.last_time||''}</div>
    </div>`
  ).join('')||'<div style="padding:20px;color:#999;text-align:center">无会话</div>';
}

document.getElementById('session-list').addEventListener('click',function(e){
  const item=e.target.closest('.session-item');
  if(!item)return;
  openSession(item.dataset.table,item.dataset.name);
});

function onSearch(q){
  if(!q){renderSessions(allSessions);return;}
  q=q.toLowerCase();
  renderSessions(allSessions.filter(s=>s.display_name.toLowerCase().includes(q)||(s.wxid||'').toLowerCase().includes(q)));
}

function openSession(table,name){
  curTable=table; curPage=0; curFilter=''; curOrder='asc';
  document.getElementById('chat-title').textContent=name;
  document.getElementById('toolbar').style.display='flex';
  document.getElementById('type-filter').value='';
  document.getElementById('btn-order').textContent='⬆ 正序';
  document.getElementById('btn-order').classList.remove('active');
  document.querySelectorAll('.session-item').forEach(el=>el.classList.toggle('active',el.dataset.table===table));
  loadMessages();
}

function onFilterChange(){
  curFilter=document.getElementById('type-filter').value;
  curPage=0; loadMessages();
}

function toggleOrder(){
  curOrder=curOrder==='asc'?'desc':'asc';
  curPage=0;
  const btn=document.getElementById('btn-order');
  btn.textContent=curOrder==='asc'?'⬆ 正序':'⬇ 倒序';
  btn.classList.toggle('active',curOrder==='desc');
  loadMessages();
}

function changePage(delta){curPage+=delta;loadMessages();}
function changePageSize(val){PAGE=parseInt(val);curPage=0;loadMessages();}

function loadMessages(){
  if(!curTable)return;
  const start=PAGE===0?0:curPage*PAGE;
  let url=`/api/messages?table=${encodeURIComponent(curTable)}&start=${start}&limit=${PAGE}&order=${curOrder}`;
  if(curFilter)url+=`&type_filter=${curFilter}`;
  fetch(url).then(r=>r.json()).then(d=>{
    curTotal=d.data.total;
    renderMessages(d.data.messages);
    const pages=PAGE===0?1:Math.ceil(curTotal/PAGE);
    document.getElementById('pagination').style.display='flex';
    const info=PAGE===0?`全部 ${curTotal.toLocaleString()} 条`:`第 ${curPage+1}/${pages} 页（共 ${curTotal.toLocaleString()} 条）`;
    document.getElementById('page-info').textContent=info;
    document.getElementById('btn-prev').disabled=curPage===0||PAGE===0;
    document.getElementById('btn-next').disabled=PAGE===0||(curPage+1)>=pages;
  });
}

function renderMessages(msgs){
  const el=document.getElementById('msg-list');
  el.innerHTML=msgs.map(m=>{
    if(m.type===10000)return `<div class="msg"><div class="sys">${escHtml(m.content)}</div></div>`;
    const cls=m.is_sender?'sent':'recv';
    let body='';
    if(m.type===3){
      body=`<img src="/api/image?table=${encodeURIComponent(curTable)}&local_id=${m.local_id}&create_time=${m.create_time}" onerror="this.outerHTML='<span style=color:#999;font-size:12px>[图片]</span>'" alt="图片">`;
    }else if(m.type===34){
      body=`<audio controls src="/api/voice?table=${encodeURIComponent(curTable)}&local_id=${m.local_id}&create_time=${m.create_time}"></audio>`;
    }else if(m.type===43){
      body=`<video controls src="/api/video?table=${encodeURIComponent(curTable)}&local_id=${m.local_id}&create_time=${m.create_time}" style="max-width:280px;border-radius:6px" onerror="this.outerHTML='<span style=color:#999;font-size:12px>[视频不可用]</span>'"></video>`;
    }else if(m.type===49&&m.file_name){
      body=`<a href="/api/file?table=${encodeURIComponent(curTable)}&filename=${encodeURIComponent(m.file_name)}" target="_blank" style="display:flex;align-items:center;gap:6px;text-decoration:none;color:#333">
        <span style="font-size:22px">📎</span>
        <span style="font-size:13px;text-decoration:underline">${escHtml(m.file_name)}</span>
      </a>`;
    }else{
      body=escHtml(m.content);
    }
    const nameHtml=m.is_sender?'':`<div class="name">${escHtml(m.sender_name||'')}</div>`;
    return `<div class="msg ${cls}"><div>${nameHtml}<div class="bubble">${body}</div></div><div class="time">${m.datetime.slice(11)}</div></div>`;
  }).join('');
  if(curOrder==='asc')el.scrollTop=el.scrollHeight;
}

// ── 面板 ──
function openPanel(name){
  document.getElementById('panel-'+name).classList.add('open');
  if(name==='contacts'){contactPage=0;loadContacts(0);}
  if(name==='sns'){snsPage=0;loadSns(0);}
}
function closePanel(name){document.getElementById('panel-'+name).classList.remove('open');}

function loadContacts(delta){
  contactPage+=delta;
  const q=document.getElementById('contact-search').value;
  fetch(`/api/contacts?limit=${CONTACT_PAGE}&offset=${contactPage*CONTACT_PAGE}&q=${encodeURIComponent(q)}`)
    .then(r=>r.json()).then(d=>{
      contactTotal=d.data.total;
      const pages=Math.ceil(contactTotal/CONTACT_PAGE);
      document.getElementById('contact-info').textContent=`第 ${contactPage+1}/${pages} 页（共 ${contactTotal.toLocaleString()} 人）`;
      document.getElementById('contact-prev').disabled=contactPage===0;
      document.getElementById('contact-next').disabled=(contactPage+1)>=pages;
      document.getElementById('contact-list').innerHTML=(d.data.contacts||[]).map(c=>
        `<div class="contact-item"><span class="cname">${escHtml(c.display_name||c.nick_name||c.username)}</span><span class="cwxid">${escHtml(c.username)}</span></div>`
      ).join('')||'<div style="color:#999;padding:10px">无结果</div>';
    });
}

function loadSns(delta){
  snsPage+=delta;
  fetch(`/api/sns?limit=${SNS_PAGE}&offset=${snsPage*SNS_PAGE}`)
    .then(r=>r.json()).then(d=>{
      snsTotal=d.data.total;
      const pages=Math.ceil(snsTotal/SNS_PAGE);
      document.getElementById('sns-info').textContent=`第 ${snsPage+1}/${pages} 页（共 ${snsTotal.toLocaleString()} 条）`;
      document.getElementById('sns-prev').disabled=snsPage===0;
      document.getElementById('sns-next').disabled=(snsPage+1)>=pages;
      document.getElementById('sns-list').innerHTML=(d.data.items||[]).map(s=>
        `<div class="sns-item"><div class="smeta">${escHtml(s.user_name)} · ${s.tid||''}</div><div class="sdesc">${escHtml(s.desc||'[无文字]')}</div></div>`
      ).join('')||'<div style="color:#999;padding:10px">暂无数据</div>';
    });
}

function escHtml(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
</script>
</body></html>"""


@app.get("/")
def index():
    return Response(content=_INDEX_HTML, media_type="text/html; charset=utf-8")


# ── 启动 ──────────────────────────────────────────────────────────────────────

def main():
    global DB_DIR
    import argparse
    parser = argparse.ArgumentParser(description="Mac 微信 FastAPI Web 服务")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--db-dir", default=str(DB_DIR))
    args = parser.parse_args()

    DB_DIR = Path(args.db_dir)

    print(f"启动 Mac 微信数据查看器: http://{args.host}:{args.port}")
    print(f"数据库目录: {DB_DIR}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
