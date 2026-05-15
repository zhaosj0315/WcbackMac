#!/usr/bin/env python3
"""
Mac 4.x FastAPI Web 服务
参考 PyWxDump remote_server.py，适配 Mac 4.x 数据库结构
启动: python3 scripts/mac_web_server.py
访问: http://127.0.0.1:5000
"""
import hashlib
import html
import json
import re
import sqlite3
import sys
import time
import threading
from collections import Counter
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

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
OLLAMA_BASE_URL = "http://127.0.0.1:11434"
OLLAMA_MAX_MODEL_B = 10.0
OLLAMA_MAX_FALLBACK_SIZE = 12 * 1024 ** 3
AI_RECENT_MESSAGE_LIMIT = 120
API_CACHE_TTL = 60.0
AI_REPLY_SIMILARITY_THRESHOLD = 0.86
SNAPSHOT_SYNC_INTERVAL = 15.0
SNAPSHOT_KEY_PATH = Path("/tmp/wechat_lldb_key_candidates.json")
BACKGROUND_REFRESH_COOLDOWN_MS = 8000

_api_cache: dict[str, tuple[float, Any]] = {}
_message_count_cache: dict[str, int] = {}
_table_db_cache: dict[str, list[Path]] = {}
_snapshot_sync_lock = threading.Lock()
_last_snapshot_sync_check = 0.0
_last_snapshot_sync_result: dict[str, Any] = {"updated": [], "errors": []}

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


def _normalize_msg_type(msg_type: Any) -> int:
    try:
        value = int(msg_type)
    except (TypeError, ValueError):
        return 0
    if value > 0xFFFFFFFF:
        return value & 0xFFFFFFFF
    return value


def _parse_appmsg_content(root) -> str | None:
    appmsg = root.find("appmsg")
    if appmsg is None:
        return None

    title = (appmsg.findtext("title") or "").strip()
    desc = (appmsg.findtext("des") or "").strip()
    url = (appmsg.findtext("url") or "").strip()
    app_type = (appmsg.findtext("type") or "").strip()
    type_names = {
        "1": "文字",
        "2": "图片",
        "3": "音乐",
        "4": "视频",
        "5": "链接",
        "6": "文件",
        "8": "表情",
        "19": "合并转发",
        "24": "笔记",
        "33": "小程序",
        "36": "小程序",
        "43": "视频号",
        "49": "文字",
        "51": "视频号",
        "57": "引用消息",
        "2000": "转账",
        "2003": "红包",
    }
    kind = type_names.get(app_type, f"type={app_type}" if app_type else "分享/文件")

    if app_type == "51":
        finder = appmsg.find("finderFeed")
        nickname = (finder.findtext("nickname") if finder is not None else "") or ""
        finder_desc = (finder.findtext("desc") if finder is not None else "") or ""
        summary = _clip_text(finder_desc or title or desc or nickname)
        if summary:
            return f"[{kind}] {summary}"
        return f"[{kind}]"

    if app_type == "24":
        note_summary = ""
        record_item = appmsg.findtext("recorditem") or ""
        if record_item.strip():
            try:
                import xml.etree.ElementTree as _ET
                record_root = _ET.fromstring(record_item.strip())
                note_summary = (
                    record_root.findtext("info")
                    or record_root.findtext(".//dataitem/datadesc")
                    or ""
                ).strip()
            except Exception:
                note_summary = ""
        summary = _clip_text(title or desc or note_summary)
        if summary:
            return f"[{kind}] {summary}" + (f"\n{url}" if url else "")
        return f"[{kind}]"

    if title:
        return f"[{kind}] {title}" + (f"\n{url}" if url else "")
    if desc:
        return f"[{kind}] {_clip_text(desc)}" + (f"\n{url}" if url else "")
    return f"[{kind}]"


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
    if msg_type in {24, 49, 51}:
        try:
            root = ET.fromstring(text)
            parsed = _parse_appmsg_content(root)
            if parsed:
                return parsed
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
    cached = _cache_get("shard_maps")
    if cached is not None:
        return cached

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

    return _cache_set("shard_maps", (shard_map, shard_my_rowid))


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
    return JSONResponse(
        {"code": 0, "data": data},
        headers={"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"},
    )


def _err(msg: str, code: int = 1001) -> JSONResponse:
    return JSONResponse(
        {"code": code, "msg": msg},
        headers={"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"},
    )


def _clip_text(text: str, limit: int = 160) -> str:
    normalized = " ".join((text or "").split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1] + "…"


def _parse_parameter_size_b(model: dict[str, Any]) -> float | None:
    details = model.get("details") or {}
    value = (details.get("parameter_size") or "").strip().upper()
    match = re.search(r"(\d+(?:\.\d+)?)\s*B", value)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            return None
    return None


def _format_model_size(size_bytes: int | None) -> str:
    if not size_bytes:
        return ""
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(size_bytes)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(size)}{unit}"
            return f"{size:.1f}{unit}"
        size /= 1024
    return ""


def _list_small_ollama_models() -> tuple[list[dict[str, Any]], str | None]:
    req = urlrequest.Request(f"{OLLAMA_BASE_URL}/api/tags")
    try:
        with urlrequest.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urlerror.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return [], f"Ollama 不可用：{exc}"

    models = []
    for item in payload.get("models", []):
        name = item.get("name") or item.get("model") or ""
        family = ((item.get("details") or {}).get("family") or "")
        lowered = f"{name} {family}".lower()
        if any(token in lowered for token in ("embed", "embedding", "ocr")):
            continue
        size_bytes = int(item.get("size") or 0)
        param_b = _parse_parameter_size_b(item)
        if param_b is not None:
            if param_b > OLLAMA_MAX_MODEL_B:
                continue
        elif size_bytes > OLLAMA_MAX_FALLBACK_SIZE:
            continue
        models.append(
            {
                "name": name,
                "size_bytes": size_bytes,
                "size_label": _format_model_size(size_bytes),
                "parameter_size_b": param_b,
                "parameter_size_label": f"{param_b:.1f}B" if param_b is not None else "",
                "family": family,
                "modified_at": item.get("modified_at") or "",
            }
        )

    def sort_key(model: dict[str, Any]) -> tuple[int, int, float, str]:
        name = model["name"].lower()
        preferred = 0 if name == "qwen3.5:2b" else 1
        custom = 0 if name.startswith("my-wechat-ai") else 1
        wechat_hint = 0 if "wechat" in name else 1
        parameter_size = model.get("parameter_size_b")
        parameter_sort = parameter_size if parameter_size is not None else 999.0
        return preferred, custom, wechat_hint, parameter_sort, name

    models.sort(key=sort_key)
    return models, None


def _default_ollama_model(models: list[dict[str, Any]]) -> str:
    return models[0]["name"] if models else ""


def _cache_get(key: str) -> Any | None:
    entry = _api_cache.get(key)
    if not entry:
        return None
    expires_at, value = entry
    if expires_at < time.time():
        _api_cache.pop(key, None)
        return None
    return value


def _cache_set(key: str, value: Any, ttl: float = API_CACHE_TTL) -> Any:
    _api_cache[key] = (time.time() + ttl, value)
    return value


def _clear_runtime_caches() -> None:
    _api_cache.clear()
    _message_count_cache.clear()
    _table_db_cache.clear()


def _looks_like_sqlite(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            return f.read(16) == b"SQLite format 3\x00"
    except OSError:
        return False


def _should_sync_rel(rel: str) -> bool:
    return rel.startswith("message/") or rel in {"contact/contact.db", "general/general.db"}


def _needs_refresh(src: Path, dst: Path) -> bool:
    if not dst.exists():
        return True
    try:
        src_stat = src.stat()
        dst_stat = dst.stat()
    except OSError:
        return True
    return src_stat.st_size != dst_stat.st_size or src_stat.st_mtime > dst_stat.st_mtime + 0.5


def _drop_sqlite_sidecars(db_file: Path) -> None:
    for suffix in ("-wal", "-shm"):
        sidecar = db_file.parent / f"{db_file.name}{suffix}"
        if sidecar.exists():
            sidecar.unlink()


def _connect_sqlite(db_file: Path, recover_sidecars: bool = False) -> sqlite3.Connection:
    try:
        conn = sqlite3.connect(db_file)
        conn.execute("pragma schema_version").fetchone()
        return conn
    except sqlite3.DatabaseError:
        if not recover_sidecars:
            raise
        _drop_sqlite_sidecars(db_file)
        conn = sqlite3.connect(db_file)
        conn.execute("pragma schema_version").fetchone()
        return conn


def _read_sqlite(db_file: Path, operation, recover_sidecars: bool = False):
    last_error: sqlite3.DatabaseError | None = None
    attempts = 2 if recover_sidecars else 1
    for attempt in range(attempts):
        conn: sqlite3.Connection | None = None
        try:
            conn = _connect_sqlite(db_file, recover_sidecars=recover_sidecars and attempt == 0)
            return operation(conn)
        except sqlite3.DatabaseError as exc:
            last_error = exc
            if not recover_sidecars or attempt + 1 >= attempts:
                raise
            _drop_sqlite_sidecars(db_file)
        finally:
            if conn is not None:
                conn.close()
    if last_error is not None:
        raise last_error
    raise sqlite3.DatabaseError(f"failed to read sqlite db: {db_file}")


def _maybe_sync_snapshot(force: bool = False) -> dict[str, Any]:
    global _last_snapshot_sync_check, _last_snapshot_sync_result
    now = time.time()
    if not force and now - _last_snapshot_sync_check < SNAPSHOT_SYNC_INTERVAL:
        return _last_snapshot_sync_result
    if not SNAPSHOT_KEY_PATH.exists():
        _last_snapshot_sync_check = now
        _last_snapshot_sync_result = {"updated": [], "errors": [], "reason": "missing_keys"}
        return _last_snapshot_sync_result
    if not _snapshot_sync_lock.acquire(blocking=False):
        return _last_snapshot_sync_result
    try:
        if not force and now - _last_snapshot_sync_check < SNAPSHOT_SYNC_INTERVAL:
            return _last_snapshot_sync_result
        try:
            from mac_decrypt_from_keys import choose_key_by_path, load_candidates
            from mac_decrypt_wcdb_raw import decrypt_db, decrypt_wal
        except Exception as exc:
            _last_snapshot_sync_check = time.time()
            _last_snapshot_sync_result = {"updated": [], "errors": [f"import failed: {exc}"]}
            return _last_snapshot_sync_result

        try:
            db_root, candidates = load_candidates(SNAPSHOT_KEY_PATH)
        except Exception as exc:
            _last_snapshot_sync_check = time.time()
            _last_snapshot_sync_result = {"updated": [], "errors": [f"load keys failed: {exc}"]}
            return _last_snapshot_sync_result

        path_to_key = choose_key_by_path(candidates)
        updated: list[str] = []
        errors: list[str] = []
        contact_changed = False
        for rel, key in sorted(path_to_key.items()):
            if not _should_sync_rel(rel):
                continue
            src = db_root / rel
            dst = DB_DIR / rel
            if not src.exists():
                continue
            try:
                rel_updated = False
                if _needs_refresh(src, dst):
                    decrypt_db(key, src, dst)
                    if not _looks_like_sqlite(dst):
                        raise RuntimeError("output is not sqlite")
                    updated.append(rel)
                    rel_updated = True

                src_wal = src.parent / f"{src.name}-wal"
                dst_wal = dst.parent / f"{dst.name}-wal"
                dst_shm = dst.parent / f"{dst.name}-shm"
                if src_wal.exists():
                    if _needs_refresh(src_wal, dst_wal):
                        decrypt_wal(key, src_wal, dst_wal)
                        updated.append(f"{rel}-wal")
                        rel_updated = True
                    if dst_shm.exists():
                        dst_shm.unlink()
                else:
                    if dst_wal.exists():
                        dst_wal.unlink()
                        updated.append(f"{rel}-wal")
                        rel_updated = True
                    if dst_shm.exists():
                        dst_shm.unlink()

                if rel_updated and rel.startswith("contact/"):
                    contact_changed = True
            except Exception as exc:
                errors.append(f"{rel}: {exc}")

        if updated:
            _clear_runtime_caches()
            if contact_changed:
                try:
                    import subprocess
                    subprocess.run(
                        [sys.executable, str(ROOT_DIR / "scripts" / "mac_contact_mapper.py")],
                        cwd=str(ROOT_DIR),
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        check=False,
                    )
                except Exception:
                    pass

        _last_snapshot_sync_check = time.time()
        _last_snapshot_sync_result = {"updated": updated, "errors": errors}
        return _last_snapshot_sync_result
    finally:
        _snapshot_sync_lock.release()


def _stats_payload() -> dict[str, int]:
    _maybe_sync_snapshot()
    cached = _cache_get("stats")
    if cached is not None:
        return cached

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

    return _cache_set("stats", {
        "total_messages": total,
        "total_contacts": contact_count,
        "total_sns": sns_count,
        "total_favorites": fav_count,
    })


def _sessions_payload(limit: int) -> list[dict[str, Any]] | None:
    _maybe_sync_snapshot()
    cache_key = f"sessions:{limit}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    msg_dir = _message_dir()
    if not msg_dir.exists():
        return None

    contact_map = _load_contact_map()
    t2w = _table_to_wxid(contact_map)
    shard_map, shard_my_rowid = _load_shard_maps()

    table_counts: dict[str, int] = {}
    table_latest: dict[str, int] = {}
    table_last_message: dict[str, dict[str, Any]] = {}

    for db_file in sorted(msg_dir.glob("message_*.db")):
        if "fts" in db_file.name:
            continue
        try:
            shard = db_file.stem

            def _scan_shard(conn: sqlite3.Connection):
                cur = conn.cursor()
                cur.execute("select name from sqlite_master where type='table' and name like 'Msg_%'")
                table_names = [table for (table,) in cur.fetchall()]
                shard_rows: list[tuple[str, int, int, tuple[Any, ...] | None]] = []
                for table in table_names:
                    cur.execute(f'select count(*), max(create_time) from "{table}"')
                    row = cur.fetchone()
                    cnt, latest = row if row else (0, 0)
                    cur.execute(
                        f'select local_id, local_type, create_time, real_sender_id, message_content '
                        f'from "{table}" order by create_time desc, local_id desc limit 1'
                    )
                    shard_rows.append((table, cnt or 0, latest or 0, cur.fetchone()))
                return shard_rows

            for table, cnt, latest, last_row in _read_sqlite(db_file, _scan_shard, recover_sidecars=True):
                table_counts[table] = table_counts.get(table, 0) + cnt
                table_latest[table] = max(table_latest.get(table, 0), latest)
                if last_row:
                    local_id, msg_type, create_time, sender_id, content = last_row
                    prev = table_last_message.get(table)
                    rank = (create_time or 0, local_id or 0)
                    prev_rank = ((prev or {}).get("create_time", 0), (prev or {}).get("local_id", 0))
                    if rank >= prev_rank:
                        base_type = _normalize_msg_type(msg_type)
                        text = _decode(content)
                        preview = _clip_text(_parse_content(base_type, text), 42)
                        is_me = sender_id == 0 or sender_id == shard_my_rowid.get(shard, -1)
                        table_last_message[table] = {
                            "local_id": local_id or 0,
                            "create_time": create_time or 0,
                            "preview": preview,
                            "is_sender": is_me,
                            "sender_name": "我" if is_me else shard_map.get(shard, {}).get(sender_id, str(sender_id)),
                        }
        except sqlite3.Error:
            continue

    sessions = []
    ordered_tables = sorted(
        table_counts.items(),
        key=lambda item: (-(table_latest.get(item[0], 0) or 0), -item[1], item[0]),
    )
    for table, count in ordered_tables[:limit]:
        wxid = t2w.get(table, "")
        display = contact_map.get(wxid, wxid or table.replace("Msg_", "")[:16])
        sessions.append({
            "table": table,
            "wxid": wxid,
            "display_name": display,
            "message_count": count,
            "last_time": _ts(table_latest.get(table, 0)),
            "last_preview": (table_last_message.get(table) or {}).get("preview", ""),
            "last_is_sender": bool((table_last_message.get(table) or {}).get("is_sender", False)),
        })
    return _cache_set(cache_key, sessions)


def _render_stats_html(stats: dict[str, int]) -> str:
    labels = ["总消息数", "联系人", "朋友圈", "收藏"]
    values = [
        stats.get("total_messages", 0),
        stats.get("total_contacts", 0),
        stats.get("total_sns", 0),
        stats.get("total_favorites", 0),
    ]
    actions = ["", " onclick=\"openPanel('contacts')\"", " onclick=\"openPanel('sns')\"", ""]
    return "".join(
        f'<div class="stat"{actions[idx]}><div class="n">{values[idx]:,}</div><div class="l">{labels[idx]}</div></div>'
        for idx in range(len(labels))
    )


def _render_sessions_html(sessions: list[dict[str, Any]]) -> str:
    if not sessions:
        return '<div style="padding:20px;color:#999;text-align:center">无会话</div>'
    return "".join(
        f'<a class="session-item" href="/?table={urlparse.quote(item["table"])}&name={urlparse.quote(item["display_name"])}" '
        f'data-table="{html.escape(item["table"])}" data-name="{html.escape(item["display_name"])}" '
        f'style="display:block;text-decoration:none;color:inherit">'
        f'<div class="name">{html.escape(item["display_name"])}</div>'
        f'<div class="meta">{item["message_count"]:,} 条 · {html.escape(item["last_time"] or "")}</div>'
        f'</a>'
        for item in sessions
    )


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
    sessions = _sessions_payload(limit)
    if sessions is None:
        return _err("message 目录不存在")
    return _ok(sessions)


# ── 消息 API ──────────────────────────────────────────────────────────────────

MSG_TYPE_NAMES = {
    1: "文本", 3: "图片", 34: "语音", 43: "视频", 47: "表情包",
    49: "分享/文件", 50: "语音通话", 10000: "系统消息",
}


def _extract_image_md5(text: str) -> str:
    try:
        import xml.etree.ElementTree as _ET
        root = _ET.fromstring((text or "").strip())
        img = root.find("img")
        return (img.get("md5", "") if img is not None else "") or ""
    except Exception:
        return ""


def _find_dbs_for_table(table: str) -> list[Path]:
    cached = _table_db_cache.get(table)
    if cached is not None:
        return cached
    msg_dir = _message_dir()
    result = []
    for db_file in sorted(msg_dir.glob("message_*.db")):
        if "fts" in db_file.name:
            continue
        try:
            if _read_sqlite(
                db_file,
                lambda conn: conn.execute(
                    "select 1 from sqlite_master where type='table' and name=? limit 1", (table,)
                ).fetchone(),
                recover_sidecars=True,
            ):
                result.append(db_file)
        except sqlite3.Error:
            continue
    _table_db_cache[table] = result
    return result


def _count_messages(table: str) -> int:
    cached = _message_count_cache.get(table)
    if cached is not None:
        return cached

    total = 0
    for db_file in _find_dbs_for_table(table):
        try:
            total += int(
                (_read_sqlite(
                    db_file,
                    lambda conn: conn.execute(f'select count(*) from "{table}"').fetchone(),
                    recover_sidecars=True,
                ) or [0])[0] or 0
            )
        except sqlite3.Error:
            continue
    _message_count_cache[table] = total
    return total


def _collect_messages(table: str, recent_limit: int = 0, per_shard_limit: int = 0, order: str = "asc") -> list[dict[str, Any]]:
    _maybe_sync_snapshot()
    db_files = _find_dbs_for_table(table)
    if not db_files:
        return []

    rows = []
    for db_file in db_files:
        try:
            shard = db_file.stem
            query = (
                f'select local_id, local_type, create_time, real_sender_id, message_content '
                f'from "{table}"'
            )
            params: tuple[Any, ...] = ()
            if recent_limit > 0:
                query += " order by create_time desc, local_id desc limit ?"
                params = (recent_limit,)
            elif per_shard_limit > 0:
                direction = "desc" if order == "desc" else "asc"
                query += f" order by create_time {direction}, local_id {direction} limit ?"
                params = (per_shard_limit,)
            else:
                query += " order by create_time asc, local_id asc"
            shard_rows = _read_sqlite(
                db_file,
                lambda conn: conn.execute(query, params).fetchall(),
                recover_sidecars=True,
            )
            rows.extend((*r, shard) for r in shard_rows)
        except sqlite3.Error:
            continue

    rows.sort(key=lambda r: (r[2] or 0, r[0] or 0))
    if recent_limit > 0 and len(rows) > recent_limit:
        rows = rows[-recent_limit:]
    elif per_shard_limit > 0:
        reverse = order == "desc"
        rows.sort(key=lambda r: (r[2] or 0, r[0] or 0), reverse=reverse)
        if len(rows) > per_shard_limit:
            rows = rows[:per_shard_limit]
        rows.sort(key=lambda r: (r[2] or 0, r[0] or 0))
    shard_map, shard_my_rowid = _load_shard_maps()

    messages = []
    for local_id, msg_type, create_time, sender_id, content, shard in rows:
        base_type = _normalize_msg_type(msg_type)
        text = _decode(content)
        readable = _parse_content(base_type, text)
        is_me = sender_id == 0 or sender_id == shard_my_rowid.get(shard, -1)
        sender_name = "我" if is_me else shard_map.get(shard, {}).get(sender_id, str(sender_id))
        file_name = None
        img_md5 = ""
        if base_type == 3:
            img_md5 = _extract_image_md5(text)
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
            "raw_content": text,
            "shard": shard,
        }
        if file_name:
            msg["file_name"] = file_name
        if img_md5:
            msg["img_md5"] = img_md5
        messages.append(msg)
    return messages


def _messages_payload(
    table: str,
    start: int = 0,
    limit: int = 50,
    type_filter: str = "",
    order: str = "asc",
    latest: bool = False,
) -> dict[str, Any] | None:
    candidate_limit = 0
    if not type_filter and limit > 0:
        candidate_limit = limit if latest else start + limit
    rows = _collect_messages(table, per_shard_limit=candidate_limit, order=("desc" if latest else order)) if candidate_limit else _collect_messages(table)
    if not rows:
        return None

    filter_types: set[int] = set()
    if type_filter:
        for t in type_filter.split(","):
            try:
                filter_types.add(int(t.strip()))
            except ValueError:
                pass

    latest_message = None
    if rows:
        latest_message = max(rows, key=lambda r: (r["create_time"] or 0, r["local_id"] or 0))

    rows.sort(key=lambda r: (r["create_time"] or 0, r["local_id"] or 0), reverse=(order == "desc"))
    if filter_types:
        rows = [r for r in rows if r["type"] in filter_types]
    total = len(rows) if filter_types or limit == 0 else _count_messages(table)
    resolved_start = start
    if limit == 0:
        page = rows[start:]
    elif latest:
        if order == "desc":
            resolved_start = 0
            page = rows[:limit]
        else:
            page = rows[-limit:]
            resolved_start = max(total - len(page), 0)
    else:
        page = rows[start: start + limit]
    return {"total": total, "messages": page, "latest_message": latest_message, "resolved_start": resolved_start}


def _render_messages_html(messages: list[dict[str, Any]], table: str) -> str:
    if not messages:
        return '<div style="padding:20px;color:#999;text-align:center">暂无聊天记录</div>'
    parts: list[str] = []
    for msg in messages:
        if msg["type"] == 10000:
            parts.append(f'<div class="msg"><div class="sys">{html.escape(msg["content"])}</div></div>')
            continue
        cls = "sent" if msg["is_sender"] else "recv"
        if msg["type"] == 3:
            image_url = (
                f'/api/image?table={urlparse.quote(table)}&local_id={msg["local_id"]}&create_time={msg["create_time"]}'
                + (f'&md5={urlparse.quote(msg["img_md5"])}' if msg.get("img_md5") else "")
            )
            body = (
                f'<img src="{image_url}" '
                f'onerror="this.outerHTML=\'<span style=color:#999;font-size:12px>[图片]</span>\'" alt="图片">'
            )
        elif msg["type"] == 34:
            body = f'<audio controls src="/api/voice?table={urlparse.quote(table)}&local_id={msg["local_id"]}&create_time={msg["create_time"]}"></audio>'
        elif msg["type"] == 43:
            body = (
                f'<video controls src="/api/video?table={urlparse.quote(table)}&local_id={msg["local_id"]}&create_time={msg["create_time"]}" '
                f'style="max-width:280px;border-radius:6px" '
                f'onerror="this.outerHTML=\'<span style=color:#999;font-size:12px>[视频不可用]</span>\'"></video>'
            )
        elif msg["type"] == 49 and msg.get("file_name"):
            body = (
                f'<a href="/api/file?table={urlparse.quote(table)}&filename={urlparse.quote(msg["file_name"])}" target="_blank" '
                f'style="display:flex;align-items:center;gap:6px;text-decoration:none;color:#333">'
                f'<span style="font-size:22px">📎</span>'
                f'<span style="font-size:13px;text-decoration:underline">{html.escape(msg["file_name"])}</span></a>'
            )
        else:
            body = html.escape(msg["content"])
        name_html = "" if msg["is_sender"] else f'<div class="name">{html.escape(msg["sender_name"] or "")}</div>'
        parts.append(
            f'<div class="msg {cls}"><div>{name_html}<div class="bubble">{body}</div></div>'
            f'<div class="time">{html.escape((msg["datetime"] or "")[11:])}</div></div>'
        )
    return "".join(parts)


@app.get("/api/messages")
def get_messages(
    table: str = Query(..., description="Msg_xxx 表名"),
    start: int = Query(0),
    limit: int = Query(50, description="每页条数，0=全部"),
    type_filter: str = Query("", description="消息类型过滤，逗号分隔，如 3,34,43"),
    order: str = Query("asc", description="asc 正序 / desc 倒序"),
    latest: bool = Query(False, description="是否直接定位到最新一页"),
):
    """获取指定会话的消息列表（跨分片聚合）"""
    payload = _messages_payload(table=table, start=start, limit=limit, type_filter=type_filter, order=order, latest=latest)
    if payload is None:
        return _err(f"未找到表 {table}")
    return _ok(payload)


@app.post("/api/refresh")
def refresh_snapshot():
    result = _maybe_sync_snapshot(force=True)
    return _ok(
        {
            "updated": result.get("updated", []),
            "errors": result.get("errors", []),
            "updated_count": len(result.get("updated", [])),
        }
    )


# ── 媒体 API ──────────────────────────────────────────────────────────────────

from mac_message_utils import MacMediaResolver  # noqa: E402

_resolver: MacMediaResolver | None = None


def _get_resolver() -> MacMediaResolver:
    global _resolver
    if _resolver is None:
        _resolver = MacMediaResolver(DB_DIR)
    return _resolver


def _decode_dat_image(path: Path) -> tuple[bytes, str] | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if len(data) < 2:
        return None
    signatures = (
        ("image/jpeg", b"\xff\xd8\xff"),
        ("image/png", b"\x89PNG"),
        ("image/gif", b"GIF8"),
        ("image/webp", b"RIFF"),
    )
    for media_type, magic in signatures:
        code = data[0] ^ magic[0]
        if all((data[idx] ^ code) == magic[idx] for idx in range(min(len(magic), len(data)))):
            return bytes(byte ^ code for byte in data), media_type
    return None


@app.get("/api/image")
def get_image(table: str = Query(...), local_id: int = Query(...), create_time: int = Query(...), md5: str = Query("")):
    """获取图片（本地缓存）"""
    path = _get_resolver().find_image_with_fallback(table, local_id, create_time, 0, md5)
    if path and path.exists():
        if path.suffix.lower() == ".dat":
            decoded = _decode_dat_image(path)
            if decoded:
                content, media_type = decoded
                return Response(content=content, media_type=media_type)
            return _err("图片缓存不存在", 4004)
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
    return _ok(_stats_payload())


# ── AI 推荐回复 API ─────────────────────────────────────────────────────────────

def _is_promptable_message(msg: dict[str, Any]) -> bool:
    return msg.get("type") != 10000 and bool((msg.get("content") or "").strip())


def _latest_inbound_message(messages: list[dict[str, Any]]) -> tuple[int, dict[str, Any]] | tuple[int, None]:
    for idx in range(len(messages) - 1, -1, -1):
        msg = messages[idx]
        if not msg.get("is_sender") and _is_promptable_message(msg):
            return idx, msg
    return -1, None


def _latest_conversation_message(messages: list[dict[str, Any]]) -> dict[str, Any] | None:
    for idx in range(len(messages) - 1, -1, -1):
        msg = messages[idx]
        if msg.get("type") != 10000:
            return msg
    return messages[-1] if messages else None


def _other_sender_names(messages: list[dict[str, Any]]) -> list[str]:
    return [
        str(msg.get("sender_name") or "").strip()
        for msg in messages
        if not msg.get("is_sender") and _is_promptable_message(msg) and str(msg.get("sender_name") or "").strip()
    ]


def _is_group_like_session(session_name: str, messages: list[dict[str, Any]]) -> bool:
    if "群" in (session_name or ""):
        return True
    return len(set(_other_sender_names(messages))) >= 3


def _promotional_score(text: str) -> int:
    value = str(text or "")
    score = 0
    if re.search(r"1\d{10}", value):
        score += 2
    if "http://" in value or "https://" in value or "mp.weixin.qq.com" in value:
        score += 2
    keywords = (
        "招聘", "兼职", "代理", "加我微信", "联系我", "电话", "同微", "同v", "推广",
        "项目", "投资", "下单", "上门回收", "免费领书", "扫码", "进群", "赚钱", "回馈",
        "福利", "店庆", "报名", "招生", "出租", "出售", "搬运工", "月结", "底薪", "提成",
    )
    score += sum(1 for kw in keywords if kw in value)
    return score


def _build_conversation_guidance(
    session_name: str,
    target_message: dict[str, Any],
    latest_message: dict[str, Any] | None,
    recent_messages: list[dict[str, Any]],
    style_profile: dict[str, Any],
) -> dict[str, Any]:
    group_like = _is_group_like_session(session_name, recent_messages)
    latest_exchange = latest_message or target_message
    target_text = str(target_message.get("content") or "")
    latest_text = str(latest_exchange.get("content") or "")
    promo_score = max(_promotional_score(target_text), _promotional_score(latest_text))
    promotional = promo_score >= 2
    low_style = int(style_profile.get("sample_count") or 0) < 2
    if promotional and group_like:
        strategy_note = "群聊里的推广/分享信息，优先给简短中性的礼貌回复，别编造个人情况。"
    elif promotional:
        strategy_note = "更像推广或转发信息，回复宜保守，避免过度投入或编造需求。"
    elif low_style:
        strategy_note = "历史样本较少，优先贴近最近上下文，少做风格猜测。"
    else:
        strategy_note = "优先模仿你近几次真实回复的长度和语气。"

    safe_replies: list[str] = []
    if promotional and group_like:
        safe_replies = [
            "收到，先了解一下。",
            "谢谢分享，有需要我再联系你。",
            "好的，先记下了，回头我再看看。",
        ]
    elif promotional:
        safe_replies = [
            "收到，我先看一下。",
            "谢谢你发我，我先了解下。",
            "好的，先放这边，有需要我再联系你。",
        ]
    elif latest_exchange.get("is_sender"):
        safe_replies = [
            "我先看一下，晚点再补一句。",
            "好的，我这边先跟一下，回头再说。",
            "先这样，我稍后再确认下。",
        ]

    return {
        "group_like": group_like,
        "promotional": promotional,
        "low_style": low_style,
        "strategy_note": strategy_note,
        "safe_replies": safe_replies,
    }


def _collect_style_examples(messages: list[dict[str, Any]], limit: int = 6) -> list[dict[str, str]]:
    pairs: list[dict[str, str]] = []
    for idx, msg in enumerate(messages):
        if msg.get("is_sender") or not _is_promptable_message(msg):
            continue
        replies = []
        cursor = idx + 1
        while cursor < len(messages):
            next_msg = messages[cursor]
            if next_msg.get("type") == 10000:
                cursor += 1
                continue
            if not next_msg.get("is_sender"):
                break
            if _is_promptable_message(next_msg):
                replies.append(_clip_text(next_msg.get("content", ""), 120))
            if len(replies) >= 2:
                break
            cursor += 1
        if replies:
            pairs.append(
                {
                    "incoming": _clip_text(msg.get("content", ""), 120),
                    "reply": " / ".join(r for r in replies if r),
                }
            )
    return pairs[-limit:]


def _leading_token(text: str) -> str:
    match = re.match(r"^[\u4e00-\u9fffA-Za-z]{1,4}", text)
    return match.group(0) if match else ""


def _trailing_token(text: str) -> str:
    match = re.search(r"[\u4e00-\u9fffA-Za-z]{1,4}$", text)
    return match.group(0) if match else ""


def _collect_contact_style_profile(messages: list[dict[str, Any]]) -> dict[str, Any]:
    sent_messages = [
        _clip_text(msg.get("content", ""), 160)
        for msg in messages
        if msg.get("is_sender") and _is_promptable_message(msg)
    ]
    if not sent_messages:
        return {
            "sample_count": 0,
            "avg_length": 0,
            "length_label": "长度样本不足",
            "tone_tags": ["以最近上下文为主"],
            "common_openers": [],
            "common_closers": [],
            "common_emojis": [],
        }

    openers: Counter[str] = Counter()
    closers: Counter[str] = Counter()
    emojis: Counter[str] = Counter()
    avg_length = round(sum(len(text) for text in sent_messages) / max(1, len(sent_messages)))
    haha_hits = 0
    polite_hits = 0
    exclaim_hits = 0
    question_hits = 0

    for text in sent_messages:
        opener = _leading_token(text)
        closer = _trailing_token(text)
        if len(opener) >= 2:
            openers[opener] += 1
        if len(closer) >= 2:
            closers[closer] += 1
        for emoji in re.findall(r"\[[^\[\]\n]{1,8}\]", text):
            emojis[emoji] += 1
        lowered = text.lower()
        if any(token in lowered for token in ("哈哈", "哎呀", "确实", "行吧", "好呀", "好嘞")):
            haha_hits += 1
        if any(token in text for token in ("谢谢", "辛苦", "麻烦", "不好意思", "抱歉")):
            polite_hits += 1
        if any(token in text for token in ("！", "!", "～", "~")):
            exclaim_hits += 1
        if any(token in text for token in ("？", "?")):
            question_hits += 1

    if avg_length <= 10:
        length_label = "回复偏短"
    elif avg_length <= 24:
        length_label = "回复中等偏短"
    else:
        length_label = "回复相对偏长"

    tone_tags = [length_label]
    threshold = max(2, len(sent_messages) // 8)
    if sum(emojis.values()) >= threshold:
        tone_tags.append("会用表情缓和语气")
    if haha_hits >= threshold:
        tone_tags.append("会用口语化缓冲词")
    if polite_hits >= threshold:
        tone_tags.append("会顺手带一点礼貌表达")
    if exclaim_hits >= threshold:
        tone_tags.append("语气不完全平铺直叙")
    if question_hits >= threshold:
        tone_tags.append("偶尔会反问或追问")

    return {
        "sample_count": len(sent_messages),
        "avg_length": avg_length,
        "length_label": length_label,
        "tone_tags": tone_tags[:4],
        "common_openers": [item for item, count in openers.most_common(3) if count >= 2],
        "common_closers": [item for item, count in closers.most_common(3) if count >= 2],
        "common_emojis": [item for item, count in emojis.most_common(3) if count >= 2],
    }


def _normalize_reply_text(text: str) -> str:
    lowered = str(text or "").strip().lower()
    return re.sub(r"[\s\.,，。！？!~～…:：;；\"'`、\-\(\)\[\]【】]+", "", lowered)


def _reply_similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, _normalize_reply_text(left), _normalize_reply_text(right)).ratio()


def _dedupe_replies(replies: list[str], threshold: float = AI_REPLY_SIMILARITY_THRESHOLD) -> list[str]:
    unique: list[str] = []
    for reply in replies:
        candidate = str(reply or "").strip()
        if not candidate:
            continue
        if any(_reply_similarity(candidate, existing) >= threshold for existing in unique):
            continue
        unique.append(candidate)
    return unique


def _with_prefix(prefix: str, text: str) -> str:
    if not prefix or text.startswith(prefix):
        return text
    if prefix[-1] in "，。！？!?~～":
        return prefix + text
    return f"{prefix}，{text}"


def _with_suffix(text: str, suffix: str) -> str:
    if not suffix or suffix in text:
        return text
    return f"{text}{suffix}"


def _expand_reply_variants(replies: list[str], style_profile: dict[str, Any]) -> list[str]:
    seeds = _dedupe_replies(replies)
    if not seeds:
        return []
    variants = list(seeds)
    prefixes = list(style_profile.get("common_openers") or [])
    if "会用口语化缓冲词" in (style_profile.get("tone_tags") or []):
        prefixes.extend(["确实", "哈哈", "行，那"])
    else:
        prefixes.extend(["确实", "那就", "行，那"])
    suffixes = list(style_profile.get("common_emojis") or [])
    if not suffixes and "会用表情缓和语气" in (style_profile.get("tone_tags") or []):
        suffixes = ["[捂脸]", "[笑哭]"]

    for base in list(seeds):
        trimmed = base.rstrip("。")
        for prefix in prefixes[:3]:
            variants.append(_with_prefix(prefix, trimmed))
        for suffix in suffixes[:2]:
            variants.append(_with_suffix(trimmed, suffix))
        variants.append(trimmed + "吧")
    return _dedupe_replies(variants)


def _reply_has_risky_hallucination(reply: str, recent_messages: list[dict[str, Any]], guidance: dict[str, Any]) -> bool:
    text = str(reply or "").strip()
    if not text:
        return True
    context = "\n".join(str(msg.get("content") or "") for msg in recent_messages)
    risky_tokens = [
        "家里", "小朋友", "宝宝", "急需用钱", "着急用钱", "变现", "资金紧张",
        "我正好需要", "我这边也有", "刚好我也", "我最近在做", "我最近也在",
    ]
    if guidance.get("promotional"):
        risky_tokens.extend(["投资", "分担一下", "想参与", "我也想做"])
    return any(token in text and token not in context for token in risky_tokens)


def _finalize_replies(
    replies: list[str],
    recent_messages: list[dict[str, Any]],
    style_profile: dict[str, Any],
    guidance: dict[str, Any],
) -> list[str]:
    cleaned: list[str] = []
    for reply in replies:
        candidate = str(reply or "").strip()
        if not candidate or len(candidate) > 120:
            continue
        if _reply_has_risky_hallucination(candidate, recent_messages, guidance):
            continue
        cleaned.append(candidate)
    cleaned = _dedupe_replies(cleaned)
    if len(cleaned) < 3:
        cleaned = _dedupe_replies(cleaned + list(guidance.get("safe_replies") or []))
    if len(cleaned) < 3:
        cleaned = _expand_reply_variants(cleaned or list(guidance.get("safe_replies") or []), style_profile)
    if len(cleaned) < 3:
        cleaned = _dedupe_replies(cleaned + ["收到，我先看一下。", "好的，我先了解下。", "行，我晚点再回你。"])
    while cleaned and len(cleaned) < 3:
        cleaned.append(cleaned[-1])
    return cleaned[:3]


def _build_reply_prompt(
    session_name: str,
    target_message: dict[str, Any],
    latest_message: dict[str, Any] | None,
    recent_messages: list[dict[str, Any]],
    style_examples: list[dict[str, str]],
    style_profile: dict[str, Any],
    guidance: dict[str, Any],
) -> str:
    context_lines = []
    for msg in recent_messages:
        role = "我" if msg.get("is_sender") else "对方"
        when = msg.get("datetime", "")
        context_lines.append(f"{when} {role}：{_clip_text(msg.get('content', ''), 140)}")

    example_lines = []
    for idx, example in enumerate(style_examples, start=1):
        example_lines.append(
            f"样例{idx}\n对方：{example['incoming']}\n我：{example['reply']}"
        )

    examples_text = "\n\n".join(example_lines) if example_lines else "暂无可用样例"
    context_text = "\n".join(context_lines) if context_lines else "暂无上下文"
    latest_text = _clip_text(target_message.get("content", ""), 200)
    latest_exchange = latest_message or target_message
    latest_exchange_role = "我" if latest_exchange.get("is_sender") else "对方"
    latest_exchange_text = _clip_text(latest_exchange.get("content", ""), 200)
    needs_reply = not bool(latest_exchange.get("is_sender"))
    profile_lines = [
        f"- 样本数：{style_profile.get('sample_count', 0)}",
        f"- 平均回复长度：约 {style_profile.get('avg_length', 0)} 字（{style_profile.get('length_label', '未知')}）",
        f"- 常见开头：{' / '.join(style_profile.get('common_openers') or []) or '无明显偏好'}",
        f"- 常见收尾：{' / '.join(style_profile.get('common_closers') or []) or '无明显偏好'}",
        f"- 常用表情：{' / '.join(style_profile.get('common_emojis') or []) or '较少使用'}",
        f"- 风格特征：{'；'.join(style_profile.get('tone_tags') or ['以最近上下文为主'])}",
    ]
    profile_text = "\n".join(profile_lines)

    return f"""你现在要帮我回复微信联系人“{session_name}”。

目标：基于下面这位联系人的历史聊天习惯，生成 3 条像“我”本人会发出的中文回复建议。

硬性要求：
1. 只输出 JSON，不要输出解释、前言、Markdown、思考过程。
2. JSON 格式固定为：{{"style_summary":"一句话概括我的回复风格","replies":["回复1","回复2","回复3"]}}
3. `replies` 必须正好 3 条，内容简短、自然、口语化，尽量像微信聊天。
4. 不要编造未在上下文中出现的事实、时间、地点、承诺或金额。
4.1 特别禁止编造“我家里怎样/我有孩子/我急需用钱/我正好也需要这个/我最近也在做这个”等个人背景，除非上下文明确出现。
5. 如果对方最后一条是图片、语音、文件等占位信息，也要给出自然回应。
6. 优先模仿历史里“我”的措辞、语气、长短和礼貌程度。
7. 3 条回复要明显不同，不能只是改一两个字；请分别偏向：稳妥自然 / 最像我平时 / 稍微更主动。
8. 如果历史里常用某些开头、表情或口头禅，可以适度复用，但不要三条都一样。
9. 一定优先参考“最新沟通消息”和最近几条对话的时间顺序，不要拿很久之前的旧话题当成当前话题。
10. 如果最新沟通消息是我发出的，说明我已经说过话了；这时给出的建议应更像“补一句/跟进一句/自然延续当前话题”，不要假装还停留在更早的那条对方消息上。
11. 如果这是群聊、推广、转发、招聘、广告、引流、小程序或链接分享场景，优先给“简短、中性、礼貌、低承诺”的回复；宁可克制，也不要硬编需求。

最近对话上下文：
{context_text}

当前沟通状态：
- 当前时间：{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
- 最新沟通消息：{latest_exchange.get("datetime", "")} {latest_exchange_role}：{latest_exchange_text}
- 是否当前对方在等我回复：{"是" if needs_reply else "不一定，最新一条是我发出的"}
- 场景判断：{"群聊" if guidance.get("group_like") else "普通会话"}；{"推广/分享信息" if guidance.get("promotional") else "普通交流"}
- 回复策略提醒：{guidance.get("strategy_note", "")}

这位联系人的专属回复画像：
{profile_text}

历史“对方消息 -> 我的回复”风格样例：
{examples_text}

当前需要回复的最后一条对方消息：
对方：{latest_text}
"""


def _build_retry_prompt(
    session_name: str,
    target_message: dict[str, Any],
    latest_message: dict[str, Any] | None,
    recent_messages: list[dict[str, Any]],
    style_examples: list[dict[str, str]],
    style_profile: dict[str, Any],
    guidance: dict[str, Any],
    existing_replies: list[str],
) -> str:
    base_prompt = _build_reply_prompt(session_name, target_message, latest_message, recent_messages, style_examples, style_profile, guidance)
    existing_text = "\n".join(f"- {reply}" for reply in existing_replies) or "- 无"
    return f"""{base_prompt}

上一次生成的候选过于相似：
{existing_text}

请重新生成 3 条**明显不同**的新回复，避免和上面这些候选重复或高度相似。
仍然只输出 JSON，格式保持：
{{"style_summary":"一句话概括我的回复风格","replies":["回复1","回复2","回复3"]}}
"""


def _call_ollama_reply_suggester(model: str, prompt: str) -> tuple[dict[str, Any] | None, str | None]:
    payload = {
        "model": model,
        "prompt": prompt,
        "format": "json",
        "stream": False,
        "think": False,
        "options": {
            "temperature": 0.35,
            "num_predict": 180,
        },
    }
    req = urlrequest.Request(
        f"{OLLAMA_BASE_URL}/api/generate",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlrequest.urlopen(req, timeout=180) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
    except (urlerror.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return None, f"调用 Ollama 失败：{exc}"

    text = (
        raw.get("response")
        or raw.get("thinking")
        or (raw.get("message") or {}).get("content")
        or (raw.get("message") or {}).get("thinking")
        or ""
    ).strip()
    if not text:
        return None, "Ollama 未返回内容"

    text = re.sub(r"^\s*Thinking Process:\s*", "", text, flags=re.I)
    text = re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.I).strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)
        text = text.strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", text)
        if not match:
            return None, "Ollama 返回了非 JSON 内容"
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None, "Ollama 返回了非 JSON 内容"

    replies_raw = parsed.get("replies") or []
    if not isinstance(replies_raw, list):
        replies_raw = []

    replies = []
    for item in replies_raw:
        candidate = re.sub(r"^\s*[\d\-\.\)\(、]+\s*", "", str(item or "")).strip().strip('"')
        if candidate and candidate not in replies:
            replies.append(candidate)
    if not replies:
        return None, "Ollama 未生成可用回复"

    while len(replies) < 3:
        replies.append(replies[-1])

    return {
        "style_summary": _clip_text(str(parsed.get("style_summary") or ""), 60),
        "replies": replies[:3],
    }, None


@app.get("/api/ai/models")
def get_ai_models():
    models, err = _list_small_ollama_models()
    if err:
        return _err(err, 5002)
    return _ok(
        {
            "default_model": _default_ollama_model(models),
            "models": models,
        }
    )


@app.get("/api/ai/reply-suggestion")
def get_reply_suggestion(table: str = Query(...), model: str = Query("")):
    messages = _collect_messages(table, recent_limit=AI_RECENT_MESSAGE_LIMIT)
    if not messages:
        return _err(f"未找到表 {table}", 4004)

    models, err = _list_small_ollama_models()
    if err:
        return _err(err, 5002)
    if not models:
        return _err("未发现不超过 10B 的本地 Ollama 模型", 5003)

    chosen_model = model or _default_ollama_model(models)
    if chosen_model not in {item["name"] for item in models}:
        return _err("所选模型不在允许范围内（需为本地 <=10B 模型）", 5004)

    latest_message = _latest_conversation_message(messages)
    target_idx, target_message = _latest_inbound_message(messages)
    if target_message is None:
        return _err("当前会话没有可用于推荐回复的对方消息", 4005)

    recent_messages = messages[max(0, len(messages) - 12):]
    style_examples = _collect_style_examples(messages[:target_idx], limit=6)
    style_profile = _collect_contact_style_profile(messages[:target_idx])
    session_name = (latest_message or target_message).get("sender_name") or target_message.get("sender_name") or table
    guidance = _build_conversation_guidance(session_name, target_message, latest_message, recent_messages, style_profile)
    if guidance.get("promotional") and guidance.get("group_like") and guidance.get("low_style"):
        result = {
            "style_summary": "群聊推广场景下更适合简短中性的回复",
            "replies": list(guidance.get("safe_replies") or []),
        }
    else:
        prompt = _build_reply_prompt(session_name, target_message, latest_message, recent_messages, style_examples, style_profile, guidance)
        result, call_err = _call_ollama_reply_suggester(chosen_model, prompt)
        if call_err:
            return _err(call_err, 5005)

    replies = _finalize_replies(result.get("replies", []), recent_messages, style_profile, guidance)
    if len(replies) < 3:
        retry_prompt = _build_retry_prompt(
            session_name,
            target_message,
            latest_message,
            recent_messages,
            style_examples,
            style_profile,
            guidance,
            replies,
        )
        retry_result, retry_err = _call_ollama_reply_suggester(chosen_model, retry_prompt)
        if not retry_err and retry_result:
            replies = _finalize_replies(replies + retry_result.get("replies", []), recent_messages, style_profile, guidance)
    if not replies:
        return _err("未生成可用推荐回复", 5006)

    return _ok(
        {
            "model": chosen_model,
            "latest_message": {
                "content": (latest_message or {}).get("content", ""),
                "datetime": (latest_message or {}).get("datetime", ""),
                "sender_name": (latest_message or {}).get("sender_name", ""),
                "is_sender": bool((latest_message or {}).get("is_sender", False)),
            },
            "target_message": {
                "content": target_message.get("content", ""),
                "datetime": target_message.get("datetime", ""),
                "sender_name": target_message.get("sender_name", ""),
            },
            "needs_reply": not bool((latest_message or target_message).get("is_sender")),
            "style_summary": result.get("style_summary", ""),
            "strategy_note": guidance.get("strategy_note", ""),
            "scene_label": ("群聊" if guidance.get("group_like") else "普通会话") + (" · 推广/分享" if guidance.get("promotional") else ""),
            "replies": replies[:3],
            "style_example_count": len(style_examples),
            "recent_window_size": len(messages),
            "style_profile": style_profile,
        }
    )


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
.session-tools{padding:8px 10px;border-bottom:1px solid #f5f5f5;display:flex;gap:6px;flex-wrap:wrap}
.session-tools button{padding:5px 10px;border:1px solid #d9d9d9;border-radius:14px;background:#fff;font-size:12px;color:#555;cursor:pointer}
.session-tools button.active{background:#07c160;color:#fff;border-color:#07c160}
.session-list{flex:1;overflow-y:auto}
.session-item{padding:12px 14px;cursor:pointer;border-bottom:1px solid #f5f5f5;transition:background .15s}
.session-item:hover,.session-item.active{background:#f0faf4}
.session-item .row{display:flex;align-items:center;gap:8px}
.session-item .name{font-size:14px;font-weight:500;color:#333;flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.session-item .meta{font-size:11px;color:#999;white-space:nowrap}
.session-item .preview{font-size:12px;color:#666;margin-top:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.reply-badge{display:inline-flex;align-items:center;padding:1px 6px;border-radius:10px;background:#fff3e8;color:#d46b08;font-size:11px;border:1px solid #ffd591}
.chat-area{flex:1;display:flex;flex-direction:column;overflow:hidden}
.chat-header{padding:10px 20px;border-bottom:1px solid #e8e8e8;background:#fff;font-weight:bold;font-size:15px;flex-shrink:0;display:flex;align-items:center;gap:10px}
.chat-header .title{flex:1}
.toolbar{display:flex;gap:6px;align-items:center;flex-wrap:wrap}
.toolbar select,.toolbar button{padding:4px 10px;border:1px solid #d9d9d9;border-radius:5px;background:#fff;cursor:pointer;font-size:12px;color:#555}
.toolbar select:focus,.toolbar button:hover{border-color:#07c160;color:#07c160;outline:none}
.toolbar button.active{background:#07c160;color:#fff;border-color:#07c160}
.latest-box{padding:10px 20px;border-bottom:1px solid #eef3ef;background:#fffef7;display:none;flex-direction:column;gap:4px;flex-shrink:0}
.latest-box.open{display:flex}
.latest-box .label{font-size:12px;color:#999}
.latest-box .content{font-size:13px;color:#333;line-height:1.5}
.ai-box{padding:12px 20px;border-bottom:1px solid #e8e8e8;background:#f8fffb;display:none;gap:10px;flex-direction:column;flex-shrink:0}
.ai-box.open{display:flex}
.ai-top{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.ai-top strong{font-size:14px;color:#1f1f1f}
.ai-top select,.ai-top button{padding:5px 10px;border:1px solid #d9d9d9;border-radius:5px;background:#fff;font-size:12px;color:#555}
.ai-meta{font-size:12px;color:#666;line-height:1.6}
.ai-target{padding:8px 10px;background:#fff;border:1px solid #e6f4ea;border-radius:8px;font-size:13px;color:#333;line-height:1.5}
.ai-replies{display:flex;gap:8px;flex-wrap:wrap}
.ai-reply-item{display:flex;align-items:stretch;max-width:420px;border:1px solid #ccebd6;border-radius:10px;background:#fff;overflow:hidden}
.ai-reply-text{padding:9px 12px;color:#1f1f1f;font-size:13px;line-height:1.5;cursor:pointer;flex:1;min-width:0}
.ai-reply-text:hover{background:#f0faf4}
.ai-copy-btn{border:none;border-left:1px solid #dff3e5;background:#f7fffa;color:#07c160;font-size:12px;padding:0 12px;cursor:pointer;white-space:nowrap}
.ai-copy-btn:hover{background:#e9f9ef}
.ai-tip{font-size:12px;color:#999}
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
<div class="stats-bar" id="stats-bar">__INITIAL_STATS__</div>
<div class="main">
  <div class="sidebar">
    <div class="search-box">
      <input id="search" placeholder="搜索联系人..." oninput="onSearch(this.value)">
    </div>
    <div class="session-tools">
      <button id="btn-session-all" class="active" onclick="setSessionMode('all')">全部会话</button>
      <button id="btn-session-pending" onclick="setSessionMode('pending')">待回复</button>
      <button id="btn-open-pending" onclick="openLatestPending()">打开最新待回复</button>
    </div>
    <div class="session-list" id="session-list">__INITIAL_SESSIONS__</div>
  </div>
  <div class="chat-area">
    <div class="chat-header">
      <span class="title" id="chat-title">__INITIAL_CHAT_TITLE__</span>
      <div class="toolbar" id="toolbar" style="display:__INITIAL_TOOLBAR_DISPLAY__">
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
        <button id="btn-latest" onclick="jumpToLatestConversation()" title="跳到最新沟通内容">⇥ 最新沟通</button>
        <button id="btn-refresh" onclick="manualRefreshLatest()" title="同步并刷新最新消息">⟳ 刷新最新</button>
        <button id="btn-order" onclick="toggleOrder()" title="切换排序">⬆ 正序</button>
      </div>
    </div>
    <div class="latest-box" id="latest-box">
      <div class="label">最新沟通消息</div>
      <div class="content" id="latest-content">暂无消息</div>
    </div>
    <div class="ai-box" id="ai-box">
      <div class="ai-top">
        <strong>AI 推荐回复</strong>
        <select id="ai-model" onchange="loadReplySuggestion()"></select>
        <button onclick="loadReplySuggestion()">重新生成</button>
        <span class="ai-tip">仅显示本地 Ollama 中 ≤10B 的模型</span>
      </div>
      <div class="ai-meta" id="ai-meta">打开会话后自动生成推荐回复</div>
      <div class="ai-target" id="ai-target">暂无推荐对象</div>
      <div class="ai-replies" id="ai-replies"></div>
      <div class="ai-tip">点击任意推荐文案可复制到剪贴板。</div>
    </div>
    <div class="msg-list" id="msg-list">__INITIAL_MESSAGES__</div>
    <div class="pagination" id="pagination" style="display:__INITIAL_PAGINATION_DISPLAY__">
      <button class="btn" id="btn-prev" onclick="changePage(-1)" disabled>上一页</button>
      <span class="page-info" id="page-info">__INITIAL_PAGE_INFO__</span>
      <button class="btn" id="btn-next" onclick="changePage(1)" __INITIAL_NEXT_DISABLED__>下一页</button>
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
let curOrder='asc', curFilter='', curFollowLatest=false;
let contactPage=0, contactTotal=0;
let snsPage=0, snsTotal=0;
let aiModels=[], aiDefaultModel='';
let sessionMode='all';
let messageRequestSeq=0, aiRequestSeq=0;
let messageAbortController=null, aiAbortController=null, refreshAbortController=null;
let refreshTimer=null, refreshInFlight=false;
let lastBackgroundRefreshAt=0;
const SNS_PAGE=50, CONTACT_PAGE=100;
const LIVE_REFRESH_MS=20000;
const BACKGROUND_REFRESH_COOLDOWN_MS=8000;
const initialParams = new URLSearchParams(window.location.search);
curTable = initialParams.get('table') || '';
const initialName = initialParams.get('name') || '';

if(curTable){
  curFollowLatest=true;
  document.getElementById('latest-box').classList.add('open');
  document.getElementById('ai-box').classList.add('open');
  document.getElementById('toolbar').style.display='flex';
  if(initialName){
    document.getElementById('chat-title').textContent=initialName;
  }
}

function syncOrderButton(){
  const btn=document.getElementById('btn-order');
  btn.textContent=curOrder==='asc'?'⬆ 正序':'⬇ 倒序';
  btn.classList.toggle('active',curOrder==='asc');
}
syncOrderButton();

function handleImageError(img){
  if(!img)return;
  if(img.dataset.retrying==='1'){
    img.outerHTML='<span style="color:#999;font-size:12px">[图片]</span>';
    return;
  }
  img.dataset.retrying='1';
  scheduleBackgroundRefresh(false, false);
  const joiner=img.src.includes('?')?'&':'?';
  img.src=`${img.src}${joiner}img_retry=1&_=${Date.now()}`;
}

function refreshStats(){
fetch('/api/stats').then(r=>r.json()).then(d=>{
  const s=d.data;
  const labels=['总消息数','联系人','朋友圈','收藏'];
  const vals=[s.total_messages,s.total_contacts,s.total_sns,s.total_favorites];
  const actions=[null,'contacts','sns',null];
  document.getElementById('stats-bar').innerHTML=labels.map((l,i)=>{
    const onclick=actions[i]?`onclick="openPanel('${actions[i]}')"`:'' ;
    return `<div class="stat" ${onclick}><div class="n">${(vals[i]||0).toLocaleString()}</div><div class="l">${l}</div></div>`;
  }).join('');
}).catch(err=>{
  document.getElementById('stats-bar').innerHTML=`<div style="padding:12px 20px;color:#d4380d;font-size:13px">统计加载失败：${escHtml(err.message||'未知错误')}</div>`;
});
}
refreshStats();

function refreshSessions(){
fetch('/api/sessions?limit=2000').then(r=>r.json()).then(d=>{
  allSessions=d.data||[];
  onSearch(document.getElementById('search').value||'');
}).catch(err=>{
  document.getElementById('session-list').innerHTML=`<div style="padding:20px;color:#d4380d;text-align:center">会话列表加载失败：${escHtml(err.message||'未知错误')}</div>`;
});
}
refreshSessions();

fetch('/api/ai/models').then(r=>r.json()).then(d=>{
  if(d.code!==0){
    setAiStatus(d.msg||'AI 模型加载失败');
    return;
  }
  aiModels=d.data.models||[];
  aiDefaultModel=d.data.default_model||'';
  renderAiModels();
}).catch(err=>{
  setAiStatus('AI 模型加载失败：'+err.message);
});

function renderSessions(list){
  document.getElementById('session-list').innerHTML=list.map(s=>
    `<a class="session-item ${s.table===curTable?'active':''}" href="/?table=${encodeURIComponent(s.table)}&name=${encodeURIComponent(s.display_name)}"
      data-table="${escHtml(s.table)}" data-name="${escHtml(s.display_name)}"
      style="display:block;text-decoration:none;color:inherit">
      <div class="row"><div class="name">${escHtml(s.display_name)}</div>${s.last_is_sender?'':'<span class="reply-badge">待回复</span>'}<div class="meta">${escHtml(s.last_time||'')}</div></div>
      <div class="preview">${escHtml(s.last_preview||`${s.message_count.toLocaleString()} 条消息`)}</div>
    </a>`
  ).join('')||'<div style="padding:20px;color:#999;text-align:center">无会话</div>';
}

document.getElementById('session-list').addEventListener('click',function(e){
  const item=e.target.closest('.session-item');
  if(!item)return;
  e.preventDefault();
  openSession(item.dataset.table,item.dataset.name);
});

function onSearch(q){
  q=(q||'').toLowerCase();
  let list=allSessions;
  if(sessionMode==='pending'){
    list=list.filter(s=>!s.last_is_sender);
  }
  if(q){
    list=list.filter(s=>(s.display_name||'').toLowerCase().includes(q)||(s.wxid||'').toLowerCase().includes(q)||(s.last_preview||'').toLowerCase().includes(q));
  }
  renderSessions(list);
}

function setSessionMode(mode){
  sessionMode=mode;
  document.getElementById('btn-session-all').classList.toggle('active',mode==='all');
  document.getElementById('btn-session-pending').classList.toggle('active',mode==='pending');
  onSearch(document.getElementById('search').value||'');
}

function openLatestPending(){
  const pending=allSessions.filter(s=>!s.last_is_sender);
  if(!pending.length){
    setAiStatus('当前没有待回复的最新会话');
    return;
  }
  openSession(pending[0].table,pending[0].display_name);
}

function openSession(table,name){
  curTable=table; curPage=0; curFilter=''; curOrder='asc'; curFollowLatest=true;
  history.pushState({table,name},'',`/?table=${encodeURIComponent(table)}&name=${encodeURIComponent(name)}`);
  document.getElementById('chat-title').textContent=name;
  document.getElementById('toolbar').style.display='flex';
  document.getElementById('latest-box').classList.add('open');
  document.getElementById('ai-box').classList.add('open');
  document.getElementById('type-filter').value='';
  syncOrderButton();
  document.querySelectorAll('.session-item').forEach(el=>el.classList.toggle('active',el.dataset.table===table));
  document.getElementById('msg-list').innerHTML='<div style="padding:20px;color:#999;text-align:center">正在加载最新聊天记录...</div>';
  document.getElementById('ai-target').textContent='正在分析当前聊天内容...';
  document.getElementById('ai-replies').innerHTML='';
  loadMessages();
  if(aiModels.length){
    loadReplySuggestion();
  }
  scheduleBackgroundRefresh(true, false);
}

function jumpToLatestConversation(){
  if(!curTable)return;
  curPage=0;
  curOrder='asc';
  curFilter='';
  curFollowLatest=true;
  document.getElementById('type-filter').value='';
  syncOrderButton();
  loadMessages();
  scheduleBackgroundRefresh(true, false);
}

function onFilterChange(){
  curFilter=document.getElementById('type-filter').value;
  curFollowLatest=false;
  curPage=0; loadMessages();
}

function toggleOrder(){
  curOrder=curOrder==='asc'?'desc':'asc';
  curFollowLatest=false;
  curPage=0;
  syncOrderButton();
  loadMessages();
}

function changePage(delta){curFollowLatest=false;curPage+=delta;loadMessages();}
function changePageSize(val){PAGE=parseInt(val);curPage=0;loadMessages();}

function renderLatestMessage(msg){
  const box=document.getElementById('latest-box');
  const el=document.getElementById('latest-content');
  if(!msg){
    box.classList.remove('open');
    el.textContent='暂无消息';
    return;
  }
  box.classList.add('open');
  const role=msg.is_sender?'我':'对方';
  const time=msg.datetime||'';
  const content=msg.content||'[空]';
  el.innerHTML=`<strong>${escHtml(role)}</strong> · ${escHtml(content)}<br><span style="color:#999;font-size:12px">${escHtml(time)}</span>`;
}

function loadMessages(){
  if(!curTable)return;
  if(messageAbortController){
    messageAbortController.abort();
  }
  messageAbortController=new AbortController();
  const requestTable=curTable;
  const requestOrder=curOrder;
  const requestFilter=curFilter;
  const requestPage=curPage;
  const requestFollowLatest=curFollowLatest;
  const requestId=++messageRequestSeq;
  const start=PAGE===0?0:curPage*PAGE;
  let url=`/api/messages?table=${encodeURIComponent(requestTable)}&start=${start}&limit=${PAGE}&order=${requestOrder}`;
  if(requestFilter)url+=`&type_filter=${requestFilter}`;
  if(requestFollowLatest&&PAGE!==0&&!requestFilter)url+='&latest=1';
  fetch(url,{signal:messageAbortController.signal}).then(r=>r.json()).then(d=>{
      if(requestId!==messageRequestSeq||requestTable!==curTable||requestOrder!==curOrder||requestFilter!==curFilter||requestPage!==curPage||requestFollowLatest!==curFollowLatest){
        return;
      }
    curTotal=d.data.total;
    if(requestFollowLatest&&PAGE!==0&&typeof d.data.resolved_start==='number'){
      curPage=Math.floor(d.data.resolved_start/PAGE);
    }
    renderLatestMessage(d.data.latest_message||null);
    renderMessages(d.data.messages, requestTable);
    const pages=PAGE===0?1:Math.ceil(curTotal/PAGE);
    document.getElementById('pagination').style.display='flex';
    const info=PAGE===0?`全部 ${curTotal.toLocaleString()} 条`:`第 ${curPage+1}/${pages} 页（共 ${curTotal.toLocaleString()} 条）`;
      document.getElementById('page-info').textContent=info;
      document.getElementById('btn-prev').disabled=curPage===0||PAGE===0;
      document.getElementById('btn-next').disabled=PAGE===0||(curPage+1)>=pages;
  }).catch(err=>{
    if(err.name==='AbortError') return;
    document.getElementById('msg-list').innerHTML=`<div style="padding:20px;color:#d4380d;text-align:center">聊天记录加载失败：${escHtml(err.message||'未知错误')}</div>`;
  });
}

function renderMessages(msgs, table){
  const el=document.getElementById('msg-list');
  el.innerHTML=msgs.map(m=>{
    if(m.type===10000)return `<div class="msg"><div class="sys">${escHtml(m.content)}</div></div>`;
    const cls=m.is_sender?'sent':'recv';
    let body='';
    if(m.type===3){
      const md5Param=m.img_md5?`&md5=${encodeURIComponent(m.img_md5)}`:'';
      body=`<img loading="lazy" decoding="async" src="/api/image?table=${encodeURIComponent(table)}&local_id=${m.local_id}&create_time=${m.create_time}${md5Param}" onerror="handleImageError(this)" alt="图片">`;
    }else if(m.type===34){
      body=`<audio controls src="/api/voice?table=${encodeURIComponent(table)}&local_id=${m.local_id}&create_time=${m.create_time}"></audio>`;
    }else if(m.type===43){
      body=`<video controls src="/api/video?table=${encodeURIComponent(table)}&local_id=${m.local_id}&create_time=${m.create_time}" style="max-width:280px;border-radius:6px" onerror="this.outerHTML='<span style=color:#999;font-size:12px>[视频不可用]</span>'"></video>`;
    }else if(m.type===49&&m.file_name){
      body=`<a href="/api/file?table=${encodeURIComponent(table)}&filename=${encodeURIComponent(m.file_name)}" target="_blank" style="display:flex;align-items:center;gap:6px;text-decoration:none;color:#333">
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

function renderAiModels(){
  const select=document.getElementById('ai-model');
  if(!aiModels.length){
    select.innerHTML='';
    select.disabled=true;
    setAiStatus('未发现可用的小模型，请先在本地 Ollama 安装 ≤10B 模型。');
    return;
  }
  select.disabled=false;
  select.innerHTML=aiModels.map(m=>{
    const meta=[m.parameter_size_label,m.size_label].filter(Boolean).join(' · ');
    return `<option value="${escHtml(m.name)}">${escHtml(m.name)}${meta?` · ${escHtml(meta)}`:''}</option>`;
  }).join('');
  if(aiDefaultModel)select.value=aiDefaultModel;
  setAiStatus('打开会话后自动生成推荐回复');
  if(curTable){
    document.getElementById('ai-box').classList.add('open');
    loadReplySuggestion();
  }
}

function currentAiModel(){
  return document.getElementById('ai-model').value||aiDefaultModel||'';
}

function setAiStatus(text){
  document.getElementById('ai-meta').textContent=text;
}

function copyReply(text){
  if(navigator.clipboard&&navigator.clipboard.writeText){
    navigator.clipboard.writeText(text).then(()=>setAiStatus('已复制推荐回复')).catch(()=>setAiStatus('复制失败，请手动复制'));
    return;
  }
  setAiStatus('当前浏览器不支持自动复制');
}

function loadReplySuggestion(){
  const repliesEl=document.getElementById('ai-replies');
  const targetEl=document.getElementById('ai-target');
  if(!curTable){
    targetEl.textContent='暂无推荐对象';
    repliesEl.innerHTML='';
    return;
  }
  if(!aiModels.length){
    setAiStatus('未发现可用的小模型，请先在本地 Ollama 安装 ≤10B 模型。');
    return;
  }
  if(aiAbortController){
    aiAbortController.abort();
  }
  aiAbortController=new AbortController();
  const requestTable=curTable;
  const model=currentAiModel();
  const requestId=++aiRequestSeq;
  setAiStatus(`正在用 ${model} 生成推荐回复...`);
  targetEl.textContent='正在分析最新沟通内容...';
  repliesEl.innerHTML='';
  fetch(`/api/ai/reply-suggestion?table=${encodeURIComponent(requestTable)}&model=${encodeURIComponent(model)}`,{signal:aiAbortController.signal})
    .then(r=>r.json())
    .then(d=>{
      if(requestId!==aiRequestSeq||requestTable!==curTable||model!==currentAiModel()){
        return;
      }
      if(d.code!==0){
        setAiStatus(d.msg||'推荐回复生成失败');
        targetEl.textContent='暂无可用推荐';
        return;
      }
      const info=d.data;
      const summary=info.style_summary?` · 风格：${info.style_summary}`:'';
      const strategy=info.strategy_note?` · 策略：${info.strategy_note}`:'';
      setAiStatus(`模型：${info.model} · 最近消息 ${info.recent_window_size} 条 · 样例 ${info.style_example_count} 组${summary}${strategy}`);
      const latestRole=info.latest_message&&info.latest_message.is_sender?'我':'对方';
      const replyHint=info.needs_reply?'当前看起来需要回复':'当前最新一条是你发出的';
      targetEl.innerHTML=`最新沟通消息：<strong>${escHtml(latestRole)}：${escHtml((info.latest_message&&info.latest_message.content)||'[空]')}</strong><br><span style="color:#999;font-size:12px">${escHtml((info.latest_message&&info.latest_message.datetime)||'')} · ${escHtml(replyHint)}${info.scene_label?` · ${escHtml(info.scene_label)}`:''}</span><br><span style="color:#666;font-size:12px">回复参考点：${escHtml(info.target_message.content||'[空]')}</span>`;
      repliesEl.innerHTML=(info.replies||[]).map(reply=>{
        const safe=encodeURIComponent(reply);
        return `<div class="ai-reply-item"><div class="ai-reply-text" onclick="copyReply(decodeURIComponent('${safe}'))">${escHtml(reply)}</div><button class="ai-copy-btn" onclick="copyReply(decodeURIComponent('${safe}'))">复制</button></div>`;
      }).join('');
    })
    .catch(err=>{
      if(err.name==='AbortError') return;
      setAiStatus('推荐回复生成失败：'+err.message);
      targetEl.textContent='暂无可用推荐';
    });
}

function refreshLiveData(){
  if(document.hidden){
    return;
  }
  refreshStats();
  refreshSessions();
  if(curTable){
    loadMessages();
    scheduleBackgroundRefresh(false);
  }
}
setInterval(refreshLiveData, LIVE_REFRESH_MS);
if(curTable){
  loadMessages();
  scheduleBackgroundRefresh(true, false);
}

function refreshLatestView(silent=false, refreshAi=false, useButtonState=true){
  const btn=document.getElementById('btn-refresh');
  if(refreshInFlight){
    return Promise.resolve(false);
  }
  if(refreshAbortController){
    refreshAbortController.abort();
  }
  refreshAbortController=new AbortController();
  refreshInFlight=true;
  const original=btn.textContent;
  if(useButtonState){
    btn.disabled=true;
    btn.textContent='刷新中...';
  }
  return fetch('/api/refresh',{method:'POST',signal:refreshAbortController.signal})
    .then(r=>r.json())
    .then(d=>{
      lastBackgroundRefreshAt=Date.now();
      if(d.code!==0){
        throw new Error(d.msg||'刷新失败');
      }
      refreshStats();
      refreshSessions();
      if(curTable){
        loadMessages();
        if(refreshAi&&aiModels.length){
          loadReplySuggestion();
        }
      }
      const updatedCount=(d.data&&d.data.updated_count)||0;
      const errorCount=((d.data&&d.data.errors)||[]).length;
      if(silent){
        return;
      }
      if(errorCount){
        setAiStatus(`已刷新，但有 ${errorCount} 个文件同步失败`);
      }else if(updatedCount){
        setAiStatus(`已同步 ${updatedCount} 个数据库文件，并刷新到最新消息`);
      }else{
        setAiStatus('已检查最新消息，当前已经是最新快照');
      }
      return true;
    })
    .catch(err=>{
      if(err.name==='AbortError') return false;
      setAiStatus('刷新最新消息失败：'+err.message);
      return false;
    })
    .finally(()=>{
      refreshInFlight=false;
      if(useButtonState){
        btn.disabled=false;
        btn.textContent=original;
      }
    });
}

function scheduleBackgroundRefresh(refreshAi=false, immediate=true){
  if(document.hidden || refreshInFlight){
    return;
  }
  if(!immediate && Date.now()-lastBackgroundRefreshAt < BACKGROUND_REFRESH_COOLDOWN_MS){
    return;
  }
  if(refreshTimer){
    clearTimeout(refreshTimer);
    refreshTimer=null;
  }
  const run=()=>refreshLatestView(true, refreshAi, false);
  if(immediate){
    run();
    return;
  }
  refreshTimer=setTimeout(run, 250);
}

function manualRefreshLatest(){
  curOrder='asc';
  curFollowLatest=true;
  syncOrderButton();
  refreshLatestView(false, true);
}

window.addEventListener('focus',()=>{
  refreshStats();
  refreshSessions();
  if(curTable){
    loadMessages();
  }
});

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
def index(
    table: str = Query(""),
    name: str = Query(""),
    start: int = Query(0),
    limit: int = Query(50),
    type_filter: str = Query(""),
    order: str = Query("asc"),
):
    stats = _stats_payload()
    sessions = _sessions_payload(2000) or []
    session_name = name or "请选择会话"
    toolbar_display = "none"
    messages_html = ""
    pagination_display = "none"
    page_info = ""
    next_disabled = ""
    if table:
        latest = bool(table and not type_filter and start == 0 and order == "asc" and limit != 0)
        payload = _messages_payload(table=table, start=start, limit=limit, type_filter=type_filter, order=order, latest=latest)
        if payload is not None:
            toolbar_display = "flex"
            messages_html = _render_messages_html(payload["messages"], table)
            pagination_display = "flex"
            total = payload["total"]
            pages = 1 if limit == 0 else max(1, (total + limit - 1) // limit)
            resolved_start = int(payload.get("resolved_start", start) or 0)
            page_info = f"全部 {total:,} 条" if limit == 0 else f"第 {resolved_start // limit + 1}/{pages} 页（共 {total:,} 条）"
            if limit != 0 and (resolved_start + limit) >= total:
                next_disabled = "disabled"
        else:
            toolbar_display = "flex"
            messages_html = '<div style="padding:20px;color:#d4380d;text-align:center">未找到该会话的聊天记录</div>'
    else:
        messages_html = ""

    content = _INDEX_HTML
    replacements = {
        "__INITIAL_STATS__": _render_stats_html(stats),
        "__INITIAL_SESSIONS__": _render_sessions_html(sessions),
        "__INITIAL_CHAT_TITLE__": html.escape(session_name),
        "__INITIAL_TOOLBAR_DISPLAY__": toolbar_display,
        "__INITIAL_MESSAGES__": messages_html,
        "__INITIAL_PAGINATION_DISPLAY__": pagination_display,
        "__INITIAL_PAGE_INFO__": html.escape(page_info),
        "__INITIAL_NEXT_DISABLED__": next_disabled,
    }
    for key, value in replacements.items():
        content = content.replace(key, value)
    return Response(
        content=content,
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"},
    )


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
